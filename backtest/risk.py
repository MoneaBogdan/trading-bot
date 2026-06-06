"""Risk manager: computes position size and enforces account-level limits.

Strategies declare *what* to trade (side + stop + TP). The risk manager
decides *how much* and whether the trade is allowed given current account state.

Conventions (forex spot, retail):
  - Risk per trade = (entry_price - stop_price) * size  (in account currency,
    assuming the quote currency == account currency; for cross-quote pairs
    you'd need an FX conversion — out of scope for v1).
  - "Open risk" = sum of |entry - stop| * size across all currently open positions.
  - "Daily P&L" resets at 00:00 UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class RejectReason(str, Enum):
    OK = "ok"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    MAX_OPEN_RISK = "max_open_risk"
    ZERO_STOP_DISTANCE = "zero_stop_distance"
    REGIME_BLOCKED = "regime_blocked"
    INSUFFICIENT_EQUITY = "insufficient_equity"
    KELLY_NEGATIVE_EDGE = "kelly_negative_edge"


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.5          # % of equity risked on any single trade
    max_open_risk_pct: float = 2.0           # cap on sum of open-trade risks
    daily_loss_limit_pct: float = 3.0        # stop trading after this much intraday loss
    min_equity: float = 1_000.0              # refuse to trade below this

    # Kelly mode. When enabled, risk_per_trade_pct is treated as a CAP, and the
    # actual risk is derived from rolling trade history:
    #   f* = (W*R - (1-W)) / R   where W=win rate, R=avg_win/|avg_loss|
    # Then we apply a fraction (default 0.25 = "quarter-Kelly") for safety.
    kelly_enabled: bool = False
    kelly_fraction: float = 0.25
    kelly_warmup_trades: int = 30            # need at least N closed trades before Kelly kicks in
    kelly_window: int = 200                  # rolling window of recent trades to compute W and R


@dataclass
class SizingDecision:
    allowed: bool
    size: float
    risk_amount: float
    reason: RejectReason


class RiskManager:
    def __init__(self, config: RiskConfig | None = None):
        self.cfg = config or RiskConfig()
        self._daily_pnl: dict[date, float] = {}
        self._open_risk: float = 0.0

    # --- public API -----------------------------------------------------------

    def evaluate(
        self,
        equity: float,
        now: datetime,
        entry_price: float,
        stop_price: float,
        regime_size_mult: float = 1.0,
        trades_so_far: list | None = None,
    ) -> SizingDecision:
        """Decide whether to take the trade and at what size."""
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return SizingDecision(False, 0.0, 0.0, RejectReason.ZERO_STOP_DISTANCE)
        if equity < self.cfg.min_equity:
            return SizingDecision(False, 0.0, 0.0, RejectReason.INSUFFICIENT_EQUITY)
        if regime_size_mult <= 0:
            return SizingDecision(False, 0.0, 0.0, RejectReason.REGIME_BLOCKED)

        today_pnl = self._daily_pnl.get(now.date(), 0.0)
        daily_loss_cap = -equity * (self.cfg.daily_loss_limit_pct / 100)
        if today_pnl <= daily_loss_cap:
            return SizingDecision(False, 0.0, 0.0, RejectReason.DAILY_LOSS_LIMIT)

        risk_pct = self._effective_risk_pct(trades_so_far or [])
        if risk_pct <= 0:
            # Kelly says current edge is negative — skip the trade entirely
            # rather than opening a zero-size position that pollutes metrics.
            return SizingDecision(False, 0.0, 0.0, RejectReason.KELLY_NEGATIVE_EDGE)
        risk_amount = equity * (risk_pct / 100) * regime_size_mult
        size = risk_amount / stop_distance
        if size <= 0:
            return SizingDecision(False, 0.0, 0.0, RejectReason.ZERO_STOP_DISTANCE)

        # Cap total open risk across all positions.
        if self._open_risk + risk_amount > equity * (self.cfg.max_open_risk_pct / 100):
            return SizingDecision(False, 0.0, 0.0, RejectReason.MAX_OPEN_RISK)

        return SizingDecision(True, size, risk_amount, RejectReason.OK)

    def on_position_opened(self, risk_amount: float) -> None:
        self._open_risk += risk_amount

    def on_position_closed(self, risk_amount: float, pnl: float, when: datetime) -> None:
        self._open_risk = max(0.0, self._open_risk - risk_amount)
        self._daily_pnl[when.date()] = self._daily_pnl.get(when.date(), 0.0) + pnl

    # --- Kelly sizing ---------------------------------------------------------

    def _effective_risk_pct(self, trades: list) -> float:
        """Return the % of equity to risk. Honors Kelly when enabled and warm."""
        cap = self.cfg.risk_per_trade_pct
        if not self.cfg.kelly_enabled or len(trades) < self.cfg.kelly_warmup_trades:
            return cap
        window = trades[-self.cfg.kelly_window:]
        wins = [t.pnl for t in window if t.pnl > 0]
        losses = [t.pnl for t in window if t.pnl <= 0]
        if not wins or not losses:
            return cap
        win_rate = len(wins) / len(window)
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))
        if avg_loss == 0:
            return cap
        b = avg_win / avg_loss
        kelly_f = (win_rate * b - (1 - win_rate)) / b
        if kelly_f <= 0:
            return 0.0  # strategy currently has negative edge — sit out
        # Translate Kelly fraction-of-bankroll to "% risked per trade".
        # Kelly f* says "bet f*·bankroll" but assumes payoff = b·bet. In our
        # framework, "risk_pct" already represents the stop-loss dollar amount
        # relative to equity, so we use kelly_f directly as risk_pct, multiplied
        # by the safety fraction (typically 0.25 = quarter Kelly).
        kelly_risk_pct = kelly_f * 100 * self.cfg.kelly_fraction
        return min(kelly_risk_pct, cap)

    # --- introspection (for logging / metrics) --------------------------------

    @property
    def open_risk(self) -> float:
        return self._open_risk

    def pnl_for_day(self, d: date) -> float:
        return self._daily_pnl.get(d, 0.0)
