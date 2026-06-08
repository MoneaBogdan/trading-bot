"""Coinbase public WebSocket: stream BTC-USD matches.

Same shape as binance_stream so the live trader can require cross-exchange
agreement before firing. Polymarket 5-min markets settle against Chainlink
Data Streams, which aggregates multiple venues — a Binance-only signal can
diverge from the aggregate during fast moves. Requiring Coinbase confirmation
filters single-exchange spikes.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import websockets

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"


@dataclass
class CoinbaseTrade:
    ts: datetime
    price: float
    size: float
    side: str  # "buy" or "sell" — the taker side


async def stream_btc_trades():
    """Async generator yielding CoinbaseTrade. Auto-reconnects."""
    sub = json.dumps({
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["matches"],
    })
    while True:
        try:
            async with websockets.connect(COINBASE_WS_URL, ping_interval=20) as ws:
                await ws.send(sub)
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "match":
                        continue
                    yield CoinbaseTrade(
                        ts=datetime.fromisoformat(msg["time"].replace("Z", "+00:00")),
                        price=float(msg["price"]),
                        size=float(msg["size"]),
                        side=msg.get("side", ""),
                    )
        except Exception:
            await asyncio.sleep(2.0)
            continue
