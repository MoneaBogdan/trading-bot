"""Permutation significance test for entry rules.

The null hypothesis: this strategy's entry rule has no edge — random entries
with identical exit logic would do just as well.

Method (Bryan Marsh / Jesse style):
  1. From the real trades, extract the joint distribution of
     (side, stop_distance, tp_distance). Random entries sample from this so
     the *exit envelope* is held fixed.
  2. Compute the realized entry rate: real_n_entries / eligible_bars.
  3. Run N permutations. In each, a RandomEntryStrategy emits a signal at
     each eligible bar with that probability, sampling (side, sl, tp) from
     the empirical pool. Same engine, same risk manager, same gate — only
     the entry trigger is replaced.
  4. Collect each permutation's profit factor (and a few other metrics).
  5. p_value = fraction of permutations with PF >= realized PF.

If p > 0.05, the real strategy is indistinguishable from random entries with
matched exits — there's no edge in the entry rule. If p < 0.05, the entry
rule contributes information beyond what the exit envelope alone provides.

What this catches: strategies that look profitable because their *exit logic*
(R:R, ATR stops, opposite-range TPs) is doing the work, while the entry
trigger adds nothing.

What this does NOT catch:
  - Exit-logic edge that's specific to the entry context (e.g., a stop that
    only works because the strategy enters at a particular price level).
    Permutation tests the entry trigger, not the entry-exit interaction.
  - Selection bias from us building many strategies and reporting the best.
    For that you'd want a Hansen SPA test or White's reality check.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from btypes import Bar, Side, Signal, StrategyState, Trade
from engine import BacktestConfig, run_backtest
from metrics import compute
from strategy import Strategy


@dataclass
class SignificanceResult:
    realized_pf: float
    realized_return_pct: float
    realized_sharpe: float
    realized_n_trades: int

    n_permutations: int
    pf_distribution: list[float]
    return_distribution: list[float]

    p_value_pf: float              # P(random PF >= realized PF)
    p_value_return: float          # P(random return >= realized return)
    pf_percentile: float           # where realized sits in the random distribution
    n_random_zero_trades: int      # permutations where the random strategy fired nothing


class RandomEntryStrategy(Strategy):
    """Permutation strategy: same lookback as base, no filters, random entries
    with the SAME exit envelope (initial SL distance, TP distance, trail config)
    as the real strategy."""

    name = "random_entry_perm"

    def __init__(self, base_lookback: int, samples: list[dict], p_entry: float, seed: int):
        # Each sample dict has: side, sl_dist, tp_dist (or None),
        # trail_atr_mult (or None), trail_extreme_bars (or None).
        self.lookback = base_lookback
        self.min_adx = None
        self.samples = samples
        self.p_entry = p_entry
        self.rng = np.random.default_rng(seed)

    def on_bar(self, bar: Bar, state: StrategyState) -> Signal | None:
        if state.position is not None:
            return None
        if len(state.recent_bars) < self.lookback:
            return None
        if self.rng.random() > self.p_entry:
            return None
        if not self.samples:
            return None

        sample = self.samples[int(self.rng.integers(0, len(self.samples)))]
        side = sample["side"]
        sl_dist = sample["sl_dist"]
        tp_dist = sample["tp_dist"]

        if side is Side.LONG:
            stop_loss = bar.close - sl_dist
            take_profit = (bar.close + tp_dist) if tp_dist is not None else None
        else:
            stop_loss = bar.close + sl_dist
            take_profit = (bar.close - tp_dist) if tp_dist is not None else None

        if stop_loss <= 0 or (take_profit is not None and take_profit <= 0):
            return None
        if side is Side.LONG and stop_loss >= bar.close:
            return None
        if side is Side.SHORT and stop_loss <= bar.close:
            return None

        return Signal(
            side, stop_loss=stop_loss, take_profit=take_profit,
            trail_atr_mult=sample.get("trail_atr_mult"),
            trail_extreme_bars=sample.get("trail_extreme_bars"),
            tag="rand",
        )


def _extract_samples(trades: list[Trade]) -> list[dict]:
    """Build the resampling pool.

    Uses INITIAL stop (entry-time) and the trail config so random entries
    inherit the full exit envelope — including trailing behavior. This way a
    Donchian permutation tests "did the breakout signal beat random entries
    with the same N/2 initial stop and 20-bar trailing exit?".
    """
    out: list[dict] = []
    for t in trades:
        # Prefer initial_stop_loss; fall back to stop_loss for legacy non-trail trades.
        sl_ref = t.initial_stop_loss if t.initial_stop_loss > 0 else t.stop_loss
        sl_dist = abs(t.entry_price - sl_ref) if sl_ref > 0 else None
        if sl_dist is None or sl_dist <= 0:
            continue
        tp_dist = abs(t.entry_price - t.take_profit) if t.take_profit else None
        out.append({
            "side": t.side,
            "sl_dist": sl_dist,
            "tp_dist": tp_dist,
            "trail_atr_mult": t.trail_atr_mult,
            "trail_extreme_bars": t.trail_extreme_bars,
        })
    return out


def permutation_test(
    bars_df: pd.DataFrame,
    real_trades: list[Trade],
    base_strategy_lookback: int,
    config: BacktestConfig,
    gate=None,
    n_permutations: int = 1000,
    seed: int = 42,
) -> SignificanceResult:
    """Run N permutations and compute p-values for PF and return."""

    samples = _extract_samples(real_trades)
    n_eligible_bars = max(len(bars_df) - base_strategy_lookback, 1)
    p_entry = min(len(real_trades) / n_eligible_bars, 1.0)

    # Realized metrics from the real trade list.
    # We recompute by running an "identity" backtest is overkill — just compute from trades.
    realized_pnl = sum(t.pnl for t in real_trades)
    wins = sum(t.pnl for t in real_trades if t.pnl > 0)
    losses = abs(sum(t.pnl for t in real_trades if t.pnl <= 0))
    realized_pf = (wins / losses) if losses > 0 else float("inf") if wins > 0 else 0.0
    realized_return_pct = realized_pnl / config.starting_equity * 100

    # Run permutations.
    rng_seed_seq = np.random.SeedSequence(seed)
    pf_dist: list[float] = []
    ret_dist: list[float] = []
    zero_trade_perms = 0

    for i, child_seed in enumerate(rng_seed_seq.generate_state(n_permutations)):
        strat = RandomEntryStrategy(
            base_lookback=base_strategy_lookback,
            samples=samples,
            p_entry=p_entry,
            seed=int(child_seed),
        )
        result = run_backtest(bars_df, strat, config, gate=gate)
        m = compute(result)
        if m.n_trades == 0:
            zero_trade_perms += 1
            pf_dist.append(0.0)
            ret_dist.append(0.0)
            continue
        pf_dist.append(m.profit_factor)
        ret_dist.append(m.return_pct)

    pf_arr = np.asarray(pf_dist)
    ret_arr = np.asarray(ret_dist)
    p_pf = float((pf_arr >= realized_pf).mean())
    p_ret = float((ret_arr >= realized_return_pct).mean())
    pf_pctile = float((pf_arr <= realized_pf).mean() * 100)

    # Sharpe is harder to compute without re-running the engine on real bars;
    # we omit it from the resampled distribution and just report the realized value.
    realized_sharpe = float("nan")

    return SignificanceResult(
        realized_pf=realized_pf,
        realized_return_pct=realized_return_pct,
        realized_sharpe=realized_sharpe,
        realized_n_trades=len(real_trades),
        n_permutations=n_permutations,
        pf_distribution=pf_dist,
        return_distribution=ret_dist,
        p_value_pf=p_pf,
        p_value_return=p_ret,
        pf_percentile=pf_pctile,
        n_random_zero_trades=zero_trade_perms,
    )


def format_significance(r: SignificanceResult) -> str:
    pf_arr = np.asarray(r.pf_distribution)
    ret_arr = np.asarray(r.return_distribution)
    if pf_arr.size == 0:
        return "(no permutation data)"
    pf_q = np.percentile(pf_arr, [5, 25, 50, 75, 95])
    ret_q = np.percentile(ret_arr, [5, 25, 50, 75, 95])
    verdict = "EDGE LIKELY" if r.p_value_pf < 0.05 else "NO EDGE DETECTED"

    return (
        f"Permutation test  n={r.n_permutations}  trades={r.realized_n_trades}\n"
        f"  Random PF:      p5={pf_q[0]:.2f}  p25={pf_q[1]:.2f}  p50={pf_q[2]:.2f}  p75={pf_q[3]:.2f}  p95={pf_q[4]:.2f}\n"
        f"  Random return%: p5={ret_q[0]:+.2f}  p25={ret_q[1]:+.2f}  p50={ret_q[2]:+.2f}  p75={ret_q[3]:+.2f}  p95={ret_q[4]:+.2f}\n"
        f"  Realized:       PF={r.realized_pf:.2f}  return={r.realized_return_pct:+.2f}%  "
        f"({r.pf_percentile:.1f}th pctile of random)\n"
        f"  p-value (PF):       {r.p_value_pf:.4f}\n"
        f"  p-value (return):   {r.p_value_return:.4f}\n"
        f"  zero-trade perms:   {r.n_random_zero_trades}/{r.n_permutations}\n"
        f"  → {verdict} (p < 0.05 means realized PF beats >95% of random-entry runs)"
    )
