"""Telegram source: Telethon → asyncio.Queue[Headline].

Wraps a TelegramClient and pushes `Headline` objects for every new message
in the configured channels.

Env vars consumed:
  TG_API_ID, TG_API_HASH       Telethon credentials from my.telegram.org
  TG_SESSION_NAME              session file basename (default 'tree_news_recorder')
  TG_CHANNELS                  comma-separated channel usernames (no @)

First run requires interactive phone+code login — Telethon will prompt on stdin.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User

from .types import Headline


def _channel_label(entity) -> str:
    if isinstance(entity, (Channel, Chat)):
        return getattr(entity, "username", None) or f"id:{entity.id}"
    if isinstance(entity, User):
        return entity.username or f"user:{entity.id}"
    return "unknown"


async def run(queue: asyncio.Queue[Headline]) -> None:
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session = os.environ.get("TG_SESSION_NAME", "tree_news_recorder")
    channels_raw = os.environ.get("TG_CHANNELS", "")
    channels = [c.strip().lstrip("@") for c in channels_raw.split(",") if c.strip()]
    if not channels:
        raise SystemExit("TG_CHANNELS is empty — set at least one channel username")

    client = TelegramClient(session, api_id, api_hash)
    await client.start()

    resolved = []
    for ch in channels:
        try:
            entity = await client.get_entity(ch)
            resolved.append(entity)
            print(f"[telegram] subscribed: @{ch} (id={entity.id})", flush=True)
        except Exception as e:
            print(f"[telegram] failed to resolve @{ch}: {e}", flush=True)
    if not resolved:
        raise SystemExit("no Telegram channels resolved")

    @client.on(events.NewMessage(chats=resolved))
    async def _on_message(event: events.NewMessage.Event) -> None:
        msg = event.message
        text = msg.message or ""
        if not text:
            return
        ts = (msg.date or datetime.now(timezone.utc)).astimezone(timezone.utc)
        await queue.put(Headline(
            ts=ts,
            ts_received=datetime.now(timezone.utc),
            source="telegram",
            channel=_channel_label(await event.get_chat()),
            message_id=str(msg.id),
            text=text,
            raw=msg.to_dict(),
        ))

    print("[telegram] listening for headlines...", flush=True)
    await client.run_until_disconnected()
