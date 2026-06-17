"""Tree of Alpha REST poller.

Polls https://news.treeofalpha.com/api/news on a fixed interval, dedups items
by their `_id`, and pushes new headlines onto an asyncio.Queue.

This is the FREE tier of the actual Tree of Alpha aggregator — same source list
as their paid websocket, just with a few-seconds-to-minutes latency floor
(measured ~2-3 min at the median when first probed). No authentication
required. No session file. Replaces the Telegram-impersonator-risk path.

Env vars consumed:
  TOA_POLL_INTERVAL_S   poll cadence in seconds (default 5)
  TOA_REST_URL          override endpoint (default https://news.treeofalpha.com/api/news)
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .types import Headline

DEFAULT_URL = "https://news.treeofalpha.com/api/news"
DEFAULT_INTERVAL_S = 5.0


def _ms_to_dt(ms: int | float | None) -> datetime:
    if ms is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def _headline_from_item(item: dict[str, Any]) -> Headline | None:
    """Convert a Tree of Alpha REST item into a `Headline`. Returns None if
    the item is missing the bare minimum we need (id + title + time)."""
    mid = item.get("_id")
    text = item.get("title") or item.get("en") or ""
    if not mid or not text:
        return None
    ts = _ms_to_dt(item.get("time"))
    channel = str(item.get("sourceName") or item.get("source") or "")
    return Headline(
        ts=ts,
        ts_received=datetime.now(timezone.utc),
        source="treeofalpha_rest",
        channel=channel,
        message_id=str(mid),
        text=text,
        raw=item,
    )


async def run(
    queue: asyncio.Queue[Headline],
    *,
    url: str | None = None,
    interval_s: float | None = None,
    seen_cap: int = 2000,
) -> None:
    """Poll the REST endpoint forever, pushing new headlines onto `queue`.

    Dedups by `_id` using a bounded FIFO set so the working set doesn't grow
    unbounded over a long run. `seen_cap` bounds memory; ~2000 ids comfortably
    covers a few hours of feed even at peak rates.
    """
    url = url or os.environ.get("TOA_REST_URL", DEFAULT_URL)
    interval_s = interval_s or float(os.environ.get("TOA_POLL_INTERVAL_S", DEFAULT_INTERVAL_S))

    seen: set[str] = set()
    seen_order: list[str] = []

    def _remember(mid: str) -> None:
        if mid in seen:
            return
        seen.add(mid)
        seen_order.append(mid)
        if len(seen_order) > seen_cap:
            evict = seen_order.pop(0)
            seen.discard(evict)

    headers = {"User-Agent": "Mozilla/5.0 (compatible; trading-bot/news_alpha)"}
    backoff = interval_s
    print(f"[toa-rest] polling {url} every {interval_s:.1f}s", flush=True)

    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        # Prime `seen` with the current snapshot so we don't fire on already-known
        # headlines at startup. Without this, every restart would replay the
        # entire most-recent 100 headlines through the classifier.
        try:
            r = await client.get(url)
            r.raise_for_status()
            for it in r.json():
                mid = it.get("_id")
                if mid:
                    _remember(str(mid))
            print(f"[toa-rest] primed with {len(seen)} known ids; entering live loop", flush=True)
        except Exception as e:
            print(f"[toa-rest] prime failed ({type(e).__name__}: {e}); starting cold", flush=True)

        while True:
            try:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
                new = 0
                # Items are newest-first; iterate oldest-first so we push in
                # chronological order even on a single batch.
                for it in reversed(data):
                    mid = it.get("_id")
                    if not mid or mid in seen:
                        continue
                    headline = _headline_from_item(it)
                    if headline is None:
                        _remember(str(mid))
                        continue
                    _remember(str(mid))
                    await queue.put(headline)
                    new += 1
                if new:
                    print(f"[toa-rest] +{new} new headlines (queue depth ~{queue.qsize()})",
                          flush=True)
                backoff = interval_s
            except (httpx.HTTPError, ValueError) as e:
                # Network or JSON error — back off and continue. Don't crash the source.
                backoff = min(backoff * 2, 60.0)
                print(f"[toa-rest] fetch error: {type(e).__name__}: {e}; "
                      f"backing off {backoff:.1f}s", flush=True)
            await asyncio.sleep(backoff)
