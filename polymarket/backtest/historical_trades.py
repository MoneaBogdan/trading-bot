"""Fetch Data API /trades per market — the executed trade tape.

Each row: side (BUY/SELL), price, size, timestamp, asset (token_id).
Lets us simulate "next executable price at signal_ts + latency".

Resumable: skips markets that already have a trades file.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

DATA = "https://data-api.polymarket.com"
ROOT = Path(__file__).parent
CACHE_DIR = ROOT / "cache"
TRADES_DIR = CACHE_DIR / "trades"


def fetch_market_trades(market: dict, max_pages: int = 5) -> tuple[str, dict | None, str]:
    """Fetch up to max_pages * 500 trades for one market.

    Data API returns latest trades first; we paginate by offset until empty
    or until we hit max_pages.
    """
    cond = market["condition_id"]
    rows: list[dict] = []
    try:
        with httpx.Client(timeout=20.0) as client:
            for page in range(max_pages):
                r = client.get(f"{DATA}/trades", params={
                    "market": cond, "limit": 500, "offset": page * 500,
                })
                if r.status_code != 200:
                    break
                batch = r.json()
                if not batch:
                    break
                rows.extend(batch)
                if len(batch) < 500:
                    break
    except Exception as e:
        return cond, None, f"err: {e}"
    if not rows:
        return cond, None, "empty"
    # Sort ascending by timestamp for replay use
    rows.sort(key=lambda r: r.get("timestamp", 0))
    payload = {
        "condition_id": cond,
        "title": market["title"],
        "end_ts": market["end_ts"],
        "winner": market["winner"],
        "up_token_id": market["up_token_id"],
        "down_token_id": market["down_token_id"],
        "trades": rows,
    }
    return cond, payload, f"ok ({len(rows)} trades)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets-file", required=True)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=5,
                    help="max 500-row pages per market (default 5 = 2500 trades)")
    args = ap.parse_args()

    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    with open(args.markets_file) as f:
        markets = json.load(f)
    if args.limit:
        markets = markets[: args.limit]

    todo = [m for m in markets if not (TRADES_DIR / f"{m['condition_id']}.json").exists()]
    print(f"[trades] {len(markets)} markets, {len(todo)} to fetch "
          f"({len(markets) - len(todo)} cached)")
    if not todo:
        return 0

    t0 = time.time()
    ok = fail = empty = 0
    total_rows = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch_market_trades, m, args.max_pages): m for m in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            cond, payload, status = fut.result()
            if payload:
                out = TRADES_DIR / f"{cond}.json"
                with out.open("w") as f:
                    json.dump(payload, f)
                ok += 1
                total_rows += len(payload["trades"])
            elif status == "empty":
                empty += 1
            else:
                fail += 1
            if i % 100 == 0 or i == len(todo):
                rate = i / (time.time() - t0)
                print(f"  [{i}/{len(todo)}] ok={ok} empty={empty} fail={fail} "
                      f"avg_trades={total_rows/max(1,ok):.0f}  ({rate:.1f}/s)")

    print(f"\n[done] {ok} files, {empty} empty, {fail} fail, "
          f"total {total_rows} trades, elapsed {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
