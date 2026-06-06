"""Cointegration spread mean-reversion.

Reads the synthetic spread "price" series from pairs.load_pair_spread.
Computes rolling z-score of the spread. Trades the reversion:

  - LONG spread (buy A, sell B) when z < -ENTRY_Z
  - SHORT spread (sell A, buy B) when z > +ENTRY_Z
  - Exit when |z| < EXIT_Z (handled via take-profit at the mean)
  - Stop at +/- STOP_Z standard deviations to cap blowups

Position sizing flows through the same RiskManager — the spread's stop
distance becomes the unit of risk. To translate to actual lot sizes on
the two legs, divide the spread "size" by 1 for leg A and by β for leg B.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from btypes import Bar, Side, Signal, StrategyState
from indicators import bars_to_df
from strategy import Strategy, register

ROLL = 60         # rolling window for z-score mean/std
ENTRY_Z = 2.0
EXIT_Z = 0.0      # exit at mean (TP at z=0)
STOP_Z = 4.0      # bail if spread blows out further (regime change)


class CointMeanRev(Strategy):
    name = "coint_mean_rev"
    lookback = ROLL + 5
    # No ADX filter — mean reversion specifically needs ranging conditions,
    # opposite of what ADX gates for. Cointegration itself is the filter.
    min_adx = None

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        if state.position is not None:
            return None
        if len(state.recent_bars) < self.lookback:
            return None

        df = bars_to_df(state.recent_bars[-self.lookback:])
        window = df["close"].iloc[-ROLL:]
        mean = window.mean()
        std = window.std()
        if std == 0 or std != std:
            return None
        z = (bar.close - mean) / std

        # Long the spread when extremely below mean.
        if z <= -ENTRY_Z:
            stop_price = mean - STOP_Z * std       # if spread keeps falling, bail
            tp_price = mean + EXIT_Z * std          # take profit at the mean
            if stop_price >= bar.close or tp_price <= bar.close:
                return None
            return Signal(Side.LONG, stop_loss=stop_price, take_profit=tp_price, tag="coint_long")

        # Short the spread when extremely above mean.
        if z >= ENTRY_Z:
            stop_price = mean + STOP_Z * std
            tp_price = mean + EXIT_Z * std
            if stop_price <= bar.close or tp_price >= bar.close:
                return None
            return Signal(Side.SHORT, stop_loss=stop_price, take_profit=tp_price, tag="coint_short")

        return None


@register
def _factory() -> CointMeanRev:
    return CointMeanRev()
