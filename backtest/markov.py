"""Markov regime sizing.

Methodology (from Lewis Jackson's video):
  1. Label each bar by its N-day cumulative return → 3 states (bull/neutral/bear)
     using terciles of the historical return distribution.
  2. Build a 3×3 transition matrix from the labelled history.
  3. At each bar, given the current state, look up the row in the matrix:
       P(next bar = bull) and P(next bar = bear).
  4. Sizing signal: confidence = P(bull) − P(bear)   ∈ [-1, +1]
       - For a LONG trade, size_mult = max(0, confidence).
       - For a SHORT trade, size_mult = max(0, -confidence).
     (A bullish regime kills shorts and vice versa.)

To prevent look-ahead, the transition matrix is fit ONLY on bars strictly
before the bar being scored. We use a rolling window (default 1000 bars,
≈ half a year of H4) so the matrix can adapt to regime change.

This composes orthogonally with the LLM regime classifier and with the
risk manager — Markov gives a continuous direction-aware multiplier from
price-only inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

import numpy as np
import pandas as pd


class State(IntEnum):
    BEAR = 0
    NEUTRAL = 1
    BULL = 2


@dataclass
class MarkovSignal:
    p_bull: float
    p_bear: float
    p_neutral: float
    confidence: float          # p_bull - p_bear
    current_state: int


def _classify(cum_returns: np.ndarray, low_q: float, high_q: float) -> np.ndarray:
    """Map cumulative-return values to BEAR/NEUTRAL/BULL by precomputed quantiles."""
    out = np.full(cum_returns.shape, State.NEUTRAL, dtype=np.int8)
    out[cum_returns <= low_q] = State.BEAR
    out[cum_returns >= high_q] = State.BULL
    return out


def _transition_matrix(states: np.ndarray) -> np.ndarray:
    """Build a row-stochastic 3×3 matrix from a sequence of state labels."""
    mat = np.full((3, 3), 1.0)  # Laplace smoothing: start each cell at 1
    for prev, nxt in zip(states[:-1], states[1:]):
        mat[prev, nxt] += 1
    row_sums = mat.sum(axis=1, keepdims=True)
    return mat / row_sums


class MarkovClassifier:
    """Pre-fit on a full price series; supports rolling lookup at any timestamp.

    To avoid recomputing the entire transition matrix per bar (slow), we
    refit only every `refit_every_bars` bars. Between refits we just re-look-up
    the current state. With refit_every=20 on H4 data that's a few minutes
    of wall-clock per full backtest.
    """

    def __init__(
        self,
        cum_return_bars: int = 20,
        fit_lookback_bars: int = 1000,
        refit_every_bars: int = 20,
    ):
        self.cum_return_bars = cum_return_bars
        self.fit_lookback_bars = fit_lookback_bars
        self.refit_every_bars = refit_every_bars
        self._closes: pd.Series | None = None  # full close series, used for state classification
        self._states: np.ndarray | None = None  # cached per-index state labels (size = len(closes))

    def fit(self, closes: pd.Series) -> None:
        """Precompute the state series so we can do fast lookups at runtime."""
        c = closes.to_numpy(dtype=float)
        # Compute cumulative N-bar return: ret_t = (close_t / close_{t-N}) - 1
        cum_ret = np.full_like(c, np.nan, dtype=float)
        if len(c) > self.cum_return_bars:
            cum_ret[self.cum_return_bars:] = c[self.cum_return_bars:] / c[:-self.cum_return_bars] - 1
        self._cum_returns = cum_ret
        self._closes = closes.reset_index(drop=True)
        # Pre-compute STATIC state classification from the full series (used as a
        # cache; rolling lookups overwrite locally to avoid look-ahead).
        valid = cum_ret[~np.isnan(cum_ret)]
        if len(valid) < 30:
            # Not enough data — everyone gets NEUTRAL.
            self._static_states = np.full(len(c), State.NEUTRAL, dtype=np.int8)
            return
        low_q = float(np.quantile(valid, 1.0 / 3.0))
        high_q = float(np.quantile(valid, 2.0 / 3.0))
        states = _classify(np.nan_to_num(cum_ret, nan=0.0), low_q, high_q)
        states[: self.cum_return_bars] = State.NEUTRAL  # invalid early bars
        self._static_states = states

    def query(self, idx: int) -> MarkovSignal:
        """Return the Markov signal at bar `idx`, fit ONLY on bars [idx-lookback, idx)."""
        if self._closes is None or self._static_states is None:
            return MarkovSignal(1 / 3, 1 / 3, 1 / 3, 0.0, int(State.NEUTRAL))
        # Need at least `cum_return_bars + 50` of history for a reasonable matrix.
        min_needed = self.cum_return_bars + 50
        start = max(0, idx - self.fit_lookback_bars)
        history_cum = self._cum_returns[start:idx]
        valid = history_cum[~np.isnan(history_cum)]
        if len(valid) < min_needed:
            return MarkovSignal(1 / 3, 1 / 3, 1 / 3, 0.0, int(State.NEUTRAL))

        # Re-classify the lookback window using ONLY past-data quantiles (avoids look-ahead).
        low_q = float(np.quantile(valid, 1.0 / 3.0))
        high_q = float(np.quantile(valid, 2.0 / 3.0))
        history_states = _classify(np.nan_to_num(history_cum, nan=0.0), low_q, high_q)
        history_states[: self.cum_return_bars] = State.NEUTRAL

        mat = _transition_matrix(history_states)
        current_state = int(history_states[-1])
        row = mat[current_state]
        p_bear, p_neutral, p_bull = float(row[State.BEAR]), float(row[State.NEUTRAL]), float(row[State.BULL])
        return MarkovSignal(
            p_bull=p_bull,
            p_bear=p_bear,
            p_neutral=p_neutral,
            confidence=p_bull - p_bear,
            current_state=current_state,
        )
