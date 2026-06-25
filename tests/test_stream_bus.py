from __future__ import annotations

import asyncio
import unittest

from src.core.stream_bus import DropPolicy, StreamBus, stream_spec


async def next_with_timeout(stream, timeout_s: float = 0.05):
    return await asyncio.wait_for(anext(stream), timeout=timeout_s)


class StreamBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_routes_by_source_and_param_filters(self) -> None:
        bus = StreamBus(default_queue_size=10)
        btc = bus.subscribe(stream_spec("binance_ws", asset="BTC"))
        eth = bus.subscribe(stream_spec("binance_ws", asset="ETH"))
        all_binance = bus.subscribe(stream_spec("binance_ws"))
        other = bus.subscribe(stream_spec("coinbase_ws", asset="BTC"))

        btc_first = asyncio.create_task(next_with_timeout(btc))
        eth_first = asyncio.create_task(next_with_timeout(eth))
        all_first = asyncio.create_task(next_with_timeout(all_binance))
        other_first = asyncio.create_task(next_with_timeout(other))
        await asyncio.sleep(0)

        delivered = await bus.publish(
            stream_spec("binance_ws", asset="BTC", venue="binance"),
            {"price": 100},
        )

        self.assertEqual(delivered, 2)
        self.assertEqual(await btc_first, {"price": 100})
        self.assertEqual(await all_first, {"price": 100})
        with self.assertRaises(asyncio.TimeoutError):
            await eth_first
        with self.assertRaises(asyncio.TimeoutError):
            await other_first

        await btc.aclose()
        await eth.aclose()
        await all_binance.aclose()
        await other.aclose()

    async def test_subscriber_unregisters_when_closed(self) -> None:
        bus = StreamBus(default_queue_size=10)
        stream = bus.subscribe(stream_spec("headlines"))
        pending = asyncio.create_task(next_with_timeout(stream))
        await asyncio.sleep(0)

        self.assertEqual(bus.subscription_count(), 1)
        await stream.aclose()

        with self.assertRaises(StopAsyncIteration):
            await pending
        self.assertEqual(bus.subscription_count(), 0)
        delivered = await bus.publish(stream_spec("headlines"), {"text": "x"})
        self.assertEqual(delivered, 0)

    async def test_drop_newest_keeps_existing_item_when_full(self) -> None:
        bus = StreamBus(default_queue_size=1)
        stream = bus.subscribe(stream_spec("headlines"))

        self.assertEqual(await bus.publish(stream_spec("headlines"), "first"), 1)
        self.assertEqual(
            await bus.publish(
                stream_spec("headlines"),
                "second",
                drop_policy=DropPolicy.DROP_NEWEST,
            ),
            1,
        )

        self.assertEqual(await next_with_timeout(stream), "first")
        await stream.aclose()

    async def test_drop_oldest_replaces_existing_item_when_full(self) -> None:
        bus = StreamBus(default_queue_size=1)
        stream = bus.subscribe(stream_spec("ticks"))

        self.assertEqual(await bus.publish(stream_spec("ticks"), "old"), 1)
        self.assertEqual(
            await bus.publish(
                stream_spec("ticks"),
                "fresh",
                drop_policy=DropPolicy.DROP_OLDEST,
            ),
            1,
        )

        self.assertEqual(await next_with_timeout(stream), "fresh")
        await stream.aclose()

    async def test_block_policy_waits_for_space(self) -> None:
        bus = StreamBus(default_queue_size=1)
        stream = bus.subscribe(stream_spec("prices"))
        await bus.publish(stream_spec("prices"), "first")

        blocked = asyncio.create_task(bus.publish(stream_spec("prices"), "second"))
        await asyncio.sleep(0)
        self.assertFalse(blocked.done())

        self.assertEqual(await next_with_timeout(stream), "first")
        self.assertEqual(await asyncio.wait_for(blocked, timeout=0.05), 1)
        self.assertEqual(await next_with_timeout(stream), "second")
        await stream.aclose()


if __name__ == "__main__":
    unittest.main()
