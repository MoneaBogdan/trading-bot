"""Phase 1 monitor: poll HL + Binance funding, log spreads + paper-PnL.

No execution, no signing — public reads only. See README.md for strategy.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
BINANCE_PERP_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BYBIT_PERP_URL = "https://api.bybit.com/v5/market/tickers"
LOG_DIR = Path(__file__).parent / "logs"

POLL_INTERVAL_S = int(os.getenv("HL_POLL_INTERVAL_S", "30"))
ASSETS = [a.strip().upper() for a in os.getenv("HL_ASSETS", "BTC,ETH,SOL").split(",")]
OPP_BPS_8H = float(os.getenv("HL_OPPORTUNITY_BPS_8H", "5"))
PAPER_NOTIONAL = float(os.getenv("HL_PAPER_NOTIONAL_USDC", "1000"))

# Fees + slippage assumptions for net-PnL accounting.
# HL: 0.045% taker, 0.015% maker. Binance: 0.04% taker, 0.02% maker.
# Bybit: 0.055% taker, 0.02% maker. Worst-case round-trip (both legs taker)
# is ~0.18%. Assume one taker + one maker entry, same on exit = 0.12%.
ROUND_TRIP_FEE_BPS = float(os.getenv("HL_ROUND_TRIP_FEE_BPS", "12"))
# Per-leg slippage at small clip — research said ~12 bps at $500k, scales sub-linear.
# At $1k notional this is closer to 1–3 bps total.
EST_SLIPPAGE_BPS = float(os.getenv("HL_EST_SLIPPAGE_BPS", "3"))


@dataclass
class FundingSnapshot:
    asset: str
    # HL `funding` field is forward-looking (next-hour rate). Multiplied by 8
    # for an 8h projection comparable to Binance/Bybit's lastFundingRate
    # (which is backward-looking but typically persistent over short horizons).
    # The comparison is approximate; treat |spread| > break_even as a signal,
    # not a fill-able quote.
    hl_funding_8h_bps: float
    hl_mark: float
    binance_funding_8h_bps: float
    binance_mark: float
    bybit_funding_8h_bps: float | None
    bybit_mark: float | None

    def best_cross(self) -> tuple[str, float] | None:
        """Return (cex_name, spread_bps_8h) for the venue with widest |spread vs HL|."""
        candidates: list[tuple[str, float]] = [
            ("binance", self.hl_funding_8h_bps - self.binance_funding_8h_bps),
        ]
        if self.bybit_funding_8h_bps is not None:
            candidates.append(("bybit", self.hl_funding_8h_bps - self.bybit_funding_8h_bps))
        return max(candidates, key=lambda c: abs(c[1]))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"funding_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"


def _emit(event: dict) -> None:
    event = {"ts": _utc_now_iso(), **event}
    with _log_path().open("a") as f:
        f.write(json.dumps(event) + "\n")
    print(json.dumps(event), flush=True)


def fetch_hl_funding() -> dict[str, tuple[float, float]]:
    """Return {asset: (funding_8h_bps, mark_px)} for perp universe.

    HL `metaAndAssetCtxs` returns the funding rate as a per-hour decimal
    (e.g. 0.0000125 = 0.00125%/hr). Multiply by 8 and 10_000 to get bps/8h.
    """
    r = requests.post(HL_INFO_URL, json={"type": "metaAndAssetCtxs"}, timeout=10)
    r.raise_for_status()
    meta, ctxs = r.json()
    universe = meta["universe"]
    out: dict[str, tuple[float, float]] = {}
    for asset_meta, ctx in zip(universe, ctxs):
        name = asset_meta["name"].upper()
        if name not in ASSETS:
            continue
        funding_per_hour = float(ctx["funding"])
        mark = float(ctx["markPx"])
        funding_bps_8h = funding_per_hour * 8 * 10_000
        out[name] = (funding_bps_8h, mark)
    return out


def fetch_binance_funding() -> dict[str, tuple[float, float]]:
    """Return {asset: (funding_8h_bps, mark_px)} for Binance perps.

    `lastFundingRate` is the most recently SETTLED 8h rate, not a forward
    estimate. We use it as a sticky-state proxy for next-period funding.
    """
    r = requests.get(BINANCE_PERP_URL, timeout=10)
    r.raise_for_status()
    by_symbol = {row["symbol"]: row for row in r.json()}
    out: dict[str, tuple[float, float]] = {}
    for asset in ASSETS:
        row = by_symbol.get(f"{asset}USDT")
        if row is None:
            continue
        funding_bps_8h = float(row["lastFundingRate"]) * 10_000
        mark = float(row["markPrice"])
        out[asset] = (funding_bps_8h, mark)
    return out


def fetch_bybit_funding() -> dict[str, tuple[float, float]]:
    """Return {asset: (funding_8h_bps, mark_px)} for Bybit USDT-perps.

    Bybit `tickers` returns `fundingRate` as the most recent settled 8h rate.
    """
    try:
        r = requests.get(BYBIT_PERP_URL, params={"category": "linear"}, timeout=10)
        r.raise_for_status()
        rows = r.json().get("result", {}).get("list", [])
    except Exception:
        return {}
    by_symbol = {row["symbol"]: row for row in rows}
    out: dict[str, tuple[float, float]] = {}
    for asset in ASSETS:
        row = by_symbol.get(f"{asset}USDT")
        if row is None:
            continue
        try:
            funding_bps_8h = float(row["fundingRate"]) * 10_000
            mark = float(row["markPrice"])
        except (KeyError, ValueError):
            continue
        out[asset] = (funding_bps_8h, mark)
    return out


def paper_pnl_net(spread_bps_8h: float, notional: float) -> tuple[float, float, float]:
    """Return (gross_pnl_8h, est_costs_8h, net_pnl_8h) on a delta-neutral pair.

    Costs are amortized over a single 8h funding cycle — fees + slippage hit
    once at entry and again at exit, so they're spread across however many
    cycles you hold. This conservatively books all of them against one cycle.
    """
    gross = notional * abs(spread_bps_8h) / 10_000
    costs = notional * (ROUND_TRIP_FEE_BPS + 2 * EST_SLIPPAGE_BPS) / 10_000
    return gross, costs, gross - costs


def poll_once() -> list[FundingSnapshot]:
    hl = fetch_hl_funding()
    bn = fetch_binance_funding()
    by = fetch_bybit_funding()
    snaps: list[FundingSnapshot] = []
    for asset in ASSETS:
        if asset not in hl or asset not in bn:
            continue
        snaps.append(FundingSnapshot(
            asset=asset,
            hl_funding_8h_bps=hl[asset][0],
            hl_mark=hl[asset][1],
            binance_funding_8h_bps=bn[asset][0],
            binance_mark=bn[asset][1],
            bybit_funding_8h_bps=by[asset][0] if asset in by else None,
            bybit_mark=by[asset][1] if asset in by else None,
        ))
    return snaps


def main() -> None:
    _emit({
        "event": "boot",
        "assets": ASSETS,
        "poll_interval_s": POLL_INTERVAL_S,
        "opportunity_bps_8h": OPP_BPS_8H,
        "paper_notional_usdc": PAPER_NOTIONAL,
    })

    running = True

    def _stop(_sig, _frm):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        try:
            snaps = poll_once()
        except Exception as exc:
            _emit({"event": "poll_error", "err": repr(exc)})
            time.sleep(POLL_INTERVAL_S)
            continue

        for s in snaps:
            best = s.best_cross()
            assert best is not None
            cex_name, spread_bps_8h = best
            _emit({
                "event": "snapshot",
                "asset": s.asset,
                "hl_funding_8h_bps": round(s.hl_funding_8h_bps, 3),
                "binance_funding_8h_bps": round(s.binance_funding_8h_bps, 3),
                "bybit_funding_8h_bps": (
                    round(s.bybit_funding_8h_bps, 3) if s.bybit_funding_8h_bps is not None else None
                ),
                "best_cex": cex_name,
                "best_spread_bps_8h": round(spread_bps_8h, 3),
                "hl_mark": s.hl_mark,
                "binance_mark": s.binance_mark,
                "bybit_mark": s.bybit_mark,
                "basis_pct_vs_binance": round((s.hl_mark - s.binance_mark) / s.binance_mark * 100, 4),
            })
            if abs(spread_bps_8h) >= OPP_BPS_8H:
                long_venue = cex_name if spread_bps_8h > 0 else "hyperliquid"
                short_venue = "hyperliquid" if spread_bps_8h > 0 else cex_name
                gross, costs, net = paper_pnl_net(spread_bps_8h, PAPER_NOTIONAL)
                _emit({
                    "event": "opportunity",
                    "asset": s.asset,
                    "long_perp": long_venue,
                    "short_perp": short_venue,
                    "spread_bps_8h": round(spread_bps_8h, 3),
                    "gross_pnl_8h_usdc": round(gross, 4),
                    "est_costs_8h_usdc": round(costs, 4),
                    "net_pnl_8h_usdc": round(net, 4),
                    "annualized_apr_pct_net": round(net / PAPER_NOTIONAL * 3 * 365 * 100, 2),
                })

        for _ in range(POLL_INTERVAL_S):
            if not running:
                break
            time.sleep(1)

    _emit({"event": "shutdown"})


if __name__ == "__main__":
    sys.exit(main())
