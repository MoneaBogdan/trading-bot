"""Telegram news recorder.

Connects as a Telegram user-client (Telethon) and appends every message from
the configured channels to `logs/news/YYYY-MM-DD.jsonl` — one row per message,
UTC-dated at write time so it rotates cleanly across midnight.

Row schema (stable, append-only):
    {
        "ts": "2026-06-16T13:42:01.234+00:00",   # message time (UTC, from Telegram)
        "ts_received": "...",                     # local receive time, for latency study
        "source": "telegram",
        "channel": "treeofalpha",                 # channel username (no @)
        "message_id": 12345,
        "text": "BREAKING: ...",
        "raw": { ... }                            # full Telegram message dict for replay
    }

This recorder does NOT trade and does NOT classify. It only persists raw events
so we can build/backtest a classifier offline before risking capital.

ENV:
    TG_API_ID            integer, from https://my.telegram.org/apps
    TG_API_HASH          hex string, from the same page
    TG_SESSION_NAME      session file basename (default: 'tree_news_recorder')
    TG_CHANNELS          comma-separated channel usernames or invite links
                         (default: 'treeofalpha')
    NEWS_LOG_DIR         output directory (default: 'logs/news')

First-run: requires interactive phone-number + SMS-code login. Session is saved
to `<TG_SESSION_NAME>.session` next to wherever you ran it from — keep that
file (it's the auth artifact; treat like a credential).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from telethon import TelegramClient, events
    from telethon.tl.types import Channel, Chat, User
except ImportError:
    sys.stderr.write(
        "telethon not installed. Run: pip install telethon\n"
    )
    raise


_write_lock = threading.Lock()


def _row_path(base: Path, ts: datetime) -> Path:
    return base / f"{ts:%Y-%m-%d}.jsonl"


def _append_row(base: Path, row: dict[str, Any]) -> None:
    ts = datetime.fromisoformat(row["ts"])
    path = _row_path(base, ts)
    line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _channel_username(entity: Any) -> str:
    if isinstance(entity, (Channel, Chat)):
        return getattr(entity, "username", None) or f"id:{entity.id}"
    if isinstance(entity, User):
        return entity.username or f"user:{entity.id}"
    return "unknown"


async def _run() -> None:
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session_name = os.environ.get("TG_SESSION_NAME", "tree_news_recorder")
    channels_raw = os.environ.get("TG_CHANNELS", "treeofalpha")
    channels = [c.strip().lstrip("@") for c in channels_raw.split(",") if c.strip()]

    log_dir = Path(os.environ.get("NEWS_LOG_DIR", "logs/news"))
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"[recorder] session={session_name} channels={channels} log_dir={log_dir}",
          flush=True)

    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()

    resolved = []
    for ch in channels:
        try:
            entity = await client.get_entity(ch)
            resolved.append(entity)
            print(f"[recorder] resolved @{ch} -> id={entity.id} title={getattr(entity, 'title', '?')}",
                  flush=True)
        except Exception as e:
            print(f"[recorder] FAILED to resolve @{ch}: {e}", flush=True)

    if not resolved:
        raise SystemExit("no channels resolved; check TG_CHANNELS")

    @client.on(events.NewMessage(chats=resolved))
    async def _on_message(event: events.NewMessage.Event) -> None:
        msg = event.message
        ts_msg = (msg.date or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ts_now = datetime.now(timezone.utc)
        row = {
            "ts": ts_msg.isoformat(timespec="milliseconds"),
            "ts_received": ts_now.isoformat(timespec="milliseconds"),
            "source": "telegram",
            "channel": _channel_username(await event.get_chat()),
            "message_id": msg.id,
            "text": msg.message or "",
            "raw": msg.to_dict(),
        }
        try:
            _append_row(log_dir, row)
            preview = (row["text"][:80] + "...") if len(row["text"]) > 80 else row["text"]
            print(f"[recorder] {row['ts']} @{row['channel']} #{row['message_id']}: {preview}",
                  flush=True)
        except Exception as e:
            print(f"[recorder] write error: {e}", flush=True)

    print("[recorder] listening...", flush=True)
    await client.run_until_disconnected()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("[recorder] shutdown (KeyboardInterrupt)", flush=True)


if __name__ == "__main__":
    main()
