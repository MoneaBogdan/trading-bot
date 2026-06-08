"""Per-day P&L stability check.

Reads a trades.jsonl file and aggregates by UTC calendar day, showing:
  - trades per day
  - win rate per day
  - total payoff per day
  - rolling equity curve
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--entry-bucket", default="",
                    help="e.g. '0.30-0.70' to filter trades by entry price bucket")
    args = ap.parse_args()

    trades = [json.loads(l) for l in Path(args.trades).open()]
    if args.entry_bucket:
        lo, hi = [float(x) for x in args.entry_bucket.split("-")]
        trades = [t for t in trades if lo <= t["entry_price"] <= hi]
        print(f"[filter] entry in [{lo},{hi}] → {len(trades)} trades")

    by_day = defaultdict(list)
    for t in trades:
        day = datetime.fromtimestamp(t["signal_ts"], tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[day].append(t)

    print(f"\n{'Date':<12}  {'N':>4}  {'Win%':>5}  {'Total':>8}  {'Equity':>9}")
    equity = 0.0
    daily = []
    for day in sorted(by_day):
        rows = by_day[day]
        n = len(rows)
        wins = sum(1 for r in rows if r["correct"])
        total = sum(r["payoff_per_unit"] for r in rows)
        equity += total
        daily.append(total)
        print(f"{day}  {n:4d}  {100*wins/n:4.1f}%  {total:+8.2f}  {equity:+9.2f}")

    if not daily:
        return 0
    n_days = len(daily)
    pos_days = sum(1 for d in daily if d > 0)
    neg_days = sum(1 for d in daily if d < 0)
    best = max(daily)
    worst = min(daily)
    print(f"\n=== Summary ===")
    print(f"  days traded:   {n_days}")
    print(f"  positive days: {pos_days} ({100*pos_days/n_days:.0f}%)")
    print(f"  negative days: {neg_days} ({100*neg_days/n_days:.0f}%)")
    print(f"  best day:      {best:+.2f}")
    print(f"  worst day:     {worst:+.2f}")
    print(f"  final equity:  {equity:+.2f}")

    # Drawdown
    peak = 0.0
    max_dd = 0.0
    cum = 0.0
    for d in daily:
        cum += d
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    print(f"  max drawdown:  {max_dd:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
