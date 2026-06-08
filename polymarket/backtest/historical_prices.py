"""Fetch CLOB /prices-history for each market in the markets cache.

Stores one JSON file per market keyed by condition_id, containing both
UP and DOWN token price series at 1-min fidelity over the market's
trading lifetime.

Resumable: skips markets that already have a price file on disk.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx

CLOB = "https://clob.polymarket.com"
ROOT = Path(__file__).parent
CACHE_DIR = ROOT / "cache"
PRICES_DIR = CACHE_DIR / "prices"


def fetch_prices(client: httpx.Client, token_id: str, start_ts: int, end_ts: int,
                 fidelity_min: int = 1) -> list[dict]:
    """Return list of {t, p} points; empty list on error."""
    r = client.get(
        f"{CLOB}/prices-history",
        params={"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": fidelity_min},
    )
    if r.status_code != 200:
        return []
    return r.json().get("history", [])


def fetch_market(market: dict, lookback_min: int) -> tuple[str, dict | None, str]:
    """Worker: fetch UP+DOWN prices for one market. Returns (condition_id, payload, status)."""
    cond = market["condition_id"]
    end_ts = market["end_ts"]
    start_ts = end_ts - lookback_min * 60
    try:
        with httpx.Client(timeout=20.0) as client:
            up = fetch_prices(client, market["up_token_id"], start_ts, end_ts + 60)
            down = fetch_prices(client, market["down_token_id"], start_ts, end_ts + 60)
    except Exception as e:
        return cond, None, f"err: {e}"
    if not up and not down:
        return cond, None, "empty"
    payload = {
        "condition_id": cond,
        "title": market["title"],
        "start_ts": start_ts,
        "end_ts": end_ts,
        "winner": market["winner"],
        "up_token_id": market["up_token_id"],
        "down_token_id": market["down_token_id"],
        "up_series": up,
        "down_series": down,
    }
    return cond, payload, f"ok ({len(up)}+{len(down)} pts)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets-file", required=True,
                    help="path to markets_<from>_<to>.json")
    ap.add_argument("--lookback-min", type=int, default=15,
                    help="how many minutes before end_ts to pull (default 15)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0,
                    help="if >0, only fetch first N markets (smoke test)")
    args = ap.parse_args()

    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    with open(args.markets_file) as f:
        markets = json.load(f)
    if args.limit:
        markets = markets[: args.limit]

    todo = [m for m in markets if not (PRICES_DIR / f"{m['condition_id']}.json").exists()]
    print(f"[prices] {len(markets)} markets total, {len(todo)} to fetch "
          f"({len(markets) - len(todo)} cached)")
    if not todo:
        return 0

    t0 = time.time()
    ok = 0
    fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch_market, m, args.lookback_min): m for m in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            cond, payload, status = fut.result()
            if payload:
                out = PRICES_DIR / f"{cond}.json"
                with out.open("w") as f:
                    json.dump(payload, f)
                ok += 1
            else:
                fail += 1
            if i % 50 == 0 or i == len(todo):
                rate = i / (time.time() - t0)
                print(f"  [{i}/{len(todo)}] ok={ok} fail={fail}  ({rate:.1f}/s)")

    print(f"\n[done] {ok} files written to {PRICES_DIR}, {fail} failures, "
          f"elapsed {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
