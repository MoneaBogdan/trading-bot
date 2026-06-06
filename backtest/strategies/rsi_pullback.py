"""RSI pullback in trend.

Hypothesis: in a strong trend (price > 200 EMA), short-term RSI dips are
buyable; in a strong downtrend, short-term RSI spikes are shortable.
Avoids fading the trend — common mistake of naive RSI strategies.

Rules:
  - LONG  when close > EMA(200) and RSI(14) crosses up through 30 from below.
  - SHORT when close < EMA(200) and RSI(14) crosses down through 70 from above.
  - Stop: most recent 10-bar swing low (long) / high (short).
  - TP: 1.5R fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from btypes import Bar, Side, Signal, StrategyState
from indicators import bars_to_df, ema, rsi
from strategy import Strategy, register

class RsiPullback(Strategy):
    name = "rsi_pullback"
    lookback = 260
    min_adx = 20.0

    # Tunable parameters (instance attrs so set_params() can override them).
    rsi_period: int = 14
    swing_lookback: int = 10
    oversold: float = 30.0
    overbought: float = 70.0
    take_profit_r: float = 1.5

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {
            "rsi_period": [10, 14, 18],
            "oversold": [25.0, 30.0, 35.0],
            "overbought": [65.0, 70.0, 75.0],
            "take_profit_r": [1.0, 1.5, 2.0],
        }

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        if state.position is not None:
            return None
        if len(state.recent_bars) < self.lookback:
            return None
        if not self.passes_filters(state):
            return None

        df = bars_to_df(state.recent_bars)
        ema200 = ema(df["close"], 200).iloc[-1]
        rsi_series = rsi(df["close"], self.rsi_period)
        rsi_now = rsi_series.iloc[-1]
        rsi_prev = rsi_series.iloc[-2]

        if pd_isnan(ema200) or pd_isnan(rsi_now) or pd_isnan(rsi_prev):
            return None

        # Long setup: uptrend + RSI crossing up from oversold.
        if bar.close > ema200 and rsi_prev < self.oversold <= rsi_now:
            swing_low = min(b.low for b in state.recent_bars[-self.swing_lookback:])
            if swing_low >= bar.close:
                return None
            return Signal(Side.LONG, stop_loss=swing_low, take_profit_r=self.take_profit_r, tag="rsi_long")

        # Short setup: downtrend + RSI crossing down from overbought.
        if bar.close < ema200 and rsi_prev > self.overbought >= rsi_now:
            swing_high = max(b.high for b in state.recent_bars[-self.swing_lookback:])
            if swing_high <= bar.close:
                return None
            return Signal(Side.SHORT, stop_loss=swing_high, take_profit_r=self.take_profit_r, tag="rsi_short")

        return None


def pd_isnan(x) -> bool:
    return x is None or (isinstance(x, float) and x != x)  # NaN check


@register
def _factory() -> RsiPullback:
    return RsiPullback()
