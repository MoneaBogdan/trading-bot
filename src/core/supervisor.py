"""Small Phase D supervisor skeleton.

The supervisor coordinates an in-process StreamBus, data sources, and bot
runtimes. v1 is intentionally conservative: it starts tasks, deduplicates
sources by stream specs, and cancels everything together. Restart/backoff policy
comes after this base is exercised by a shadow bot.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.core.bot import BotRuntime
from src.core.stream_bus import StreamBus, StreamSpec, specs_key


SourceRunner = Callable[[StreamBus], Awaitable[None]]


@dataclass(frozen=True)
class DataSourceSpec:
    name: str
    streams: tuple[StreamSpec, ...]
    run: SourceRunner

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("source name is required")
        if not self.streams:
            raise ValueError("at least one source stream is required")

    @property
    def dedupe_key(self) -> tuple[StreamSpec, ...]:
        return specs_key(self.streams)


class Supervisor:
    def __init__(self, *, bus: StreamBus | None = None):
        self.bus = bus or StreamBus()
        self._sources: list[DataSourceSpec] = []
        self._bots: list[BotRuntime] = []

    def add_source(self, source: DataSourceSpec) -> None:
        self._sources.append(source)

    def add_bot(self, bot: BotRuntime) -> None:
        self._bots.append(bot)

    async def run(self) -> None:
        source_tasks: list[asyncio.Task[Any]] = []
        bot_tasks = [asyncio.create_task(bot.run(self.bus), name=f"bot:{bot.spec.name}")
                     for bot in self._bots]
        try:
            await asyncio.sleep(0)
            for source in self._deduped_sources():
                source_tasks.append(
                    asyncio.create_task(source.run(self.bus), name=f"source:{source.name}")
                )
            await asyncio.gather(*bot_tasks, *source_tasks)
        except asyncio.CancelledError:
            raise
        finally:
            for task in (*source_tasks, *bot_tasks):
                task.cancel()
            await asyncio.gather(*source_tasks, *bot_tasks, return_exceptions=True)
            await self.bus.close()

    def _deduped_sources(self) -> list[DataSourceSpec]:
        seen: set[tuple[StreamSpec, ...]] = set()
        result: list[DataSourceSpec] = []
        for source in self._sources:
            key = source.dedupe_key
            if key in seen:
                continue
            seen.add(key)
            result.append(source)
        return result
