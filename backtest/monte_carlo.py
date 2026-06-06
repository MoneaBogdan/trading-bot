"""Monte Carlo trade resampling.

Two methods, both useful for different questions:

  - shuffle:   keep the same trades, reorder them randomly. Tells you whether
               a particular max-drawdown is driven by sequence (a string of
               losses early on) vs. magnitude. If shuffled DDs are much smaller
               than realized DD, you got unlucky; if similar, the strategy
               itself produces deep drawdowns.

  - bootstrap: sample with replacement from the trade pool. Tells you the
               range of outcomes "if I'd traded a slightly different sample
               of the same kind of trades." Wider distribution → more luck
               in the realized result; tighter → more skill.

Both produce percentile bands on return, max DD, and Sharpe. The realized
backtest result is one draw from the bootstrap distribution; we report
where it falls on the curve.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from btypes import Trade


@dataclass
class MCResult:
    method: str
    n_simulations: int
    returns_pct: dict        # {p5, p25, p50, p75, p95}
    max_dd_pct: dict
    realized_return_pct: float
    realized_dd_pct: float
    realized_return_percentile: float    # where the realized return falls in the distribution


def _equity_path(pnls: np.ndarray, starting_equity: float) -> np.ndarray:
    return starting_equity + np.cumsum(pnls)


def _max_drawdown_pct(equity: np.ndarray) -> float:
    running_max = np.maximum.accumulate(equity)
    dd = (equity - running_max) / running_max
    return float(dd.min() * 100)


def simulate(
    trades: list[Trade],
    starting_equity: float,
    method: str = "bootstrap",
    n_simulations: int = 10_000,
    seed: int | None = 42,
) -> MCResult:
    if not trades:
        return MCResult(method, 0, {}, {}, 0.0, 0.0, 0.0)

    rng = np.random.default_rng(seed)
    pnls = np.array([t.pnl for t in trades], dtype=float)

    if method == "shuffle":
        sampler = lambda: rng.permutation(pnls)
    elif method == "bootstrap":
        sampler = lambda: rng.choice(pnls, size=len(pnls), replace=True)
    else:
        raise ValueError("method must be 'shuffle' or 'bootstrap'")

    returns_pct: list[float] = []
    max_dds: list[float] = []
    for _ in range(n_simulations):
        sample = sampler()
        eq = _equity_path(sample, starting_equity)
        returns_pct.append((eq[-1] / starting_equity - 1.0) * 100)
        max_dds.append(_max_drawdown_pct(eq))

    realized_eq = _equity_path(pnls, starting_equity)
    realized_return = (realized_eq[-1] / starting_equity - 1.0) * 100
    realized_dd = _max_drawdown_pct(realized_eq)
    realized_pct = float((np.asarray(returns_pct) <= realized_return).mean() * 100)

    return MCResult(
        method=method,
        n_simulations=n_simulations,
        returns_pct={
            "p5": float(np.percentile(returns_pct, 5)),
            "p25": float(np.percentile(returns_pct, 25)),
            "p50": float(np.percentile(returns_pct, 50)),
            "p75": float(np.percentile(returns_pct, 75)),
            "p95": float(np.percentile(returns_pct, 95)),
        },
        max_dd_pct={
            "p5": float(np.percentile(max_dds, 5)),    # most severe (most negative)
            "p25": float(np.percentile(max_dds, 25)),
            "p50": float(np.percentile(max_dds, 50)),
            "p75": float(np.percentile(max_dds, 75)),
            "p95": float(np.percentile(max_dds, 95)),  # mildest
        },
        realized_return_pct=realized_return,
        realized_dd_pct=realized_dd,
        realized_return_percentile=realized_pct,
    )


def format_mc(result: MCResult) -> str:
    if result.n_simulations == 0:
        return "(no trades to resample)"
    r = result.returns_pct
    d = result.max_dd_pct
    return (
        f"Monte Carlo ({result.method}, n={result.n_simulations})\n"
        f"  Return %:   p5={r['p5']:+.2f}  p25={r['p25']:+.2f}  p50={r['p50']:+.2f}  p75={r['p75']:+.2f}  p95={r['p95']:+.2f}\n"
        f"  Max DD %:   p5={d['p5']:.2f}   p25={d['p25']:.2f}   p50={d['p50']:.2f}   p75={d['p75']:.2f}   p95={d['p95']:.2f}\n"
        f"  Realized:   return={result.realized_return_pct:+.2f}%  DD={result.realized_dd_pct:.2f}%  "
        f"(realized return is at the {result.realized_return_percentile:.0f}th percentile)"
    )
