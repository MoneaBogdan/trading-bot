"""CLI entry point.

Examples:
    python run.py --list
    python run.py --strategy london_orb --pair EURUSD --granularity M5 --start 2024-01-01 --end 2024-06-01
    python run.py --strategy donchian_55_20 --pair GBPUSD --granularity H1 --start 2023-01-01 --end 2024-01-01 --gate
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

import strategies  # noqa: F401 — registers strategies
from data import load_candles
from engine import BacktestConfig, run_backtest
from gate import RegimeGate, StaticGate
from metrics import compute, format_report
from strategy import available, get


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="List registered strategies and exit")
    p.add_argument("--strategy", help="Strategy name (see --list)")
    p.add_argument("--pair", default="EURUSD",
                   help="Single instrument, or 'PAIR_A,PAIR_B' for cointegration spread (e.g. EURUSD,GBPUSD)")
    p.add_argument("--source", default="dukascopy", choices=["yahoo", "oanda", "dukascopy", "binance"],
                   help="Candle source. dukascopy = deep FX history. yahoo = quick FX/stocks. "
                        "oanda needs a token. binance = crypto (no auth).")
    p.add_argument("--granularity", default="H4",
                   help="Granularity: M1, M5, M15, M30, H1, H4 (default), D. "
                        "Higher granularity amortizes spread cost across bigger moves.")
    p.add_argument("--start", help="ISO date, e.g. 2024-01-01")
    p.add_argument("--end", help="ISO date, e.g. 2024-06-01")
    p.add_argument("--spread-pips", type=float, default=1.0)
    p.add_argument("--jpy", action="store_true", help="Use 0.01 pip value (JPY pairs)")
    p.add_argument("--cost-pct", type=float, default=None,
                   help="Use percentage cost instead of pip-based (for crypto). 0.001 = 0.1% per side. "
                        "Binance spot taker fee is 0.1%%.")
    p.add_argument("--starting-equity", type=float, default=100_000)
    p.add_argument("--risk-pct", type=float, default=0.5, help="Risk per trade (% of equity)")
    p.add_argument("--max-open-risk-pct", type=float, default=2.0, help="Cap on sum of open-trade risk")
    p.add_argument("--daily-loss-pct", type=float, default=3.0, help="Daily loss limit (% of equity)")
    p.add_argument("--kelly", action="store_true", help="Use Kelly criterion for sizing (capped at --risk-pct)")
    p.add_argument("--kelly-fraction", type=float, default=0.25, help="Fraction of full Kelly to use (0.25 = quarter Kelly)")
    p.add_argument("--gate", action="store_true", help="Apply LLM regime gate from regime-classifier cache")
    p.add_argument("--markov", action="store_true",
                   help="Apply Markov regime sizing (3-state, P(bull)-P(bear) drives size mult)")
    p.add_argument("--markov-cum-bars", type=int, default=20,
                   help="N-bar cumulative-return window for Markov state labelling")
    p.add_argument("--markov-min-conf", type=float, default=0.05,
                   help="Minimum direction-aware confidence to allow a trade")
    p.add_argument("--trades-csv", help="Optional path to write trades CSV")
    p.add_argument("--walk-forward", action="store_true",
                   help="Run walk-forward analysis instead of a single backtest")
    p.add_argument("--wf-tune", action="store_true",
                   help="True walk-forward: tune params on train, evaluate on test")
    p.add_argument("--wf-window-days", type=int, default=365,
                   help="Stability mode: window length. Tune mode: train length.")
    p.add_argument("--wf-step-days", type=int, default=90,
                   help="Stability mode: slide step. Tune mode: defaults to --wf-test-days.")
    p.add_argument("--wf-test-days", type=int, default=90,
                   help="Tune mode only: out-of-sample test window length.")
    p.add_argument("--wf-tuning-metric", default="sharpe",
                   choices=["sharpe", "sortino", "profit_factor", "return", "calmar"])
    p.add_argument("--wf-no-purge", action="store_true",
                   help="Disable purged warmup. Default ON: prepend strategy.lookback bars of prior data as warmup so first test signal has full context.")
    p.add_argument("--mc", type=int, default=0,
                   help="Run Monte Carlo trade resampling with N simulations after the backtest")
    p.add_argument("--mc-method", default="bootstrap", choices=["bootstrap", "shuffle"])
    p.add_argument("--significance", type=int, default=0,
                   help="Run permutation significance test with N random-entry permutations (e.g. 1000)")
    p.add_argument("--force-pairs", action="store_true",
                   help="Run pairs backtest even if the spread fails the ADF cointegration test")
    p.add_argument("--strategy-param", action="append", default=[],
                   metavar="KEY=VALUE",
                   help="Override a strategy parameter (repeatable). "
                        "Auto-parses int/float/bool. Example: --strategy-param min_edge_pct=0.15")
    args = p.parse_args()

    if args.list:
        print("registered strategies:")
        for n in available():
            print(f"  - {n}")
        return 0

    if not (args.strategy and args.start and args.end):
        p.error("--strategy, --start, --end are required (or use --list)")

    # Parse --strategy-param overrides once; apply to all strategy instances we create.
    overrides: dict = {}
    for kv in (args.strategy_param or []):
        if "=" not in kv:
            p.error(f"--strategy-param expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        try:
            parsed = int(v)
        except ValueError:
            try:
                parsed = float(v)
            except ValueError:
                parsed = {"true": True, "false": False, "none": None}.get(v.lower(), v)
        overrides[k.strip()] = parsed

    def make_strategy():
        s = get(args.strategy)
        if overrides:
            s.set_params(**overrides)
        return s

    strategy = make_strategy()
    if overrides:
        print(f"strategy params overridden: {overrides}")
    start = _parse_ts(args.start)
    end = _parse_ts(args.end)

    is_pair_trade = "," in args.pair
    spread_fit = None
    if is_pair_trade:
        from pairs import load_pair_spread
        pa, pb = [p.strip().upper() for p in args.pair.split(",", 1)]
        print(f"loading spread {pa} - β·{pb} {args.granularity} {start.date()} -> {end.date()} via {args.source} ...")
        bars, spread_fit = load_pair_spread(pa, pb, args.granularity, start, end, source=args.source)
        print(f"  {spread_fit.n_bars:,} aligned bars")
        print(f"  {spread_fit.summary()}")
        for w in spread_fit.warnings:
            print(f"  ⚠ {w}")
        if not spread_fit.is_cointegrated and not args.force_pairs:
            print("\nrefusing to backtest: spread is not cointegrated at the 5% level.")
            print("re-run with --force-pairs to proceed anyway (results unreliable).")
            return 2
    else:
        print(f"loading {args.pair} {args.granularity} {start.date()} -> {end.date()} via {args.source} ...")
        bars = load_candles(args.pair, args.granularity, start, end, source=args.source)
        print(f"  {len(bars):,} bars")
    if bars.empty:
        print("no bars returned; check pair/granularity/range/credentials")
        return 1

    from risk import RiskConfig
    # Pair trades cross bid/ask on BOTH legs, so spread cost is 2x a single-instrument trade.
    effective_spread_pips = args.spread_pips * (2.0 if is_pair_trade else 1.0)
    # Crypto: use cost_pct (double it for pair trades). FX: pip-based.
    effective_cost_pct = (args.cost_pct * 2.0 if args.cost_pct and is_pair_trade else args.cost_pct)
    config = BacktestConfig(
        pair=args.pair,
        spread_pips=effective_spread_pips,
        pip_value=0.01 if args.jpy else 0.0001,
        cost_pct=effective_cost_pct,
        starting_equity=args.starting_equity,
        risk=RiskConfig(
            risk_per_trade_pct=args.risk_pct,
            max_open_risk_pct=args.max_open_risk_pct,
            daily_loss_limit_pct=args.daily_loss_pct,
            kelly_enabled=args.kelly,
            kelly_fraction=args.kelly_fraction,
        ),
    )
    # Compose gates: LLM regime + Markov + always-allow base.
    base_gate = RegimeGate(enabled=True) if args.gate else StaticGate()
    if args.markov:
        from markov import MarkovClassifier
        from gate import CompositeGate, MarkovGate
        clf = MarkovClassifier(cum_return_bars=args.markov_cum_bars)
        clf.fit(bars["close"])
        markov_gate = MarkovGate(clf, bars["ts"], min_confidence=args.markov_min_conf)
        gate = CompositeGate([base_gate, markov_gate]) if args.gate else markov_gate
        print(f"Markov gate active: window={args.markov_cum_bars} bars, min_conf={args.markov_min_conf}")
    else:
        gate = base_gate

    if args.walk_forward:
        from walk_forward import format_walk_forward, walk_forward, walk_forward_tuned
        from strategy import get as _get
        purged = not args.wf_no_purge
        purge_label = "purged" if purged else "unpurged"
        if args.wf_tune:
            grid = _get(args.strategy).param_grid()
            if not grid:
                print(f"strategy {args.strategy!r} has no tunable params; falling back to stability mode")
            else:
                combos = 1
                for v in grid.values():
                    combos *= len(v)
                print(f"\nwalk-forward (tuned, {purge_label}): train={args.wf_window_days}d test={args.wf_test_days}d  "
                      f"strategy={strategy.name}  pair={args.pair}  metric={args.wf_tuning_metric}  "
                      f"grid={combos} combinations\n")
            rows = walk_forward_tuned(
                bars,
                make_strategy,
                config,
                train_days=args.wf_window_days,
                test_days=args.wf_test_days,
                step_days=args.wf_step_days if args.wf_step_days != 90 else None,
                gate=gate,
                tuning_metric=args.wf_tuning_metric,
                purged=purged,
            )
            print(format_walk_forward(rows, show_params=bool(grid)))
        else:
            rows = walk_forward(
                bars,
                make_strategy,
                config,
                window_days=args.wf_window_days,
                step_days=args.wf_step_days,
                gate=gate,
                purged=purged,
            )
            print(f"\nwalk-forward (stability, {purge_label}): window={args.wf_window_days}d step={args.wf_step_days}d  "
                  f"strategy={strategy.name}  pair={args.pair}\n")
            print(format_walk_forward(rows))
        return 0

    result = run_backtest(bars, strategy, config, gate=gate)

    print(f"\nstrategy: {strategy.name}  pair: {args.pair}  gate: {'on' if args.gate else 'off'}")
    print(f"risk: {args.risk_pct}%/trade, max-open {args.max_open_risk_pct}%, daily-cap {args.daily_loss_pct}%\n")
    print(format_report(compute(result)))
    if result.block_reasons:
        print("\nblocked breakdown:")
        for reason, n in sorted(result.block_reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason}: {n}")

    if args.mc > 0 and result.trades:
        from monte_carlo import format_mc, simulate
        mc = simulate(result.trades, config.starting_equity,
                      method=args.mc_method, n_simulations=args.mc)
        print()
        print(format_mc(mc))

    if args.significance > 0 and result.trades:
        from significance import format_significance, permutation_test
        print(f"\nrunning permutation test ({args.significance} permutations) ...")
        sig = permutation_test(
            bars_df=bars,
            real_trades=result.trades,
            base_strategy_lookback=strategy.lookback,
            config=config,
            gate=gate,
            n_permutations=args.significance,
        )
        print()
        print(format_significance(sig))

    if args.trades_csv and result.trades:
        import pandas as pd
        rows = [t.__dict__.copy() for t in result.trades]
        if spread_fit is not None:
            for r in rows:
                leg_a, leg_b = spread_fit.leg_sizes(r["size"])
                r["leg_a_pair"] = spread_fit.pair_a
                r["leg_a_size"] = leg_a
                r["leg_b_pair"] = spread_fit.pair_b
                r["leg_b_size"] = leg_b
                r["hedge_ratio"] = spread_fit.hedge_ratio
        pd.DataFrame(rows).to_csv(args.trades_csv, index=False)
        print(f"trades written to {args.trades_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
