from __future__ import annotations

import json
import asyncio
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


@dataclass(frozen=True)
class Trade:
    ts: datetime
    price: float


@dataclass(frozen=True)
class Market:
    title: str = "Bitcoin Up or Down"
    condition_id: str = "condition-1"
    end_dt: datetime = datetime(2026, 6, 21, 12, 5, tzinfo=timezone.utc)
    window_end_iso: str = "2026-06-21T12:05:00+00:00"
    up_token_id: str = "up-token"
    down_token_id: str = "down-token"


@dataclass(frozen=True)
class Orderbook:
    best_ask: float | None


@dataclass(frozen=True)
class TraderConfig:
    dry_run: bool = True
    max_order_usdc: float = 5.0
    max_daily_usdc: float = 50.0


@dataclass(frozen=True)
class OrderResult:
    ok: bool = True
    order_id: str = "order-1"
    filled_size: float = 7.5
    filled_price: float = 0.66
    error: str | None = None


class FakeTrader:
    def __init__(self) -> None:
        self.config = TraderConfig()
        self.orders: list[tuple[str, float, float]] = []

    def place_buy_fok(self, token_id: str, price: float, size_usdc: float) -> OrderResult:
        self.orders.append((token_id, price, size_usdc))
        return OrderResult()


class FakeLogger:
    def __init__(self) -> None:
        self.boots: list[dict] = []
        self.skips: list[tuple[str, dict]] = []
        self.fires: list[dict] = []

    def boot(self, **config) -> None:
        self.boots.append(config)

    def skip(self, reason: str, **fields) -> None:
        self.skips.append((reason, fields))

    def fire(self, **fields) -> None:
        self.fires.append(fields)


def install_import_fakes() -> None:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules.setdefault("dotenv", dotenv)

    binance_stream = types.ModuleType("binance_stream")
    binance_stream.stream_trades = None
    sys.modules.setdefault("binance_stream", binance_stream)

    coinbase_stream = types.ModuleType("coinbase_stream")
    coinbase_stream.stream_trades = None
    sys.modules.setdefault("coinbase_stream", coinbase_stream)

    clob = types.ModuleType("clob")
    clob.get_orderbook = None
    sys.modules.setdefault("clob", clob)

    gamma = types.ModuleType("gamma")
    gamma.discover_markets = None
    sys.modules.setdefault("gamma", gamma)

    class MoveTracker:
        def __init__(self, window_seconds: float = 60.0) -> None:
            self.trades: list[Trade] = []

        def add(self, trade: Trade) -> None:
            self.trades.append(trade)

        @property
        def return_pct(self) -> float | None:
            if len(self.trades) < 2:
                return None
            start = self.trades[0].price
            end = self.trades[-1].price
            return (end / start - 1.0) * 100 if start > 0 else None

    def pick_market(markets: list[Market], now: datetime, max_lookahead_s: int = 300):
        for market in markets:
            delta = (market.end_dt - now).total_seconds()
            if 30 <= delta <= max_lookahead_s:
                return market
        return None

    monitor = types.ModuleType("monitor")
    monitor.MoveTracker = MoveTracker
    monitor._pick_market = pick_market
    sys.modules.setdefault("monitor", monitor)

    class OrderbookCache:
        def start(self) -> None:
            pass

        def update_subscriptions(self, tokens: set[str]) -> None:
            pass

    orderbook_ws_cache = types.ModuleType("orderbook_ws_cache")
    orderbook_ws_cache.OrderbookCache = OrderbookCache
    sys.modules.setdefault("orderbook_ws_cache", orderbook_ws_cache)

    trader = types.ModuleType("trader")
    trader.trader_from_env = None
    sys.modules.setdefault("trader", trader)


install_import_fakes()
import polymarket.live_trader as live_trader  # noqa: E402


async def stream_from(trades: list[Trade]):
    for trade in trades:
        await asyncio.sleep(0)
        yield trade


async def quiet_task(*args, **kwargs) -> None:
    return None


class LiveTraderShimTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_places_order_and_writes_legacy_and_new_fire(self) -> None:
        market = Market()
        trades = [
            Trade(datetime(2026, 6, 21, 12, 3, tzinfo=timezone.utc), 100.0),
            Trade(datetime(2026, 6, 21, 12, 4, tzinfo=timezone.utc), 100.2),
        ]
        fake_trader = FakeTrader()
        fake_logger = FakeLogger()

        async def refresh_markets(state: dict, *args, **kwargs) -> None:
            state["markets"] = [market]

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "live.jsonl"
            with (
                patch.object(live_trader, "_book_ws_cache_enabled", return_value=False),
                patch.object(live_trader, "_refresh_markets", refresh_markets),
                patch.object(live_trader, "_watchdog", quiet_task),
                patch.object(live_trader, "stream_trades", lambda asset: stream_from(trades)),
                patch.object(live_trader, "trader_from_env", lambda: fake_trader),
                patch.object(live_trader, "get_orderbook", lambda token: Orderbook(best_ask=0.66)),
                patch("builtins.print"),
            ):
                await live_trader.run(
                    log_path=log_path,
                    threshold_pct=0.10,
                    cooldown_s=60.0,
                    sweet_lo=0.60,
                    sweet_hi=0.75,
                    require_confirm=False,
                    snipe_window_s=90.0,
                    require_window_anchor=False,
                    asset="BTC",
                    timeframe_min=5,
                    bot_logger=fake_logger,
                )

            rows = [json.loads(line) for line in log_path.read_text().splitlines()]

        self.assertEqual(fake_trader.orders, [("up-token", 0.66, 5.0)])
        self.assertEqual(len(fake_logger.boots), 1)
        self.assertEqual(fake_logger.skips, [])
        self.assertEqual(len(fake_logger.fires), 1)
        self.assertEqual(fake_logger.fires[0]["market_id"], "condition-1")
        self.assertEqual(fake_logger.fires[0]["outcome_name"], "Up")
        self.assertEqual(fake_logger.fires[0]["limit_price"], 0.66)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "trade")
        self.assertEqual(rows[0]["market_condition_id"], "condition-1")
        self.assertEqual(rows[0]["direction"], "Up")
        self.assertEqual(rows[0]["ask"], 0.66)

    async def test_run_logs_no_market_skip_without_orderbook_or_order(self) -> None:
        trades = [
            Trade(datetime(2026, 6, 21, 12, 3, tzinfo=timezone.utc), 100.0),
            Trade(datetime(2026, 6, 21, 12, 4, tzinfo=timezone.utc), 100.2),
        ]
        fake_trader = FakeTrader()
        fake_logger = FakeLogger()

        async def refresh_markets(state: dict, *args, **kwargs) -> None:
            state["markets"] = []

        def fail_get_orderbook(token: str) -> Orderbook:
            raise AssertionError("orderbook should not be fetched when no market is selected")

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "live.jsonl"
            with (
                patch.object(live_trader, "_book_ws_cache_enabled", return_value=False),
                patch.object(live_trader, "_refresh_markets", refresh_markets),
                patch.object(live_trader, "_watchdog", quiet_task),
                patch.object(live_trader, "stream_trades", lambda asset: stream_from(trades)),
                patch.object(live_trader, "trader_from_env", lambda: fake_trader),
                patch.object(live_trader, "get_orderbook", fail_get_orderbook),
                patch("builtins.print"),
            ):
                await live_trader.run(
                    log_path=log_path,
                    threshold_pct=0.10,
                    cooldown_s=60.0,
                    sweet_lo=0.60,
                    sweet_hi=0.75,
                    require_confirm=False,
                    snipe_window_s=90.0,
                    require_window_anchor=False,
                    asset="BTC",
                    timeframe_min=5,
                    bot_logger=fake_logger,
                )

            self.assertFalse(log_path.exists())

        self.assertEqual(fake_trader.orders, [])
        self.assertEqual(len(fake_logger.boots), 1)
        self.assertEqual(len(fake_logger.fires), 0)
        self.assertEqual(len(fake_logger.skips), 1)
        self.assertEqual(fake_logger.skips[0][0], "no_market_in_window")


if __name__ == "__main__":
    unittest.main()
