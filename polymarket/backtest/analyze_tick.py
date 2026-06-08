"""Analyze tick-level replay output. Distinguishes tradeable signals from
filtered-out ones to expose any selection bias."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean


def summarize(rows, label):
    if not rows:
        print(f"  {label:<28}  n=0"); return
    n = len(rows)
    wins = sum(1 for r in rows if r["correct"])
    total = sum(r["payoff_per_unit"] for r in rows)
    avg = total / n
    print(f"  {label:<28}  n={n:5d}  win%={100*wins/n:5.1f}  avg={avg:+.3f}  total={total:+8.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="cache/tick_replay.jsonl")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.trades).open()]
    print(f"=== {len(rows)} total signal records ===\n")

    tradeable = [r for r in rows if r.get("skip_reason") is None]
    no_tape = [r for r in rows if r.get("skip_reason") == "no_fill_in_tape"]
    outside_sweet = [r for r in rows if r.get("skip_reason") and r["skip_reason"].startswith("outside_sweet")]

    print(f"=== Breakdown by skip-reason ===")
    print(f"  tradeable (sweet spot + fill):  {len(tradeable):5d}")
    print(f"  no fillable BUY in trade tape:  {len(no_tape):5d}")
    print(f"  ask outside sweet spot:         {len(outside_sweet):5d}")
    print()

    print("=== Tradeable signals (what live would have filled) ===")
    summarize(tradeable, "all tradeable")

    print()
    print("=== By entry-price bucket (tradeable only) ===")
    for lo, hi in [(0.30,0.40),(0.40,0.50),(0.50,0.60),(0.60,0.70)]:
        rows_b = [r for r in tradeable if lo <= r["entry_price"] < hi]
        summarize(rows_b, f"{lo:.2f}-{hi:.2f}")

    print()
    print("=== If we'd traded ALL sweet-spot signals (drop no-fill filter) ===")
    print("  (For no-tape rows we don't know the entry price, so this is a partial view)")

    # Counterfactual: among signals that LANDED in the sweet spot ask range
    # but had no fill print, what would they have looked like?
    # Without entry price we can't compute payoff. But we can check how many
    # OUTSIDE_SWEET rows were JUST outside (might have been in-bucket at fill time).
    # For now, just report tradeable winrate (live should align with this).

    if tradeable:
        n = len(tradeable)
        wins = sum(1 for r in tradeable if r["correct"])
        total = sum(r["payoff_per_unit"] for r in tradeable)
        print(f"\n=== HEADLINE: tradeable win rate {100*wins/n:.1f}%  avg payoff {total/n:+.3f}  total {total:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
