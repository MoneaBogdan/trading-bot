"""London Open Range Breakout.

Define an opening range from 00:00 to 07:00 UTC each day. Enter long on a
close above the range high, short on a close below the range low, during the
07:00-10:00 UTC window. Stop at the opposite range edge. One trade per day.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from btypes import Bar, Side, Signal, StrategyState
from strategy import Strategy, register

RANGE_END_HOUR = 7   # range closes at 07:00 UTC
ENTRY_END_HOUR = 10  # no new entries after 10:00 UTC


class LondonORB(Strategy):
    name = "london_orb"
    lookback = 600  # ~10 hours of M1 bars

    def on_start(self, state: StrategyState) -> None:
        state.memory["range"] = {"date": None, "high": None, "low": None, "traded": False}

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        r = state.memory["range"]
        bar_date = bar.ts.date()

        # New day: reset range tracking.
        if r["date"] != bar_date:
            r["date"] = bar_date
            r["high"] = None
            r["low"] = None
            r["traded"] = False

        hour = bar.ts.hour

        # Building the range (00:00-07:00 UTC).
        if hour < RANGE_END_HOUR:
            r["high"] = bar.high if r["high"] is None else max(r["high"], bar.high)
            r["low"] = bar.low if r["low"] is None else min(r["low"], bar.low)
            return None

        # Don't trade if we never built a range or already traded today.
        if r["high"] is None or r["traded"] or state.position is not None:
            return None

        # Outside the entry window — no new entries.
        if hour >= ENTRY_END_HOUR:
            return None

        # Entry: bar closes outside the range. SL is opposite range edge, TP is 1.5R.
        if bar.close > r["high"]:
            r["traded"] = True
            return Signal(side=Side.LONG, stop_loss=r["low"], take_profit_r=1.5, tag="orb_long")
        if bar.close < r["low"]:
            r["traded"] = True
            return Signal(side=Side.SHORT, stop_loss=r["high"], take_profit_r=1.5, tag="orb_short")
        return None


@register
def _factory() -> LondonORB:
    return LondonORB()
