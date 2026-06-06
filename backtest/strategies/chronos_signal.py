"""Chronos foundation-model directional signal.

Hypothesis (from the 5-min scalping video): general-purpose LLMs are wrong
for price prediction, but time-series foundation models like Chronos —
trained on millions of diverse time series — actually do forecast.

We feed Chronos the last N closes, ask it for the next-bar median forecast,
and trade only when:
  1. The forecast deviates from the current close by at least `min_edge_pct`.
  2. ADX is above 20 (skip ranging markets).

Stop: 1.5 * ATR(14).  TP: 2R.

The Chronos pipeline is loaded lazily on first use and reused across calls.
Inference cost on Apple Silicon (chronos-bolt-tiny) is ~6ms/bar; over a
6-year H1 backtest with ADX gating that's roughly 1-2 minutes total.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from btypes import Bar, Side, Signal, StrategyState
from indicators import atr, bars_to_df
from strategy import Strategy, register

CONTEXT_BARS = 96               # input length for Chronos
ATR_STOP_MULT = 1.5
MODEL_NAME = "amazon/chronos-bolt-tiny"


class ChronosSignal(Strategy):
    name = "chronos_signal"
    lookback = max(CONTEXT_BARS + 5, 60)
    min_adx = 20.0

    # Tunable parameter — forecast must deviate at least this % from
    # current price to fire a signal. Higher = fewer, higher-conviction trades.
    min_edge_pct: float = 0.05

    @classmethod
    def param_grid(cls) -> dict[str, list]:
        return {"min_edge_pct": [0.05, 0.10, 0.15, 0.20, 0.30]}

    def __init__(self) -> None:
        self._pipe = None  # lazy

    def _ensure_pipe(self):
        if self._pipe is not None:
            return self._pipe
        import torch
        from chronos import BaseChronosPipeline
        # Prefer Apple Silicon GPU, fall back to CPU. CUDA path also works if present.
        device = "mps" if torch.backends.mps.is_available() else (
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        self._pipe = BaseChronosPipeline.from_pretrained(
            MODEL_NAME, device_map=device, dtype=torch.float32,
        )
        return self._pipe

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        if state.position is not None:
            return None
        if len(state.recent_bars) < self.lookback:
            return None
        if not self.passes_filters(state):
            return None

        import torch

        df = bars_to_df(state.recent_bars[-self.lookback:])
        ctx = torch.tensor(df["close"].iloc[-CONTEXT_BARS:].to_numpy(), dtype=torch.float32)
        pipe = self._ensure_pipe()
        _q, mean = pipe.predict_quantiles(
            inputs=ctx, prediction_length=1, quantile_levels=[0.5],
        )
        pred = float(mean[0, 0].item())
        current = float(ctx[-1].item())
        edge_pct = (pred - current) / current * 100

        atr_now = atr(df["high"], df["low"], df["close"]).iloc[-1]
        if atr_now is None or atr_now != atr_now or atr_now <= 0:
            return None

        # 2x ATR initial stop, then TRAIL with 2x ATR — let winners run.
        if edge_pct >= self.min_edge_pct:
            stop = bar.close - ATR_STOP_MULT * atr_now
            if stop >= bar.close:
                return None
            return Signal(
                Side.LONG, stop_loss=stop,
                trail_atr_mult=ATR_STOP_MULT,
                tag="chronos_long",
            )
        if edge_pct <= -self.min_edge_pct:
            stop = bar.close + ATR_STOP_MULT * atr_now
            if stop <= bar.close:
                return None
            return Signal(
                Side.SHORT, stop_loss=stop,
                trail_atr_mult=ATR_STOP_MULT,
                tag="chronos_short",
            )
        return None


@register
def _factory() -> ChronosSignal:
    return ChronosSignal()
