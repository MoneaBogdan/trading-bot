"""Bollinger band breakout confirmed by volume.

Hypothesis: closes outside the Bollinger band on a volume spike indicate
genuine institutional participation, not noise. Pure BB breakouts whipsaw
in low-volume conditions.

For forex via Dukascopy, "volume" is tick count — a proxy for activity.

Rules:
  - LONG  when close > upper BB(20, 2σ) and volume > 1.5 * 20-bar avg volume.
  - SHORT when close < lower BB(20, 2σ) and volume > 1.5 * 20-bar avg volume.
  - Stop: BB middle (20-period SMA) — if the breakout fails, mean-revert exit.
  - TP: 2R fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from btypes import Bar, Side, Signal, StrategyState
from indicators import bars_to_df, bollinger
from strategy import Strategy, register

class BbVolumeBreakout(Strategy):
    name = "bb_volume_breakout"
    lookback = 60
    min_adx = 20.0

    # Tunable parameters.
    bb_period: int = 20
    bb_std_mult: float = 2.0
    volume_lookback: int = 20
    volume_spike_mult: float = 1.5
    take_profit_r: float = 2.0

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {
            "bb_period": [15, 20, 25],
            "bb_std_mult": [1.5, 2.0, 2.5],
            "volume_spike_mult": [1.25, 1.5, 2.0],
            "take_profit_r": [1.5, 2.0, 3.0],
        }

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        if state.position is not None:
            return None
        if len(state.recent_bars) < self.lookback:
            return None
        if not self.passes_filters(state):
            return None

        df = bars_to_df(state.recent_bars)
        upper, mid, lower = bollinger(df["close"], self.bb_period, self.bb_std_mult)
        u, m, l = upper.iloc[-1], mid.iloc[-1], lower.iloc[-1]
        if any(_isnan(x) for x in (u, m, l)):
            return None

        avg_vol = df["volume"].iloc[-self.volume_lookback:].mean()
        if avg_vol <= 0:
            return None
        if bar.volume <= self.volume_spike_mult * avg_vol:
            return None

        if bar.close > u and m < bar.close:
            return Signal(Side.LONG, stop_loss=m, take_profit_r=self.take_profit_r, tag="bb_long")
        if bar.close < l and m > bar.close:
            return Signal(Side.SHORT, stop_loss=m, take_profit_r=self.take_profit_r, tag="bb_short")
        return None


def _isnan(x) -> bool:
    return x is None or (isinstance(x, float) and x != x)


@register
def _factory() -> BbVolumeBreakout:
    return BbVolumeBreakout()
