"""Shared data types for strategies, engine, and live runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class Bar:
    ts: datetime  # UTC, bar close time
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Signal:
    """Strategy output. Must define at least one exit mechanism (TP or trail).

    Exits (any combination, all checked each bar — first to trigger wins):
      - `take_profit`:        absolute price (e.g. 1.0850)
      - `take_profit_r`:      R-multiple of stop distance (engine computes price)
      - `trail_atr_mult`:     ATR(14)-based trailing stop with this multiplier
      - `trail_extreme_bars`: N-bar extreme trailing stop (Turtle-style)

    Trailing stops only ratchet in the favorable direction — they never loosen.
    Combine with `take_profit` to set a hard cap, or omit it to let winners run.
    """
    side: Side
    stop_loss: float
    take_profit: float | None = None
    take_profit_r: float | None = None
    trail_atr_mult: float | None = None
    trail_extreme_bars: int | None = None
    tag: str = ""

    def __post_init__(self) -> None:
        if self.take_profit is not None and self.take_profit_r is not None:
            raise ValueError("Signal cannot have both take_profit and take_profit_r")
        has_exit = (
            self.take_profit is not None
            or self.take_profit_r is not None
            or self.trail_atr_mult is not None
            or self.trail_extreme_bars is not None
        )
        if not has_exit:
            raise ValueError(
                "Signal must define at least one exit: take_profit, take_profit_r, "
                "trail_atr_mult, or trail_extreme_bars"
            )


@dataclass
class Position:
    side: Side
    entry_ts: datetime
    entry_price: float
    size: float                 # units (positive)
    stop_loss: float            # ratchets forward if trail config is set
    take_profit: float | None
    initial_stop_loss: float = 0.0  # frozen at entry; used by permutation tests
    trail_atr_mult: float | None = None
    trail_extreme_bars: int | None = None
    tag: str = ""

    def unrealized_pnl(self, price: float) -> float:
        diff = price - self.entry_price if self.side is Side.LONG else self.entry_price - price
        return diff * self.size

    def update_trailing_stop(self, recent_bars: list, current_atr: float | None) -> None:
        """Ratchet the stop_loss in the favorable direction. Never loosens.

        Called at bar close (so the updated stop applies on the next bar's
        high/low check — no look-ahead).
        """
        if not recent_bars:
            return
        candidate: float | None = None

        if self.trail_atr_mult is not None and current_atr is not None and current_atr > 0:
            last_close = recent_bars[-1].close
            atr_candidate = (
                last_close - self.trail_atr_mult * current_atr if self.side is Side.LONG
                else last_close + self.trail_atr_mult * current_atr
            )
            candidate = atr_candidate

        if self.trail_extreme_bars is not None and len(recent_bars) >= self.trail_extreme_bars:
            window = recent_bars[-self.trail_extreme_bars:]
            extreme_candidate = (
                min(b.low for b in window) if self.side is Side.LONG
                else max(b.high for b in window)
            )
            # If both styles are active, take the tighter (less favorable to us / safer).
            if candidate is None:
                candidate = extreme_candidate
            else:
                candidate = (
                    max(candidate, extreme_candidate) if self.side is Side.LONG
                    else min(candidate, extreme_candidate)
                )

        if candidate is None:
            return
        # Ratchet only — never loosen.
        if self.side is Side.LONG:
            if candidate > self.stop_loss:
                self.stop_loss = candidate
        else:
            if candidate < self.stop_loss:
                self.stop_loss = candidate


@dataclass
class Trade:
    side: Side
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    reason: str  # "stop" | "take_profit" | "signal_close" | "end_of_data"
    tag: str = ""
    # Captured so the permutation test can resample (SL, TP) distances.
    # `stop_loss` is the LAST stop (possibly trailed); permutation should use
    # `initial_stop_loss` to faithfully replicate the entry-time exit envelope.
    stop_loss: float = 0.0
    initial_stop_loss: float = 0.0
    take_profit: float | None = None
    trail_atr_mult: float | None = None
    trail_extreme_bars: int | None = None


@dataclass
class StrategyState:
    """What the strategy sees on each bar. Strategy can mutate `memory`."""
    position: Position | None
    recent_bars: list[Bar]  # last N bars (configurable per strategy)
    memory: dict = field(default_factory=dict)
