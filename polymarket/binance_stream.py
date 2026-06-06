"""Binance public WebSocket: stream BTC trades for low-latency price reference.

Public stream, no auth. Each trade includes price + size + ts. We expose a
small async iterator that yields trades; the monitor uses these to maintain
a rolling 60-second move tracker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import websockets

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"


@dataclass
class BinanceTrade:
    ts: datetime           # event time (UTC)
    price: float
    size: float
    is_buyer_maker: bool   # True = market sell; False = market buy


async def stream_btc_trades():
    """Async generator yielding BinanceTrade objects. Auto-reconnects on disconnect."""
    while True:
        try:
            async with websockets.connect(BINANCE_WS_URL, ping_interval=20) as ws:
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
        except Exception as exc:
            # Brief backoff before reconnect.
            import asyncio
            await asyncio.sleep(2.0)
            continue
