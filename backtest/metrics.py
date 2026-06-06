"""Standard backtest metrics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine import BacktestResult


@dataclass
class Metrics:
    n_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    total_pnl: float
    return_pct: float
    max_drawdown_pct: float
    sharpe_annual: float
    sortino_annual: float
    calmar: float
    exposure_pct: float       # share of bars with an open position
    blocked_signals: int


def compute(result: BacktestResult, bars_per_year: int = 252 * 24 * 60) -> Metrics:
    """`bars_per_year` defaults to M1 forex (24x5 minutes; we approximate)."""
    trades = result.trades
    equity = result.equity_curve

    if not trades or equity.empty:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, result.blocked_signals)

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in trades)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    eq = equity["equity"].to_numpy(dtype=float)
    starting = float(eq[0]) if eq.size else result.config.starting_equity
    return_pct = (eq[-1] / starting - 1.0) * 100 if eq.size else 0.0

    running_max = np.maximum.accumulate(eq)
    drawdowns = (eq - running_max) / running_max
    max_dd_pct = float(drawdowns.min()) * 100

    rets = pd.Series(eq).pct_change().dropna().to_numpy()
    if rets.size > 1 and rets.std() > 0:
        sharpe = float(np.sqrt(bars_per_year) * rets.mean() / rets.std())
    else:
        sharpe = 0.0
    downside = rets[rets < 0]
    if downside.size > 1 and downside.std() > 0:
        sortino = float(np.sqrt(bars_per_year) * rets.mean() / downside.std())
    else:
        sortino = 0.0
    calmar = (return_pct / abs(max_dd_pct)) if max_dd_pct < 0 else 0.0

    # Exposure: fraction of TIME with an open trade (not fraction of bars).
    in_trade_seconds = sum(max((t.exit_ts - t.entry_ts).total_seconds(), 0.0) for t in trades)
    total_seconds = (equity["ts"].iloc[-1] - equity["ts"].iloc[0]).total_seconds() or 1.0
    exposure_pct = min((in_trade_seconds / total_seconds) * 100, 100.0)

    return Metrics(
        n_trades=len(trades),
        win_rate=len(wins) / len(trades) * 100,
        avg_win=float(np.mean(wins)) if wins else 0.0,
        avg_loss=float(np.mean(losses)) if losses else 0.0,
        profit_factor=profit_factor,
        total_pnl=total_pnl,
        return_pct=return_pct,
        max_drawdown_pct=max_dd_pct,
        sharpe_annual=sharpe,
        sortino_annual=sortino,
        calmar=calmar,
        exposure_pct=exposure_pct,
        blocked_signals=result.blocked_signals,
    )


def format_report(m: Metrics) -> str:
    return (
        f"trades:           {m.n_trades}\n"
        f"win rate:         {m.win_rate:.1f}%\n"
        f"avg win / loss:   {m.avg_win:+.2f} / {m.avg_loss:+.2f}\n"
        f"profit factor:    {m.profit_factor:.2f}\n"
        f"total P&L:        {m.total_pnl:+.2f}\n"
        f"return:           {m.return_pct:+.2f}%\n"
        f"max drawdown:     {m.max_drawdown_pct:.2f}%\n"
        f"Sharpe (ann.):    {m.sharpe_annual:.2f}\n"
        f"Sortino (ann.):   {m.sortino_annual:.2f}\n"
        f"Calmar:           {m.calmar:.2f}\n"
        f"exposure:         {m.exposure_pct:.1f}%\n"
        f"blocked by gate:  {m.blocked_signals}\n"
    )
