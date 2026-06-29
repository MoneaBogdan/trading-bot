"""Supervisor-managed shadow latency-arb bot.

Phase D safety rule: this runner exercises StreamBus + BotRuntime + Supervisor
without placing orders. It consumes public market data and logs what the
extracted latency-arb strategy would have done.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "polymarket"))

from src.core.bot import BotRuntime, BotSpec
from src.core.logger import BotLogger
from src.core.stream_bus import DropPolicy, StreamBus, stream_spec
from src.core.supervisor import DataSourceSpec, Supervisor
from src.strategies.polymarket_latency_arb import (
    BookEvent,
    BookRequest,
    Ignore,
    OrderIntent,
    Params,
    SignalEvent,
    Skip,
    State,
    decide_prebook,
    decide_signal_gate,
    decide_with_book,
)


@dataclass(frozen=True)
class MarketListUpdate:
    ts: datetime
    asset: str
    timeframe_min: int
    markets: tuple[Any, ...]


class RollingReturn:
    def __init__(self, window_s: float = 60.0):
        self.window = timedelta(seconds=window_s)
        self._trades: deque[Any] = deque()

    def add(self, trade: Any) -> None:
        self._trades.append(trade)
        cutoff = trade.ts - self.window
        while self._trades and self._trades[0].ts < cutoff:
            self._trades.popleft()

    @property
    def return_pct(self) -> float | None:
        if len(self._trades) < 2:
            return None
        start = self._trades[0].price
        end = self._trades[-1].price
        return (end / start - 1.0) * 100 if start > 0 else None


class PriceHistory:
    def __init__(self, window_s: float):
        self.window = timedelta(seconds=window_s)
        self._trades: deque[Any] = deque()

    def add(self, trade: Any) -> None:
        self._trades.append(trade)
        cutoff = trade.ts - self.window
        while self._trades and self._trades[0].ts < cutoff:
            self._trades.popleft()

    def price_at(self, ts: datetime) -> float | None:
        if not self._trades or self._trades[0].ts > ts:
            return None
        price = None
        for trade in self._trades:
            if trade.ts <= ts:
                price = trade.price
            else:
                break
        return price


def params_from_env() -> Params:
    return Params(
        threshold_pct=float(os.environ.get("THRESHOLD", "0.13")),
        cooldown_s=float(os.environ.get("COOLDOWN", "60")),
        sweet_lo=float(os.environ.get("SWEET_LO", "0.60")),
        sweet_hi=float(os.environ.get("SWEET_HI", "0.75")),
        require_confirm=os.environ.get("REQUIRE_CONFIRM", "1").strip().lower() in ("1", "true", "yes", "on"),
        snipe_window_s=float(os.environ.get("SNIPE_WINDOW_S", "90")),
        require_window_anchor=os.environ.get("REQUIRE_WINDOW_ANCHOR", "0").strip().lower() in ("1", "true", "yes", "on"),
        asset=os.environ.get("ASSET", "ETH").upper(),
        timeframe_min=int(os.environ.get("TIMEFRAME_MIN", "5")),
    )


def bot_name(params: Params) -> str:
    suffix = os.environ.get("VARIANT_SUFFIX", "via-supervisor").strip()
    base = f"{params.asset.lower()}-{params.timeframe_min}m"
    return f"{base}-{suffix}" if suffix else base


def pick_market(markets: tuple[Any, ...], now: datetime, max_lookahead_s: int) -> Any | None:
    for market in markets:
        delta = (market.end_dt - now).total_seconds()
        if 30 <= delta <= max_lookahead_s:
            return market
    return None


def best_ask(orderbook: Any) -> float | None:
    value = getattr(orderbook, "best_ask", None)
    if value is not None:
        return value
    asks = getattr(orderbook, "asks", None)
    if asks:
        first = asks[0]
        if hasattr(first, "price"):
            return float(first.price)
        return float(first[0])
    return None


class LatencyArbShadowBot:
    def __init__(
        self,
        *,
        params: Params,
        logger: BotLogger,
        orderbook_fetcher: Callable[[str], Any],
        size_usdc: float,
    ):
        self.params = params
        self.logger = logger
        self.orderbook_fetcher = orderbook_fetcher
        self.size_usdc = size_usdc
        self.state = State()
        self.binance = RollingReturn()
        self.coinbase = RollingReturn()
        self.history = PriceHistory(window_s=float(params.timeframe_min * 60 + 60))
        self.markets: tuple[Any, ...] = ()

    def boot(self) -> None:
        self.logger.boot(
            **asdict(self.params),
            size_usdc=self.size_usdc,
            shadow=True,
            execution="none",
        )

    async def on_event(self, event: Any) -> None:
        if isinstance(event, MarketListUpdate):
            self.markets = event.markets
            return
        venue = getattr(event, "venue", "")
        if venue == "coinbase":
            self.coinbase.add(event)
            return
        if venue != "binance":
            return
        await self._on_binance_trade(event)

    async def _on_binance_trade(self, trade: Any) -> None:
        self.binance.add(trade)
        self.history.add(trade)
        ret = self.binance.return_pct
        cb_ret = self.coinbase.return_pct if self.params.require_confirm else None
        signal = SignalEvent(
            ts=trade.ts,
            price=trade.price,
            ret_60s_pct=ret,
            cb_ret_60s_pct=cb_ret,
            market=None,
        )
        gate = decide_signal_gate(self.state, signal, self.params)
        if isinstance(gate, Ignore):
            return
        if isinstance(gate, Skip):
            self.logger.skip(gate.reason, ts=trade.ts, **gate.debug, shadow=True)
            return

        market = pick_market(self.markets, trade.ts, int(self.params.snipe_window_s))
        window_open_price = None
        if market is not None and self.params.require_window_anchor:
            window_open = market.end_dt - timedelta(seconds=self.params.timeframe_min * 60)
            window_open_price = self.history.price_at(window_open)

        prebook = decide_prebook(
            self.state,
            SignalEvent(
                ts=trade.ts,
                price=trade.price,
                ret_60s_pct=ret,
                cb_ret_60s_pct=cb_ret,
                market=market,
                window_open_price=window_open_price,
            ),
            self.params,
        )
        if isinstance(prebook, Ignore):
            return
        if isinstance(prebook, Skip):
            self.logger.skip(prebook.reason, ts=trade.ts, **prebook.debug, shadow=True)
            return
        if not isinstance(prebook, BookRequest):
            raise TypeError(f"unexpected prebook decision {prebook!r}")

        loop = asyncio.get_running_loop()
        orderbook = await loop.run_in_executor(None, self.orderbook_fetcher, prebook.target_token)
        book_decision = decide_with_book(
            self.state,
            BookEvent(
                ts=trade.ts,
                ret_60s_pct=ret,
                market=prebook.market,
                direction=prebook.direction,
                target_token=prebook.target_token,
                ask=best_ask(orderbook),
            ),
            self.params,
        )
        if isinstance(book_decision, Skip):
            self.logger.skip(book_decision.reason, ts=trade.ts, **book_decision.debug, shadow=True)
            return
        if not isinstance(book_decision, OrderIntent):
            raise TypeError(f"unexpected book decision {book_decision!r}")

        self.logger.fire(
            ts=trade.ts,
            intent_id=str(uuid4()),
            venue=book_decision.venue,
            market_id=book_decision.market_id,
            market_title=book_decision.market_title,
            outcome_name=book_decision.outcome_name,
            side=book_decision.side,
            order_type=book_decision.order_type,
            size_usdc=self.size_usdc,
            limit_price=book_decision.limit_price,
            filled_size=None,
            filled_price=None,
            cost_usdc=None,
            order_ok=False,
            order_id=None,
            dry_run=True,
            shadow=True,
            ret_60s_pct=ret,
            underlying_price=trade.price,
        )
        self.state.mark_signal(trade.ts)


async def binance_source(bus: StreamBus, asset: str) -> None:
    from binance_stream import stream_trades

    async for trade in stream_trades(asset):
        trade.venue = "binance"
        await bus.publish(
            stream_spec("price", venue="binance", asset=asset),
            trade,
            drop_policy=DropPolicy.DROP_OLDEST,
        )


async def coinbase_source(bus: StreamBus, asset: str) -> None:
    from coinbase_stream import stream_trades

    async for trade in stream_trades(asset):
        trade.venue = "coinbase"
        await bus.publish(
            stream_spec("price", venue="coinbase", asset=asset),
            trade,
            drop_policy=DropPolicy.DROP_OLDEST,
        )


async def gamma_market_source(bus: StreamBus, asset: str, timeframe_min: int, interval_s: float) -> None:
    from gamma import discover_markets

    while True:
        try:
            markets = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: discover_markets(
                    asset=asset,
                    timeframe_min=timeframe_min,
                    window_horizon_min=max(10, timeframe_min * 2),
                ),
            )
            await bus.publish(
                stream_spec("markets", venue="polymarket", asset=asset, timeframe_min=timeframe_min),
                MarketListUpdate(
                    ts=datetime.now(timezone.utc),
                    asset=asset,
                    timeframe_min=timeframe_min,
                    markets=tuple(markets),
                ),
            )
        except Exception as exc:
            print(f"[shadow-supervisor] market refresh failed: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(interval_s)


def default_orderbook_fetcher(token_id: str) -> Any:
    from clob import get_orderbook

    return get_orderbook(token_id)


async def run() -> None:
    params = params_from_env()
    name = os.environ.get("SUPERVISOR_SHADOW_BOT_NAME") or bot_name(params)
    log_dir = Path(os.environ.get("SUPERVISOR_SHADOW_LOG_DIR", "polymarket/logs"))
    size_usdc = float(os.environ.get("POLY_MAX_ORDER_USDC", "5"))
    logger = BotLogger(bot=name, strategy="PolymarketLatencyArbSupervisorShadow", base_dir=log_dir)
    shadow_bot = LatencyArbShadowBot(
        params=params,
        logger=logger,
        orderbook_fetcher=default_orderbook_fetcher,
        size_usdc=size_usdc,
    )
    shadow_bot.boot()

    supervisor = Supervisor()
    supervisor.add_bot(
        BotRuntime(
            BotSpec(
                name=name,
                subscriptions=(
                    stream_spec("price", venue="binance", asset=params.asset),
                    stream_spec("markets", venue="polymarket", asset=params.asset, timeframe_min=params.timeframe_min),
                    stream_spec("price", venue="coinbase", asset=params.asset),
                ),
            ),
            shadow_bot.on_event,
        )
    )
    supervisor.add_source(
        DataSourceSpec(
            name=f"binance-{params.asset.lower()}",
            streams=(stream_spec("price", venue="binance", asset=params.asset),),
            run=lambda bus: binance_source(bus, params.asset),
        )
    )
    if params.require_confirm:
        supervisor.add_source(
            DataSourceSpec(
                name=f"coinbase-{params.asset.lower()}",
                streams=(stream_spec("price", venue="coinbase", asset=params.asset),),
                run=lambda bus: coinbase_source(bus, params.asset),
            )
        )
    supervisor.add_source(
        DataSourceSpec(
            name=f"gamma-{params.asset.lower()}-{params.timeframe_min}m",
            streams=(stream_spec("markets", venue="polymarket", asset=params.asset, timeframe_min=params.timeframe_min),),
            run=lambda bus: gamma_market_source(
                bus,
                params.asset,
                params.timeframe_min,
                float(os.environ.get("MARKET_REFRESH_S", "60")),
            ),
        )
    )
    print(
        f"[shadow-supervisor] bot={name} asset={params.asset} timeframe={params.timeframe_min}m "
        f"threshold={params.threshold_pct} sweet=[{params.sweet_lo},{params.sweet_hi}] "
        f"confirm={params.require_confirm} execution=none",
        flush=True,
    )
    await supervisor.run()


def main() -> int:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[shadow-supervisor] stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
