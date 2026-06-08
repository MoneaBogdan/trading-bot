"""Record full orderbook depth (both sides) for upcoming BTC 5-min markets.

Goal: build out-of-sample data we couldn't get historically, so the next
backtest iteration can model realistic slippage. Logs include:
  - timestamp (s)
  - BTC last price + 60s return
  - market condition_id + time-to-close
  - full bids[] and asks[] arrays for both UP and DOWN tokens

One JSONL per UTC day at logs/orderbook_<date>.jsonl. Runs forever; restart
on day boundary picks up new file. Run alongside live_trader.py — they don't
fight, both just read public endpoints.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from binance_stream import stream_btc_trades  # noqa: E402
from gamma import discover_btc_markets  # noqa: E402

CLOB = "https://clob.polymarket.com"


def fetch_full_book(client: httpx.Client, token_id: str) -> dict:
    """Return {'bids':[[p,s],...], 'asks':[[p,s],...]} or {} on error."""
    try:
        r = client.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=5.0)
        if r.status_code != 200:
            return {}
        d = r.json()
        return {
            "bids": [[float(b["price"]), float(b["size"])] for b in d.get("bids") or []],
            "asks": [[float(a["price"]), float(a["size"])] for a in d.get("asks") or []],
        }
    except Exception:
        return {}


def topk(book: dict, side: str, k: int = 10) -> list[list[float]]:
    arr = book.get(side, [])
    arr = sorted(arr, key=lambda x: -x[0]) if side == "bids" else sorted(arr, key=lambda x: x[0])
    return arr[:k]


async def refresh_markets(state: dict) -> None:
    while True:
        try:
            mks = await asyncio.get_running_loop().run_in_executor(
                None, lambda: discover_btc_markets(window_horizon_min=10)
            )
            state["markets"] = mks
        except Exception as e:
            print(f"[markets] {e}", flush=True)
        await asyncio.sleep(60)


async def run(log_dir: Path, snapshot_interval_s: float) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    state: dict = {"markets": [], "last_btc_price": None, "btc_trades": deque(maxlen=2000)}
    asyncio.create_task(refresh_markets(state))

    # Producer: BTC trades
    async def btc_loop() -> None:
        async for tr in stream_btc_trades():
            state["last_btc_price"] = tr.price
            state["btc_trades"].append((tr.ts.timestamp(), tr.price))

    asyncio.create_task(btc_loop())

    print(f"[recorder] interval={snapshot_interval_s}s  log_dir={log_dir}", flush=True)
    last_snap = 0.0
    http_client = httpx.Client(timeout=5.0)

    while True:
        now_ts = time.time()
        if now_ts - last_snap < snapshot_interval_s:
            await asyncio.sleep(0.5)
            continue
        last_snap = now_ts
        markets = state["markets"]
        if not markets:
            continue
        btc_now = state["last_btc_price"]
        # 60s return
        trades = list(state["btc_trades"])
        ret_60s = None
        if btc_now is not None and trades:
            cutoff = now_ts - 60
            past = [p for t, p in trades if t <= cutoff]
            if past:
                ret_60s = (btc_now / past[-1] - 1) * 100

        date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
        out_path = log_dir / f"orderbook_{date_key}.jsonl"

        for mk in markets[:3]:  # only nearest 3 markets to limit API rate
            secs_to_close = mk.end_dt.timestamp() - now_ts
            if secs_to_close < 30 or secs_to_close > 600:
                continue
            up_book = fetch_full_book(http_client, mk.up_token_id)
            down_book = fetch_full_book(http_client, mk.down_token_id)
            record = {
                "ts": now_ts,
                "btc_price": btc_now,
                "btc_60s_ret_pct": ret_60s,
                "market": {
                    "title": mk.title,
                    "condition_id": mk.condition_id,
                    "end_iso": mk.window_end_iso,
                    "secs_to_close": secs_to_close,
                    "up_token_id": mk.up_token_id,
                    "down_token_id": mk.down_token_id,
                },
                "up_top10_bids": topk(up_book, "bids"),
                "up_top10_asks": topk(up_book, "asks"),
                "down_top10_bids": topk(down_book, "bids"),
                "down_top10_asks": topk(down_book, "asks"),
            }
            with out_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
        # Tight status line every 60s
        if int(now_ts) % 60 < snapshot_interval_s:
            tracked = sum(1 for mk in markets[:3]
                          if 30 < mk.end_dt.timestamp() - now_ts < 600)
            print(f"[hb] {datetime.now(timezone.utc):%H:%M:%S}  btc=${btc_now}  "
                  f"60s_ret={ret_60s}  tracking={tracked} markets",
                  flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=5.0,
                    help="seconds between orderbook snapshots (default 5)")
    ap.add_argument("--log-dir", default="logs")
    args = ap.parse_args()
    try:
        asyncio.run(run(Path(args.log_dir), args.interval))
    except KeyboardInterrupt:
        print("\n[stop] interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
