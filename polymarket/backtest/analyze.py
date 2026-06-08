"""Aggregate metrics over trades.jsonl from replay.py.

Reports:
  - Overall: win rate, avg payoff, total payoff (= ROI if 1 unit per trade)
  - Per entry-price bucket (the contrarian-bucket hypothesis)
  - Per side (UP vs DOWN)
  - Per seconds-to-close bucket
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean


def load_trades(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open()]


def summarize(rows: list[dict], label: str) -> None:
    if not rows:
        print(f"  {label:<24}  n=0")
        return
    n = len(rows)
    wins = sum(1 for r in rows if r["correct"])
    total = sum(r["payoff_per_unit"] for r in rows)
    avg = total / n
    avg_entry = mean(r["entry_price"] for r in rows)
    print(f"  {label:<24}  n={n:4d}  win%={100*wins/n:5.1f}  avg={avg:+.3f}  "
          f"total={total:+7.2f}  avg_entry={avg_entry:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="cache/trades.jsonl")
    args = ap.parse_args()

    path = Path(__file__).parent / args.trades
    trades = load_trades(path)
    if not trades:
        print("no trades")
        return 1

    print(f"\n=== Overall ({len(trades)} trades) ===")
    summarize(trades, "all")

    print("\n=== By side ===")
    for side in ("UP", "DOWN"):
        summarize([t for t in trades if t["side"] == side], side)

    print("\n=== By entry-price bucket ===")
    buckets = [
        ("0.01-0.30 (contrarian)", lambda e: 0.01 <= e <= 0.30),
        ("0.30-0.50",              lambda e: 0.30 < e <= 0.50),
        ("0.50-0.70",              lambda e: 0.50 < e <= 0.70),
        ("0.70-0.90",              lambda e: 0.70 < e <= 0.90),
        ("0.90-0.95",              lambda e: 0.90 < e <= 0.95),
        ("0.95-1.00 (chase)",      lambda e: 0.95 < e <= 1.00),
    ]
    for label, pred in buckets:
        summarize([t for t in trades if pred(t["entry_price"])], label)

    print("\n=== By seconds-to-close ===")
    for lo, hi in [(0, 60), (60, 120), (120, 180), (180, 240), (240, 300)]:
        rows = [t for t in trades if lo <= t["seconds_to_close"] < hi]
        summarize(rows, f"{lo}-{hi}s")

    print("\n=== By BTC return magnitude ===")
    for lo, hi in [(0.05, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.50), (0.50, 5.0)]:
        rows = [t for t in trades if lo <= abs(t["btc_60s_ret_pct"]) < hi]
        summarize(rows, f"|ret|={lo:.2f}-{hi:.2f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
