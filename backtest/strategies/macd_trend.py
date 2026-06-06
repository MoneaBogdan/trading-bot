"""MACD trend trigger with EMA filter.

Hypothesis: MACD signal-line crossovers are noisy on their own. Filtering by
the 50EMA / 200EMA relationship keeps us on the right side of the larger trend.

Rules:
  - LONG  when EMA(50) > EMA(200) and MACD histogram crosses up through zero.
  - SHORT when EMA(50) < EMA(200) and MACD histogram crosses down through zero.
  - Stop: 2 * ATR(14) below entry (above for shorts).
  - TP: 2R fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from btypes import Bar, Side, Signal, StrategyState
from indicators import atr, bars_to_df, ema, macd
from strategy import Strategy, register

LOOKBACK = 260


class MacdTrend(Strategy):
    name = "macd_trend"
    lookback = LOOKBACK
    min_adx = 20.0

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        if state.position is not None:
            return None
        if len(state.recent_bars) < LOOKBACK:
            return None
        if not self.passes_filters(state):
            return None

        df = bars_to_df(state.recent_bars)
        ema50 = ema(df["close"], 50).iloc[-1]
        ema200 = ema(df["close"], 200).iloc[-1]
        _, _, hist = macd(df["close"])
        h_now, h_prev = hist.iloc[-1], hist.iloc[-2]
        atr_now = atr(df["high"], df["low"], df["close"]).iloc[-1]

        if any(_isnan(x) for x in (ema50, ema200, h_now, h_prev, atr_now)):
            return None

        bull_regime = ema50 > ema200
        cross_up = h_prev < 0 <= h_now
        cross_down = h_prev > 0 >= h_now

        # Trend-following: trail at 2x ATR instead of capping at 2R.
        if bull_regime and cross_up:
            stop = bar.close - 2 * atr_now
            if stop >= bar.close:
                return None
            return Signal(Side.LONG, stop_loss=stop, trail_atr_mult=2.0, tag="macd_long")
        if not bull_regime and cross_down:
            stop = bar.close + 2 * atr_now
            if stop <= bar.close:
                return None
            return Signal(Side.SHORT, stop_loss=stop, trail_atr_mult=2.0, tag="macd_short")
        return None


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and x != x)


@register
def _factory() -> MacdTrend:
    return MacdTrend()
