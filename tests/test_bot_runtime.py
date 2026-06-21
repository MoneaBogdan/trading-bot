from __future__ import annotations

import asyncio
import unittest

from src.core.bot import BotRuntime, BotSpec
from src.core.stream_bus import StreamBus, stream_spec


async def wait_for_count(items: list, count: int, timeout_s: float = 0.1) -> None:
    async def _wait() -> None:
        while len(items) < count:
            await asyncio.sleep(0)

    await asyncio.wait_for(_wait(), timeout=timeout_s)


class BotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_consumes_events_from_all_subscriptions(self) -> None:
        bus = StreamBus(default_queue_size=10)
        seen: list[dict] = []

        async def handler(event: dict) -> None:
            seen.append(event)

        runtime = BotRuntime(
            BotSpec(
                name="btc-bot",
                subscriptions=(
                    stream_spec("binance_ws", asset="BTC"),
                    stream_spec("market_list", asset="BTC"),
                ),
            ),
            handler,
        )
        task = asyncio.create_task(runtime.run(bus))
        await asyncio.sleep(0)

        await bus.publish(stream_spec("binance_ws", asset="BTC"), {"kind": "price"})
        await bus.publish(stream_spec("market_list", asset="BTC"), {"kind": "markets"})
        await bus.publish(stream_spec("binance_ws", asset="ETH"), {"kind": "wrong-asset"})
        await wait_for_count(seen, 2)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(seen, [{"kind": "price"}, {"kind": "markets"}])
        self.assertEqual(bus.subscription_count(), 0)

    async def test_runtime_supports_sync_handlers(self) -> None:
        bus = StreamBus(default_queue_size=10)
        seen: list[str] = []

        def handler(event: str) -> None:
            seen.append(event)

        runtime = BotRuntime(
            BotSpec(name="headline-bot", subscriptions=(stream_spec("headlines"),)),
            handler,
        )
        task = asyncio.create_task(runtime.run(bus))
        await asyncio.sleep(0)

        await bus.publish(stream_spec("headlines"), "headline")
        await wait_for_count(seen, 1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(seen, ["headline"])
        self.assertEqual(bus.subscription_count(), 0)

    async def test_runtime_propagates_handler_errors_and_closes_subscriptions(self) -> None:
        bus = StreamBus(default_queue_size=10)

        def handler(event: str) -> None:
            raise RuntimeError(f"bad event: {event}")

        runtime = BotRuntime(
            BotSpec(name="bad-bot", subscriptions=(stream_spec("headlines"),)),
            handler,
        )
        task = asyncio.create_task(runtime.run(bus))
        await asyncio.sleep(0)

        await bus.publish(stream_spec("headlines"), "boom")

        with self.assertRaisesRegex(RuntimeError, "bad event: boom"):
            await task
        self.assertEqual(bus.subscription_count(), 0)

    async def test_bot_spec_validates_required_fields(self) -> None:
        with self.assertRaises(ValueError):
            BotSpec(name="", subscriptions=(stream_spec("x"),))
        with self.assertRaises(ValueError):
            BotSpec(name="x", subscriptions=())


if __name__ == "__main__":
    unittest.main()
