"""Walk-forward analysis — both modes.

`walk_forward()` is the stability variant: slide a fixed-params window through
history. Catches the "strategy decayed since 2022" failure mode.

`walk_forward_tuned()` is the true walk-forward: for each window, tune params
on the train segment by grid-searching the strategy's `param_grid()`, then
evaluate the best params on the next test segment. Catches the "I curve-fit
my params to the whole history" failure mode by ensuring test results only
ever use parameters chosen from PRIOR data.

A strategy with empty `param_grid()` degenerates to stability mode under
either entry point.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pandas as pd

from engine import BacktestConfig, run_backtest
from metrics import Metrics, compute
from strategy import Strategy


@dataclass
class WalkForwardWindow:
    start: pd.Timestamp
    end: pd.Timestamp
    metrics: Metrics
    params: dict = field(default_factory=dict)        # winning params (true WF only)
    train_metric_value: float = 0.0                    # score on train window (true WF only)


def walk_forward(
    bars_df: pd.DataFrame,
    strategy_factory,
    config: BacktestConfig,
    window_days: int = 365,
    step_days: int = 90,
    gate=None,
    purged: bool = True,
) -> list[WalkForwardWindow]:
    """Run the strategy on rolling windows. Returns one Metrics object per window.

    A fresh strategy instance is created for each window (`strategy_factory()`)
    so per-window state (e.g. London ORB's daily memory) doesn't leak across.
    """
    if bars_df.empty:
        return []

    start = bars_df["ts"].iloc[0]
    end = bars_df["ts"].iloc[-1]
    window_td = pd.Timedelta(days=window_days)
    step_td = pd.Timedelta(days=step_days)

    results: list[WalkForwardWindow] = []
    cursor = start
    while cursor + window_td <= end + step_td:
        win_end = min(cursor + window_td, end)
        # Purged: prepend the strategy's lookback worth of bars BEFORE cursor as
        # warmup so the first real test bar already has full indicator history.
        strategy = strategy_factory()
        if purged:
            warmup_start_mask = (bars_df["ts"] < cursor)
            warmup_tail = bars_df.loc[warmup_start_mask].tail(strategy.lookback)
            n_warmup = len(warmup_tail)
            mask = (bars_df["ts"] >= cursor) & (bars_df["ts"] < win_end)
            chunk = pd.concat([warmup_tail, bars_df.loc[mask]], ignore_index=True)
        else:
            n_warmup = 0
            mask = (bars_df["ts"] >= cursor) & (bars_df["ts"] < win_end)
            chunk = bars_df.loc[mask].reset_index(drop=True)
        if len(chunk) - n_warmup < 100:
            cursor = cursor + step_td
            continue
        result = run_backtest(chunk, strategy, config, gate=gate, warmup_bars=n_warmup)
        results.append(WalkForwardWindow(start=cursor, end=win_end, metrics=compute(result)))
        cursor = cursor + step_td

    return results


def _iter_grid(grid: dict[str, list]):
    """Cartesian product of param values, yielding dicts."""
    if not grid:
        return iter([{}])
    keys = list(grid.keys())
    return ({k: v for k, v in zip(keys, combo)} for combo in itertools.product(*(grid[k] for k in keys)))


def _score(metrics: Metrics, key: str) -> float:
    """Map a tuning metric name to a sortable number (bigger = better)."""
    if key == "sharpe":
        return metrics.sharpe_annual
    if key == "sortino":
        return metrics.sortino_annual
    if key == "profit_factor":
        return metrics.profit_factor if metrics.n_trades > 0 else -1.0
    if key == "return":
        return metrics.return_pct
    if key == "calmar":
        return metrics.calmar
    raise ValueError(f"unknown tuning metric {key!r}")


def walk_forward_tuned(
    bars_df: pd.DataFrame,
    strategy_factory,
    config: BacktestConfig,
    train_days: int = 365,
    test_days: int = 90,
    step_days: int | None = None,
    gate=None,
    tuning_metric: str = "sharpe",
    min_trades_to_tune: int = 10,
    purged: bool = True,
) -> list[WalkForwardWindow]:
    """True walk-forward: tune on train, evaluate on test, slide.

    Each test window only uses params chosen from PRIOR data — no look-ahead.
    `step_days` defaults to `test_days` (non-overlapping test windows, the
    standard out-of-sample setup). The concatenation of all test windows is
    a genuine out-of-sample equity curve.

    `tuning_metric` ∈ {sharpe, sortino, profit_factor, return, calmar}.
    """
    if bars_df.empty:
        return []
    step = step_days if step_days is not None else test_days

    proto = strategy_factory()
    lookback = proto.lookback
    grid = proto.param_grid()
    combos = list(_iter_grid(grid))
    # If a strategy has no tunable params, fall through to stability mode
    # so the user still gets per-window metrics.
    if not grid:
        return walk_forward(
            bars_df, strategy_factory, config,
            window_days=test_days, step_days=step, gate=gate, purged=purged,
        )

    start = bars_df["ts"].iloc[0]
    end = bars_df["ts"].iloc[-1]
    train_td = pd.Timedelta(days=train_days)
    test_td = pd.Timedelta(days=test_days)
    step_td = pd.Timedelta(days=step)

    results: list[WalkForwardWindow] = []
    cursor = start
    while cursor + train_td + test_td <= end + step_td:
        train_end = cursor + train_td
        test_end = min(train_end + test_td, end)
        train_mask = (bars_df["ts"] >= cursor) & (bars_df["ts"] < train_end)
        test_mask = (bars_df["ts"] >= train_end) & (bars_df["ts"] < test_end)
        train_chunk = bars_df.loc[train_mask].reset_index(drop=True)
        test_chunk = bars_df.loc[test_mask].reset_index(drop=True)
        if len(train_chunk) < 100 or len(test_chunk) < 50:
            cursor = cursor + step_td
            continue

        # Grid-search on train.
        best_score = float("-inf")
        best_params: dict = {}
        for combo in combos:
            s = strategy_factory()
            s.set_params(**combo)
            r = run_backtest(train_chunk, s, config, gate=gate)
            m = compute(r)
            if m.n_trades < min_trades_to_tune:
                continue
            score = _score(m, tuning_metric)
            if score > best_score:
                best_score = score
                best_params = combo

        if not best_params:
            # No combo had enough trades on train — fall back to defaults.
            best_params = {k: vals[len(vals) // 2] for k, vals in grid.items()}
            best_score = float("nan")

        # Evaluate on test with the winner.
        # Purged: prepend the last `lookback` bars of train as warmup, so the
        # first signal-eligible bar is the first real test bar with full
        # indicator context — same as the strategy would have live.
        s = strategy_factory()
        s.set_params(**best_params)
        if purged:
            warmup_tail = train_chunk.tail(lookback)
            n_warmup = len(warmup_tail)
            chunk_for_test = pd.concat([warmup_tail, test_chunk], ignore_index=True)
        else:
            n_warmup = 0
            chunk_for_test = test_chunk
        test_result = run_backtest(chunk_for_test, s, config, gate=gate, warmup_bars=n_warmup)
        results.append(WalkForwardWindow(
            start=train_end, end=test_end,
            metrics=compute(test_result),
            params=best_params,
            train_metric_value=best_score,
        ))
        cursor = cursor + step_td

    return results


def format_walk_forward(rows: list[WalkForwardWindow], show_params: bool = False) -> str:
    if not rows:
        return "(no windows)"
    lines = []
    header = f"{'window':<25} {'trades':>6} {'win%':>6} {'PF':>6} {'ret%':>8} {'DD%':>8} {'Sharpe':>7}"
    if show_params:
        header += "  params"
    lines.append(header)
    lines.append("-" * (70 + (40 if show_params else 0)))
    for w in rows:
        m = w.metrics
        label = f"{w.start.date()} -> {w.end.date()}"
        row = (
            f"{label:<25} {m.n_trades:>6} {m.win_rate:>5.1f} {m.profit_factor:>6.2f} "
            f"{m.return_pct:>+7.2f} {m.max_drawdown_pct:>7.2f} {m.sharpe_annual:>+6.2f}"
        )
        if show_params and w.params:
            row += "  " + ", ".join(f"{k}={v}" for k, v in w.params.items())
        lines.append(row)

    pf_values = [w.metrics.profit_factor for w in rows if w.metrics.n_trades > 0]
    ret_values = [w.metrics.return_pct for w in rows]
    pos_windows = sum(1 for r in ret_values if r > 0)
    lines.append("-" * (70 + (40 if show_params else 0)))
    lines.append(
        f"summary: {len(rows)} windows | {pos_windows}/{len(rows)} positive "
        f"| median PF {pd.Series(pf_values).median():.2f} "
        f"| median return {pd.Series(ret_values).median():+.2f}%"
    )
    return "\n".join(lines)
