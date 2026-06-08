"""Permutation significance test.

Replaces the direction signal (follow-the-move) with a random coin flip
while keeping ALL other constraints identical (same signal times, same
markets selected, same fill mechanics). If random direction also wins
~80%, our "follow BTC" rule contributes no information — the edge is
elsewhere (e.g. base rate skew, market-selection bias).

Method: load the real trades file. For each trade, with prob 0.5 flip
the side, recompute correct & payoff using the same winner. Repeat N
times, build the distribution of mean payoff under random direction.
Compare to the real mean.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--n-perms", type=int, default=2000)
    ap.add_argument("--entry-bucket", default="")
    args = ap.parse_args()

    trades = [json.loads(l) for l in Path(args.trades).open()]
    if args.entry_bucket:
        lo, hi = [float(x) for x in args.entry_bucket.split("-")]
        trades = [t for t in trades if lo <= t["entry_price"] <= hi]
        print(f"[filter] entry in [{lo},{hi}] → {len(trades)} trades")

    if not trades:
        print("no trades")
        return 1

    # Real metric
    real_mean = mean(t["payoff_per_unit"] for t in trades)
    real_winrate = sum(1 for t in trades if t["correct"]) / len(trades)
    print(f"\n[real]   n={len(trades)}  win%={100*real_winrate:.1f}  "
          f"avg_payoff={real_mean:+.4f}")

    # Random-side baseline: keep entry price, but flip side randomly
    # Outcome under flip: if we'd flipped, then "correct" means new_side == winner.
    # We don't know winner directly per trade but we can infer it:
    #   if t["correct"] and t["side"] == "UP" → winner=UP
    #   if t["correct"] and t["side"] == "DOWN" → winner=DOWN
    #   if not t["correct"] and t["side"] == "UP" → winner=DOWN
    #   ...etc
    winners = []
    for t in trades:
        if t["correct"]:
            winners.append(t["side"])
        else:
            winners.append("DOWN" if t["side"] == "UP" else "UP")

    rng = random.Random(42)
    means = []
    winrates = []
    for _ in range(args.n_perms):
        s = 0.0
        wins = 0
        for t, w in zip(trades, winners):
            side = "UP" if rng.random() < 0.5 else "DOWN"
            correct = (side == w)
            entry = t["entry_price"]
            payoff = (1.0 - entry) if correct else -entry
            s += payoff
            if correct:
                wins += 1
        means.append(s / len(trades))
        winrates.append(wins / len(trades))

    means.sort()
    pct = sum(1 for m in means if m >= real_mean) / len(means)
    print(f"\n[random] {args.n_perms} permutations, avg payoff distribution:")
    print(f"  mean:    {mean(means):+.4f}")
    print(f"  p5:      {means[int(0.05*len(means))]:+.4f}")
    print(f"  median:  {means[len(means)//2]:+.4f}")
    print(f"  p95:     {means[int(0.95*len(means))]:+.4f}")
    print(f"  max:     {means[-1]:+.4f}")
    print(f"\n  P(random ≥ real) = {pct:.4f}")
    print(f"  real win%: {100*real_winrate:.1f}   random win% mean: {100*mean(winrates):.1f}")

    if pct < 0.01:
        print(f"\n  ✓ Real strategy beats random with p < 0.01 — direction signal is meaningful")
    elif pct < 0.05:
        print(f"\n  ~ Real strategy beats random with p < 0.05")
    else:
        print(f"\n  ✗ Real strategy NOT distinguishable from random direction (p={pct:.3f})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
