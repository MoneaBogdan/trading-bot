"""Binance public WebSocket: stream trades for low-latency price reference.

Public stream, no auth. Each trade includes price + size + ts. We expose a
small async iterator that yields trades; the monitor uses these to maintain
a rolling 60-second move tracker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import websockets

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"

# Map our asset codes to Binance trading symbols (USDT-quoted spot).
ASSET_TO_BINANCE_SYMBOL = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
}


@dataclass
class BinanceTrade:
    ts: datetime           # event time (UTC)
    price: float
    size: float
    is_buyer_maker: bool   # True = market sell; False = market buy


async def stream_trades(asset: str = "BTC"):
    """Async generator yielding BinanceTrade for the given asset.
    asset ∈ {"BTC", "ETH", "SOL"}. Auto-reconnects.
    """
    symbol = ASSET_TO_BINANCE_SYMBOL[asset.upper()]
    url = f"{BINANCE_WS_BASE}/{symbol}@trade"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("e") != "trade":
                        continue
                    yield BinanceTrade(
                        ts=datetime.fromtimestamp(msg["T"] / 1000, tz=timezone.utc),
                        price=float(msg["p"]),
                        size=float(msg["q"]),
                        is_buyer_maker=bool(msg["m"]),
                    )
        except Exception:
            import asyncio
            await asyncio.sleep(2.0)
            continue


async def stream_btc_trades():
    """Backward-compat alias for BTC. Use stream_trades(asset) for new code."""
    async for tr in stream_trades("BTC"):
        yield tr
