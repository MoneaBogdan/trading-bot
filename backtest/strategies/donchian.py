"""Donchian channel breakout (simplified Turtle).

Long on a break above the N-bar high, short on a break below the N-bar low.
Stop at the opposite N/2-bar extreme. One position at a time.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from btypes import Bar, Side, Signal, StrategyState
from strategy import Strategy, register

ENTRY_LOOKBACK = 55
EXIT_LOOKBACK = 20


class Donchian(Strategy):
    name = "donchian_55_20"
    lookback = ENTRY_LOOKBACK + 5
    min_adx = 20.0

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        if len(state.recent_bars) < ENTRY_LOOKBACK:
            return None
        if state.position is not None:
            return None  # let stops handle exits
        if not self.passes_filters(state):
            return None

        window = state.recent_bars[-ENTRY_LOOKBACK:]
        high_n = max(b.high for b in window[:-1])  # exclude current bar
        low_n = min(b.low for b in window[:-1])
        exit_window = state.recent_bars[-EXIT_LOOKBACK:]
        recent_low = min(b.low for b in exit_window)
        recent_high = max(b.high for b in exit_window)

        # Trend-following Turtle-style: SL at N/2 opposite extreme as the
        # initial stop, then TRAIL with the 20-bar opposite extreme. No fixed
        # TP — let winners run until they violate the 20-bar level.
        if bar.close > high_n:
            return Signal(
                side=Side.LONG,
                stop_loss=recent_low,
                trail_extreme_bars=EXIT_LOOKBACK,
                tag="donch_long",
            )
        if bar.close < low_n:
            return Signal(
                side=Side.SHORT,
                stop_loss=recent_high,
                trail_extreme_bars=EXIT_LOOKBACK,
                tag="donch_short",
            )
        return None


@register
def _factory() -> Donchian:
    return Donchian()
