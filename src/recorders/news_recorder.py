"""Unified news recorder. Dispatches on NEWS_SOURCE env var.

NEWS_SOURCE=treeofalpha_rest  → polls https://news.treeofalpha.com/api/news
NEWS_SOURCE=telegram          → Telethon listener on TG_CHANNELS

Writes one JSONL row per headline to:
  <NEWS_LOG_DIR>/YYYY-MM-DD.jsonl
default NEWS_LOG_DIR='logs/news'.

Row schema (stable, shared across both sources — see Headline dataclass):
  {ts, ts_received, source, channel, message_id, text, raw}

This is the data-collection counterpart to the live bot. It does NOT trade
and does NOT classify. Run it to build a corpus before the live bot does.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

# Make repo root importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.strategies.news_alpha.sources.types import Headline  # noqa: E402

_write_lock = threading.Lock()


def _write(base: Path, h: Headline) -> None:
    path = base / f"{h.ts:%Y-%m-%d}.jsonl"
    row = {
        "ts": h.ts.isoformat(timespec="milliseconds"),
        "ts_received": h.ts_received.isoformat(timespec="milliseconds"),
        "source": h.source,
        "channel": h.channel,
        "message_id": h.message_id,
        "text": h.text,
        "raw": h.raw,
    }
    line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _preview(text: str, n: int = 100) -> str:
    return (text[:n] + "…") if len(text) > n else text


async def _consume(queue: asyncio.Queue[Headline], base: Path) -> None:
    while True:
        h = await queue.get()
        try:
            _write(base, h)
            print(f"[recorder] {h.ts.isoformat(timespec='seconds')} "
                  f"{h.source}/{h.channel} #{h.message_id}: {_preview(h.text)}",
                  flush=True)
        except Exception as e:
            print(f"[recorder] write error: {type(e).__name__}: {e}", flush=True)


async def _main_async() -> None:
    source = os.environ.get("NEWS_SOURCE", "treeofalpha_rest").lower()
    base = Path(os.environ.get("NEWS_LOG_DIR", "logs/news"))
    base.mkdir(parents=True, exist_ok=True)

    print(f"[recorder] source={source} log_dir={base}", flush=True)

    if source == "treeofalpha_rest":
        from src.strategies.news_alpha.sources import treeofalpha_rest as src_mod
    elif source == "telegram":
        from src.strategies.news_alpha.sources import telegram as src_mod
    else:
        raise SystemExit(f"unknown NEWS_SOURCE={source!r}; "
                         f"expected 'treeofalpha_rest' or 'telegram'")

    queue: asyncio.Queue[Headline] = asyncio.Queue(maxsize=10_000)
    src_task = asyncio.create_task(src_mod.run(queue))
    consumer = asyncio.create_task(_consume(queue, base))

    # Either task ending unexpectedly should bring the recorder down.
    done, pending = await asyncio.wait(
        [src_task, consumer], return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    for t in done:
        exc = t.exception()
        if exc is not None:
            raise exc


def main() -> None:
    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        print("[recorder] shutdown (KeyboardInterrupt)", flush=True)


if __name__ == "__main__":
    main()
