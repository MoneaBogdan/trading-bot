"""Minimal bot runtime for Phase D stream-bus migration.

This is the side-effect shell that future supervisor-managed bots can use to
consume bus events. It intentionally does not know about strategies, venues, or
orders yet; those stay in the bot-specific handler while the migration is small.
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.core.stream_bus import StreamBus, StreamSpec


EventHandler = Callable[[Any], None | Awaitable[None]]


@dataclass(frozen=True)
class BotSpec:
    name: str
    subscriptions: tuple[StreamSpec, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("bot name is required")
        if not self.subscriptions:
            raise ValueError("at least one subscription is required")


class BotRuntime:
    def __init__(self, spec: BotSpec, handler: EventHandler):
        self.spec = spec
        self.handler = handler

    async def run(self, bus: StreamBus) -> None:
        streams = [bus.subscribe(spec) for spec in self.spec.subscriptions]
        tasks = [asyncio.create_task(self._consume(stream)) for stream in streams]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
        finally:
            for stream in streams:
                await stream.aclose()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _consume(self, stream) -> None:
        async for event in stream:
            result = self.handler(event)
            if inspect.isawaitable(result):
                await result
