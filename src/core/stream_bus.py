"""In-process async pub/sub for Phase D supervisor work.

The bus is deliberately small: no persistence, no network, and one bounded
queue per subscriber. Data sources publish with an explicit StreamSpec so the
supervisor can later deduplicate them by the same shape.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator, Iterable


class DropPolicy(StrEnum):
    BLOCK = "block"
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"


@dataclass(frozen=True)
class StreamSpec:
    source_name: str
    params: tuple[tuple[str, Any], ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, source_name: str, **params: Any) -> "StreamSpec":
        return cls(source_name=source_name, params=tuple(sorted(params.items())))

    def matches(self, other: "StreamSpec") -> bool:
        """True when this subscription wants events from `other`.

        Subscriber params are filters. A subscription to binance_ws(asset=BTC)
        receives binance_ws(asset=BTC, venue=binance) but not ETH.
        """
        if self.source_name != other.source_name:
            return False
        other_params = dict(other.params)
        return all(other_params.get(key) == value for key, value in self.params)


@dataclass(frozen=True, eq=False)
class _Subscription:
    spec: StreamSpec
    queue: asyncio.Queue


class _SubscriptionIterator:
    def __init__(self, bus: "StreamBus", subscription: _Subscription):
        self._bus = bus
        self._subscription = subscription
        self._closed = False
        self._closed_event = asyncio.Event()

    def __aiter__(self) -> "_SubscriptionIterator":
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        get_task = asyncio.create_task(self._subscription.queue.get())
        close_task = asyncio.create_task(self._closed_event.wait())
        done, pending = await asyncio.wait(
            {get_task, close_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if close_task in done:
            if not get_task.done():
                get_task.cancel()
            raise StopAsyncIteration
        close_task.cancel()
        return get_task.result()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._unregister(self._subscription)
        self._closed_event.set()


class StreamBus:
    def __init__(self, *, default_queue_size: int = 1000):
        if default_queue_size < 1:
            raise ValueError("default_queue_size must be >= 1")
        self.default_queue_size = default_queue_size
        self._subscriptions: set[_Subscription] = set()

    async def publish(
        self,
        spec: StreamSpec,
        event: Any,
        *,
        drop_policy: DropPolicy = DropPolicy.BLOCK,
    ) -> int:
        """Publish one event and return the number of subscriber queues hit."""
        subscriptions = self._matching_subscriptions(spec)
        for sub in subscriptions:
            await self._put(sub.queue, event, drop_policy)
        return len(subscriptions)

    def _matching_subscriptions(self, spec: StreamSpec) -> list[_Subscription]:
        return [sub for sub in self._subscriptions if sub.spec.matches(spec)]

    def subscribe(
        self,
        spec: StreamSpec,
        *,
        queue_size: int | None = None,
    ) -> AsyncIterator[Any]:
        """Subscribe to events matching `spec`.

        The returned async iterator unregisters itself when closed or when the
        consuming task exits the loop.
        """
        size = queue_size if queue_size is not None else self.default_queue_size
        if size < 1:
            raise ValueError("queue_size must be >= 1")
        sub = _Subscription(spec=spec, queue=asyncio.Queue(maxsize=size))
        self._subscriptions.add(sub)
        return _SubscriptionIterator(self, sub)

    async def close(self) -> None:
        self._subscriptions.clear()

    def _unregister(self, subscription: _Subscription) -> None:
        self._subscriptions.discard(subscription)

    async def _put(
        self,
        queue: asyncio.Queue,
        event: Any,
        drop_policy: DropPolicy,
    ) -> None:
        if drop_policy == DropPolicy.BLOCK:
            await queue.put(event)
            return
        if drop_policy == DropPolicy.DROP_NEWEST:
            if queue.full():
                return
            queue.put_nowait(event)
            return
        if drop_policy == DropPolicy.DROP_OLDEST:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
            return
        raise ValueError(f"unknown drop policy {drop_policy!r}")

    def subscription_count(self) -> int:
        return len(self._subscriptions)


def stream_spec(source_name: str, **params: Any) -> StreamSpec:
    return StreamSpec.of(source_name, **params)


def specs_key(specs: Iterable[StreamSpec]) -> tuple[StreamSpec, ...]:
    """Stable key helper for supervisor-side source deduping."""
    return tuple(sorted(specs, key=lambda spec: (spec.source_name, spec.params)))
