from __future__ import annotations

import asyncio
import unittest

from src.core.bot import BotRuntime, BotSpec
from src.core.stream_bus import StreamBus, stream_spec
from src.core.supervisor import DataSourceSpec, Supervisor


async def wait_for_count(items: list, count: int, timeout_s: float = 0.1) -> None:
    async def _wait() -> None:
        while len(items) < count:
            await asyncio.sleep(0)

    await asyncio.wait_for(_wait(), timeout=timeout_s)


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_runs_source_and_bot_on_shared_bus(self) -> None:
        seen: list[dict] = []
        source_started = asyncio.Event()
        keep_running = asyncio.Event()

        async def source(bus: StreamBus) -> None:
            source_started.set()
            await bus.publish(stream_spec("binance_ws", asset="BTC"), {"price": 100})
            await keep_running.wait()

        async def handler(event: dict) -> None:
            seen.append(event)

        supervisor = Supervisor()
        supervisor.add_bot(
            BotRuntime(
                BotSpec(name="btc-bot", subscriptions=(stream_spec("binance_ws", asset="BTC"),)),
                handler,
            )
        )
        supervisor.add_source(
            DataSourceSpec(
                name="binance-btc",
                streams=(stream_spec("binance_ws", asset="BTC"),),
                run=source,
            )
        )

        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(source_started.wait(), timeout=0.1)
        await wait_for_count(seen, 1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(seen, [{"price": 100}])
        self.assertEqual(supervisor.bus.subscription_count(), 0)

    async def test_supervisor_deduplicates_sources_by_stream_specs(self) -> None:
        starts = 0
        source_started = asyncio.Event()
        keep_running = asyncio.Event()

        async def source(bus: StreamBus) -> None:
            nonlocal starts
            starts += 1
            source_started.set()
            await keep_running.wait()

        spec = stream_spec("binance_ws", asset="BTC")
        supervisor = Supervisor()
        supervisor.add_source(DataSourceSpec(name="first", streams=(spec,), run=source))
        supervisor.add_source(DataSourceSpec(name="duplicate", streams=(spec,), run=source))

        task = asyncio.create_task(supervisor.run())
        await asyncio.wait_for(source_started.wait(), timeout=0.1)
        await asyncio.sleep(0)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(starts, 1)

    async def test_supervisor_propagates_source_error_and_cleans_up(self) -> None:
        async def source(bus: StreamBus) -> None:
            raise RuntimeError("source failed")

        supervisor = Supervisor()
        supervisor.add_bot(
            BotRuntime(
                BotSpec(name="btc-bot", subscriptions=(stream_spec("binance_ws", asset="BTC"),)),
                lambda event: None,
            )
        )
        supervisor.add_source(
            DataSourceSpec(
                name="bad-source",
                streams=(stream_spec("binance_ws", asset="BTC"),),
                run=source,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "source failed"):
            await supervisor.run()
        self.assertEqual(supervisor.bus.subscription_count(), 0)

    async def test_data_source_spec_validates_required_fields(self) -> None:
        async def source(bus: StreamBus) -> None:
            return None

        with self.assertRaises(ValueError):
            DataSourceSpec(name="", streams=(stream_spec("x"),), run=source)
        with self.assertRaises(ValueError):
            DataSourceSpec(name="x", streams=(), run=source)


if __name__ == "__main__":
    unittest.main()
