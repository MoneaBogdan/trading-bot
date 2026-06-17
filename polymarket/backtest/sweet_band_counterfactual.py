"""Counterfactual PnL backtest for widened sweet-band configurations.

Reads `ask_outside_sweet_band` skip rows from the new-schema BotLogger output
(`bot=<variant>/<date>.jsonl`), looks up the eventual Polymarket resolution
via the gamma /events endpoint, then computes the PnL we WOULD have realized
if the sweet-band ceiling were higher (so those skipped signals fired).

Assumptions (conservative):
  * Fill at exactly the recorded ask (no slippage modeled — the live bot uses
    FOK so this is the worst-case "no better fill" assumption).
  * Bet direction = sign of ret_60s_pct (same as the live strategy).
  * Filled size = size_usdc / ask (continuous size, matches polymarket trader).
  * PnL = (1.0 * filled_size if won else 0) - size_usdc

Outputs a table of {sweet_hi value → n_fires, win_rate, total PnL}.

Usage:
  python -m polymarket.backtest.sweet_band_counterfactual \
    --logs ~/trading-bot-logs \
    --size-usdc 5
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

GAMMA = "https://gamma-api.polymarket.com"


def _load_skip_rows(logs_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant_dir in sorted(logs_dir.glob("bot=*")):
        variant = variant_dir.name[len("bot="):]
        for path in sorted(variant_dir.glob("*.jsonl")):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("event") != "skip":
                        continue
                    if row.get("reason") != "ask_outside_sweet_band":
                        continue
                    dbg = row.get("debug") or {}
                    rows.append({
                        "variant": variant,
                        "ts": row["ts"],
                        "market_id": dbg.get("market_id", ""),
                        "ret_60s_pct": dbg.get("ret_60s_pct"),
                        "ask": dbg.get("ask"),
                    })
    return rows


def _resolution_lookup(condition_ids: set[str], end_min: datetime,
                       end_max: datetime) -> dict[str, str]:
    """Fetch all closed events between [end_min, end_max] and return
    {condition_id: "UP" | "DOWN" | "UNKNOWN"}."""
    out: dict[str, str] = {}
    client = httpx.Client(timeout=30.0)
    try:
        offset = 0
        while True:
            r = client.get(
                f"{GAMMA}/events",
                params={
                    "closed": "true",
                    "end_date_min": end_min.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_date_max": end_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limit": 100,
                    "offset": offset,
                    "order": "endDate",
                    "ascending": "true",
                },
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for e in data:
                for mk in e.get("markets", []):
                    cid = mk.get("conditionId")
                    if cid not in condition_ids:
                        continue
                    prices = mk.get("outcomePrices")
                    try:
                        up_p, down_p = json.loads(prices) if prices else (None, None)
                        up_p = float(up_p) if up_p is not None else None
                        down_p = float(down_p) if down_p is not None else None
                    except (ValueError, TypeError, json.JSONDecodeError):
                        up_p = down_p = None
                    if up_p is not None and up_p >= 0.99:
                        out[cid] = "UP"
                    elif down_p is not None and down_p >= 0.99:
                        out[cid] = "DOWN"
                    else:
                        out[cid] = "UNKNOWN"
            offset += 100
            if len(data) < 100:
                break
    finally:
        client.close()
    return out


def _bet_direction(ret_60s_pct: float | None) -> str:
    if ret_60s_pct is None:
        return "UNKNOWN"
    return "UP" if ret_60s_pct > 0 else "DOWN"


def _pnl(direction: str, winner: str, ask: float, size_usdc: float) -> float:
    if winner == "UNKNOWN" or direction == "UNKNOWN":
        return 0.0
    won = direction == winner
    filled = size_usdc / ask
    return (filled * 1.0 - size_usdc) if won else (-size_usdc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="~/trading-bot-logs",
                    help="directory containing bot=*/<date>.jsonl trees")
    ap.add_argument("--size-usdc", type=float, default=5.0)
    ap.add_argument("--ceilings", default="0.40,0.45,0.50,0.55,0.60,0.70,0.90",
                    help="comma list of sweet_hi values to evaluate")
    ap.add_argument("--floor", type=float, default=0.30,
                    help="sweet_lo (held constant across scenarios)")
    args = ap.parse_args()

    logs_dir = Path(os.path.expanduser(args.logs))
    rows = _load_skip_rows(logs_dir)
    if not rows:
        print(f"no ask_outside_sweet_band skips found under {logs_dir}", file=sys.stderr)
        return 1

    # Filter out rows lacking the data we need
    rows = [r for r in rows if r["ask"] is not None and r["market_id"]
            and r["ret_60s_pct"] is not None]
    print(f"loaded {len(rows)} skip rows with full data")

    # Time range for resolution lookup
    timestamps = [datetime.fromisoformat(r["ts"]) for r in rows]
    end_min = min(timestamps) - timedelta(minutes=5)
    end_max = max(timestamps) + timedelta(minutes=80)   # hourly markets close ≤60min later
    cids = {r["market_id"] for r in rows}
    print(f"unique markets: {len(cids)}; resolution scan window {end_min} → {end_max}")

    resolutions = _resolution_lookup(cids, end_min, end_max)
    resolved = sum(1 for v in resolutions.values() if v != "UNKNOWN")
    print(f"resolved {resolved}/{len(cids)} markets via gamma")

    # Attach resolution + direction
    for r in rows:
        r["winner"] = resolutions.get(r["market_id"], "UNKNOWN")
        r["direction"] = _bet_direction(r["ret_60s_pct"])
    rows_resolved = [r for r in rows if r["winner"] != "UNKNOWN"]
    print(f"resolved rows: {len(rows_resolved)}/{len(rows)}\n")

    ceilings = [float(x) for x in args.ceilings.split(",")]
    floor = args.floor

    # Header
    print(f"{'sweet_hi':>9} | {'n':>4} {'wins':>4} {'win%':>5} | "
          f"{'gross_pnl':>9} {'avg_pnl':>8} {'per_var':<40}")
    print("-" * 100)

    for hi in ceilings:
        fires = [r for r in rows_resolved if floor <= r["ask"] <= hi]
        n = len(fires)
        if n == 0:
            print(f"{hi:>9.2f} |   no fires in this band")
            continue
        wins = sum(1 for r in fires if r["direction"] == r["winner"])
        total = sum(_pnl(r["direction"], r["winner"], r["ask"], args.size_usdc) for r in fires)
        per_variant: dict[str, int] = defaultdict(int)
        for r in fires:
            per_variant[r["variant"]] += 1
        per_var_str = " ".join(f"{k}={v}" for k, v in sorted(per_variant.items()))
        print(f"{hi:>9.2f} | {n:>4} {wins:>4} {100*wins/n:>4.0f}% | "
              f"{total:>+9.2f} {total/n:>+8.3f} {per_var_str}")

    # Side analysis: distribution of asks rejected
    print("\n--- ask distribution among resolved rejected signals ---")
    asks_sorted = sorted(r["ask"] for r in rows_resolved)
    if asks_sorted:
        print(f"  n={len(asks_sorted)} min={asks_sorted[0]:.2f} "
              f"25p={asks_sorted[len(asks_sorted)//4]:.2f} "
              f"med={asks_sorted[len(asks_sorted)//2]:.2f} "
              f"75p={asks_sorted[3*len(asks_sorted)//4]:.2f} "
              f"max={asks_sorted[-1]:.2f}")

    # Side analysis: direction × winner cross-tab
    print("\n--- direction × winner cross-tab (all resolved rejected signals) ---")
    cross: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows_resolved:
        cross[(r["direction"], r["winner"])] += 1
    for k in sorted(cross):
        print(f"  bet {k[0]:>4} | actual {k[1]:>4}: {cross[k]:>3}")
    n = len(rows_resolved)
    bet_right = sum(v for (d, w), v in cross.items() if d == w)
    print(f"  bet-direction accuracy: {bet_right}/{n} = {100*bet_right/max(1,n):.0f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
