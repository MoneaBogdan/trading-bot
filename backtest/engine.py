"""Event-driven backtest engine.

Execution model:
  - Signals fire on bar close. Every signal MUST have SL and TP.
  - Entries fill at the NEXT bar's open (no look-ahead).
  - SL and TP are checked against each bar's high/low. If both could fire in
    the same bar, we conservatively assume SL hits first.
  - Position size = risk_manager.evaluate(equity, entry, stop, regime_mult).
  - Spread cost applied as a per-trade pip charge on entry and exit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from btypes import Bar, Position, Side, Signal, StrategyState, Trade
from gate import GateDecision, StaticGate
from risk import RejectReason, RiskConfig, RiskManager
from strategy import Strategy


class _Gate(Protocol):
    def decide(self, pair: str, now) -> GateDecision: ...


@dataclass
class BacktestConfig:
    pair: str
    spread_pips: float = 1.0
    pip_value: float = 0.0001     # 0.01 for JPY pairs
    cost_pct: float | None = None  # if set, overrides pip-based cost (use for crypto)
    starting_equity: float = 100_000
    risk: RiskConfig = field(default_factory=RiskConfig)

    def cost_per_unit(self, price: float) -> float:
        """Round-trip-equivalent cost in price units, applied as ±half on each side.

        For FX:    spread_pips * pip_value  (constant)
        For crypto: price * cost_pct        (proportional to notional)
        """
        if self.cost_pct is not None:
            return abs(price) * self.cost_pct
        return self.spread_pips * self.pip_value


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade]
    equity_curve: pd.DataFrame
    blocked_signals: int
    block_reasons: dict[str, int] = field(default_factory=dict)


def _df_to_bars(df: pd.DataFrame) -> list[Bar]:
    return [
        Bar(
            ts=row.ts.to_pydatetime(),
            open=row.open, high=row.high, low=row.low, close=row.close,
            volume=int(row.volume),
        )
        for row in df.itertuples(index=False)
    ]


def _resolve_tp(signal: Signal, entry_price: float) -> float | None:
    """Convert R-multiple TP into an absolute price if needed. Returns None
    when the signal uses only trailing exits (no fixed TP)."""
    if signal.take_profit is not None:
        return signal.take_profit
    if signal.take_profit_r is None:
        return None
    stop_distance = abs(entry_price - signal.stop_loss)
    if signal.side is Side.LONG:
        return entry_price + signal.take_profit_r * stop_distance
    return entry_price - signal.take_profit_r * stop_distance


def _atr_at(recent_bars: list, period: int = 14) -> float | None:
    """Streaming-ish ATR computed from a list of Bar objects. Used by the
    engine to feed the trailing-stop updater. Period defaults to 14."""
    if len(recent_bars) < period + 1:
        return None
    trs = []
    prev_close = recent_bars[-period - 1].close
    for b in recent_bars[-period:]:
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
        prev_close = b.close
    return sum(trs) / len(trs) if trs else None


def run_backtest(
    bars_df: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig,
    gate: _Gate | None = None,
    warmup_bars: int = 0,
) -> BacktestResult:
    """Run the backtest.

    `warmup_bars`: the first N bars are "warmup" — state.recent_bars and
    strategy memory build up via on_bar, but signals are discarded, no
    positions open, and equity points are not recorded. This lets walk-forward
    pre-feed the strategy with prior context so the first real test bar has
    full indicator history (purged walk-forward).
    """
    bars = _df_to_bars(bars_df)
    if not bars:
        return BacktestResult(config, [], pd.DataFrame(columns=["ts", "equity"]), 0)
    gate = gate or StaticGate()
    risk = RiskManager(config.risk)
    warmup_bars = max(0, min(warmup_bars, len(bars)))

    state = StrategyState(position=None, recent_bars=[], memory={})
    strategy.on_start(state)

    trades: list[Trade] = []
    equity = config.starting_equity
    realized = 0.0
    equity_points: list[tuple] = []
    pending_entry: Signal | None = None  # signal queued at prev bar's close
    blocked = 0
    block_reasons: dict[str, int] = {}
    open_trade_risk: float = 0.0  # tracked separately so we can return it to risk on close

    for i, bar in enumerate(bars):
        in_warmup = i < warmup_bars
        # Per-bar cost: constant for FX (pip-based), proportional to price for crypto.
        spread_cost = config.cost_per_unit(bar.close)

        # 1. Resolve any pending entry — fill at this bar's open.
        if pending_entry is not None and state.position is None and not in_warmup:
            sig = pending_entry
            fill = bar.open + (spread_cost / 2 if sig.side is Side.LONG else -spread_cost / 2)
            decision = gate.decide(config.pair, bar.ts, sig.side)
            current_equity = config.starting_equity + realized
            if not decision.trade_allowed:
                blocked += 1
                block_reasons[RejectReason.REGIME_BLOCKED.value] = block_reasons.get(RejectReason.REGIME_BLOCKED.value, 0) + 1
            else:
                sizing = risk.evaluate(
                    equity=current_equity,
                    now=bar.ts,
                    entry_price=fill,
                    stop_price=sig.stop_loss,
                    regime_size_mult=decision.size_mult,
                    trades_so_far=trades,
                )
                if not sizing.allowed:
                    blocked += 1
                    block_reasons[sizing.reason.value] = block_reasons.get(sizing.reason.value, 0) + 1
                else:
                    tp_price = _resolve_tp(sig, fill)
                    state.position = Position(
                        side=sig.side,
                        entry_ts=bar.ts,
                        entry_price=fill,
                        size=sizing.size,
                        stop_loss=sig.stop_loss,
                        initial_stop_loss=sig.stop_loss,
                        take_profit=tp_price,
                        trail_atr_mult=sig.trail_atr_mult,
                        trail_extreme_bars=sig.trail_extreme_bars,
                        tag=sig.tag,
                    )
                    open_trade_risk = sizing.risk_amount
                    risk.on_position_opened(sizing.risk_amount)
            pending_entry = None

        # 2. Check SL/TP against this bar's high/low.
        if state.position is not None:
            pos = state.position
            exit_reason: str | None = None
            exit_price: float | None = None

            if pos.side is Side.LONG:
                # Conservative: if bar straddles both, assume stop fires first.
                if bar.low <= pos.stop_loss:
                    exit_price = pos.stop_loss - spread_cost / 2
                    exit_reason = "stop"
                elif pos.take_profit is not None and bar.high >= pos.take_profit:
                    exit_price = pos.take_profit - spread_cost / 2
                    exit_reason = "take_profit"
            else:  # SHORT
                if bar.high >= pos.stop_loss:
                    exit_price = pos.stop_loss + spread_cost / 2
                    exit_reason = "stop"
                elif pos.take_profit is not None and bar.low <= pos.take_profit:
                    exit_price = pos.take_profit + spread_cost / 2
                    exit_reason = "take_profit"

            if exit_reason is not None:
                pnl = pos.unrealized_pnl(exit_price)
                trades.append(Trade(
                    side=pos.side,
                    entry_ts=pos.entry_ts, exit_ts=bar.ts,
                    entry_price=pos.entry_price, exit_price=exit_price,
                    size=pos.size, pnl=pnl, reason=exit_reason, tag=pos.tag,
                    stop_loss=pos.stop_loss, initial_stop_loss=pos.initial_stop_loss,
                    take_profit=pos.take_profit,
                    trail_atr_mult=pos.trail_atr_mult, trail_extreme_bars=pos.trail_extreme_bars,
                ))
                realized += pnl
                risk.on_position_closed(open_trade_risk, pnl, bar.ts)
                state.position = None
                open_trade_risk = 0.0

        # 3. Equity curve — skip warmup bars so metrics measure the real test period only.
        unreal = state.position.unrealized_pnl(bar.close) if state.position else 0.0
        equity = config.starting_equity + realized + unreal
        if not in_warmup:
            equity_points.append((bar.ts, equity))

        # 4. Update recent_bars.
        state.recent_bars.append(bar)
        if len(state.recent_bars) > strategy.lookback:
            state.recent_bars = state.recent_bars[-strategy.lookback:]

        # 5. Ratchet the trailing stop on the open position (no look-ahead — uses
        #    only bars closed at or before this point). Applies on next bar.
        if state.position is not None and (
            state.position.trail_atr_mult is not None
            or state.position.trail_extreme_bars is not None
        ):
            atr_now = _atr_at(state.recent_bars, 14) if state.position.trail_atr_mult else None
            state.position.update_trailing_stop(state.recent_bars, atr_now)

        # 6. Request strategy signal for this bar's close.
        signal = strategy.on_bar(bar, state)
        # During warmup, the strategy is allowed to run (so memory and indicator
        # state evolve), but any signals it generates are discarded. The first
        # signal that can queue is at the last warmup bar (so it fills at the
        # first real bar) — but for strict no-leakage we discard those too.
        if signal is not None and state.position is None and pending_entry is None and not in_warmup:
            pending_entry = signal

    # End-of-data flush.
    if state.position is not None:
        last = bars[-1]
        pos = state.position
        pnl = pos.unrealized_pnl(last.close)
        trades.append(Trade(
            side=pos.side,
            entry_ts=pos.entry_ts, exit_ts=last.ts,
            entry_price=pos.entry_price, exit_price=last.close,
            size=pos.size, pnl=pnl, reason="end_of_data", tag=pos.tag,
            stop_loss=pos.stop_loss, initial_stop_loss=pos.initial_stop_loss,
            take_profit=pos.take_profit,
            trail_atr_mult=pos.trail_atr_mult, trail_extreme_bars=pos.trail_extreme_bars,
        ))
        risk.on_position_closed(open_trade_risk, pnl, last.ts)

    equity_df = pd.DataFrame(equity_points, columns=["ts", "equity"])
    return BacktestResult(config, trades, equity_df, blocked, block_reasons)
