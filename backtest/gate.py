"""Regime gate: applies block + size multiplier from the regime classifier.

In live trading, reads regime-classifier/regime_cache.json (current regime).
In backtests, you typically want a *historical* regime log keyed by date;
that's out of scope for v1, so the backtest path supports an `enabled=False`
mode (no gating) and a static override.
"""

from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from btypes import Side


@dataclass
class GateDecision:
    trade_allowed: bool
    size_mult: float       # 0.0 - 1.5
    stop_mult: float       # 0.5 - 3.0 (we ignore in v1; pass-through)
    reason: str


class RegimeGate:
    """Reads the regime-classifier cache and decides on size/block."""

    def __init__(self, cache_path: str | None = None, enabled: bool = True):
        self.enabled = enabled
        self.cache_path = Path(cache_path or os.environ.get("REGIME_CACHE_PATH", ""))

    def decide(self, pair: str, now: datetime, side: Side = Side.LONG) -> GateDecision:
        if not self.enabled:
            return GateDecision(True, 1.0, 1.0, "gate disabled")
        if not self.cache_path.exists():
            return GateDecision(False, 0.5, 1.5, "no regime cache; conservative default")
        try:
            payload = json.loads(self.cache_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return GateDecision(False, 0.5, 1.5, f"cache read error: {exc}")

        valid_until = datetime.fromisoformat(payload["valid_until"].replace("Z", "+00:00"))
        if now > valid_until:
            return GateDecision(False, 0.5, 1.5, "regime stale")

        pairs = payload.get("pairs", {})
        pair_norm = pair.upper().replace("/", "").replace("_", "")
        info = pairs.get(pair_norm)
        if info is None:
            return GateDecision(False, 0.5, 1.5, f"no regime entry for {pair_norm}")

        return GateDecision(
            trade_allowed=bool(info["trade_allowed"]),
            size_mult=float(info["suggested_size_mult"]),
            stop_mult=float(info["suggested_stop_mult"]),
            reason="regime",
        )


class StaticGate:
    """For backtesting: same decision for every bar. Useful for A/B comparisons."""

    def __init__(self, trade_allowed: bool = True, size_mult: float = 1.0, stop_mult: float = 1.0):
        self.decision = GateDecision(trade_allowed, size_mult, stop_mult, "static")

    def decide(self, pair: str, now: datetime, side: Side = Side.LONG) -> GateDecision:
        return self.decision


class MarkovGate:
    """Direction-aware sizing from a price-only Markov regime classifier.

    Pre-fitted on the full bars series before backtest starts. Each query
    uses ONLY past bars (look-ahead-free) — the classifier handles that.
    """

    def __init__(self, classifier, bar_timestamps: pd.Series, min_confidence: float = 0.05):
        self._clf = classifier
        # Convert to numpy int64 ns once for fast binary search.
        self._ts_ns = bar_timestamps.astype("datetime64[ns, UTC]").astype("int64").to_numpy()
        self.min_confidence = min_confidence

    def _idx_for(self, ts: datetime) -> int:
        target = pd.Timestamp(ts).tz_convert("UTC").value if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).tz_localize("UTC").value
        i = bisect.bisect_right(self._ts_ns, target) - 1
        return max(0, i)

    def decide(self, pair: str, now: datetime, side: Side = Side.LONG) -> GateDecision:
        idx = self._idx_for(now)
        sig = self._clf.query(idx)
        # Direction-aware sizing: bullish regime amplifies longs, suppresses shorts.
        if side is Side.LONG:
            size_mult = max(0.0, sig.confidence)
        else:
            size_mult = max(0.0, -sig.confidence)
        trade_allowed = size_mult >= self.min_confidence
        reason = f"markov state={sig.current_state} P(bull)={sig.p_bull:.2f} P(bear)={sig.p_bear:.2f}"
        return GateDecision(trade_allowed, size_mult, 1.0, reason)


class CompositeGate:
    """Combine multiple gates: ANDs trade_allowed, multiplies size_mult and stop_mult."""

    def __init__(self, gates: list):
        self.gates = gates

    def decide(self, pair: str, now: datetime, side: Side = Side.LONG) -> GateDecision:
        allowed = True
        size_mult = 1.0
        stop_mult = 1.0
        reasons = []
        for g in self.gates:
            d = g.decide(pair, now, side)
            allowed = allowed and d.trade_allowed
            size_mult *= d.size_mult
            stop_mult *= d.stop_mult
            reasons.append(d.reason)
        return GateDecision(allowed, size_mult, stop_mult, " | ".join(reasons))
