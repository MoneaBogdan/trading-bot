"""WebSocket-based orderbook recorder.

Replaces orderbook_recorder.py's 5s HTTP polling with Polymarket's official
WebSocket. Captures every book/price/trade update for the upcoming BTC
5-min markets — ~50-200 events/sec across all tracked tokens, much higher
fidelity than polling.

Log: logs/orderbook_ws_<date>.jsonl

Each line is one of these event types (raw from the SDK):
  - MarketBookEvent: full snapshot when first subscribed
  - MarketPriceChangeEvent: delta updates (level changes)
  - MarketLastTradePriceEvent: every trade
  - MarketBestBidAskEvent: top-of-book changes
  - MarketResolvedEvent: when a market closes
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gamma import discover_btc_markets  # noqa: E402


def event_to_dict(ev) -> dict:
    """Pydantic event → JSON-safe dict."""
    if hasattr(ev, "model_dump"):
        try:
            return ev.model_dump(mode="json")
        except Exception:
            pass
    return {"raw": str(ev)}


async def run(log_dir: Path, refresh_interval_s: int) -> None:
    from polymarket import AsyncPublicClient
    from polymarket.streams import MarketSpec

    log_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncPublicClient() as client:
        current_handle = None
        current_tokens: set[str] = set()

        while True:
            try:
                mks = await asyncio.to_thread(discover_btc_markets, 15)
            except Exception as e:
                print(f"[markets] {e}", flush=True)
                await asyncio.sleep(refresh_interval_s)
                continue

            tokens: set[str] = set()
            for mk in mks[:3]:  # nearest 3 markets
                tokens.add(mk.up_token_id)
                tokens.add(mk.down_token_id)

            if tokens != current_tokens and tokens:
                if current_handle is not None:
                    try:
                        await current_handle.close()
                    except Exception:
                        pass
                spec = MarketSpec(token_ids=list(tokens), custom_feature_enabled=True)
                current_handle = await client.subscribe(spec)
                current_tokens = tokens
                print(f"[sub] now tracking {len(tokens)} tokens across {len(mks[:3])} markets",
                      flush=True)
                # Spawn reader task for this handle
                asyncio.create_task(_drain(current_handle, log_dir))

            await asyncio.sleep(refresh_interval_s)


async def _drain(handle, log_dir: Path) -> None:
    n = 0
    t0 = time.time()
    try:
        async for ev in handle:
            n += 1
            payload = event_to_dict(ev)
            payload["_recv_ts"] = time.time()
            payload["_type"] = type(ev).__name__
            date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
            out_path = log_dir / f"orderbook_ws_{date_key}.jsonl"
            with out_path.open("a") as f:
                f.write(json.dumps(payload, default=str) + "\n")
            if n % 100 == 0:
                rate = n / (time.time() - t0)
                print(f"[recv] {n} events  ({rate:.1f}/s)", flush=True)
    except Exception as e:
        print(f"[drain] stopped: {e}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", type=int, default=60,
                    help="seconds between market-list refreshes (default 60)")
    ap.add_argument("--log-dir", default="logs")
    args = ap.parse_args()
    try:
        asyncio.run(run(Path(args.log_dir), args.refresh))
    except KeyboardInterrupt:
        print("\n[stop] interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
