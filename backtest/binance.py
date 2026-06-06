"""Binance public klines (candles) fetcher.

No auth required for historical data. Symbol format is Binance-native
(BTCUSDT, ETHUSDT, SOLUSDT, etc.) — we accept it as-is, no conversion.

Quirks vs FX adapters:
  - Crypto trades 24/7 — no weekend gaps. Annualization constants in
    metrics.py assume continuous time; that's actually closer to reality
    for crypto than for FX.
  - Volume here is BASE asset volume (e.g. BTC), not USD notional. The
    quote volume is available but we don't use it.
  - 1500-bar limit per request, so we paginate by interval.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pandas as pd

BINANCE_URL = "https://api.binance.com/api/v3/klines"
MAX_BARS = 1500

# Map our internal granularity strings to Binance interval codes.
GRANULARITY_MAP = {
    "M1":  "1m",
    "M5":  "5m",
    "M15": "15m",
    "M30": "30m",
    "H1":  "1h",
    "H4":  "4h",
    "D":   "1d",
}


def to_binance_symbol(pair: str) -> str:
    """Accept BTCUSDT / btc_usdt / BTC/USDT etc. and return the canonical form."""
    return pair.upper().replace("/", "").replace("_", "")


def fetch_candles(
    pair: str,
    granularity: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    if granularity not in GRANULARITY_MAP:
        raise ValueError(f"unsupported granularity {granularity!r}; use one of {list(GRANULARITY_MAP)}")
    symbol = to_binance_symbol(pair)
    interval = GRANULARITY_MAP[granularity]

    start = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)

    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    rows: list[dict] = []
    cursor = start_ms

    with httpx.Client(timeout=30.0) as client:
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": MAX_BARS,
            }
            resp = client.get(BINANCE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break

            for kline in data:
                # Each kline:
                #   [openTime, open, high, low, close, volume, closeTime, ...]
                rows.append({
                    "ts": pd.Timestamp(kline[0], unit="ms", tz="UTC"),
                    "open": float(kline[1]),
                    "high": float(kline[2]),
                    "low": float(kline[3]),
                    "close": float(kline[4]),
                    "volume": float(kline[5]),
                })

            # Advance past the last kline's CLOSE time so we don't re-fetch it.
            # Binance's effective max page size can be <MAX_BARS, so we cannot
            # use `len(data) < MAX_BARS` to detect the last page — we must
            # rely on the cursor reaching end_ms or zero new data.
            last_close_ms = data[-1][6]  # closeTime
            new_cursor = last_close_ms + 1
            if new_cursor <= cursor:
                break  # safety stop (shouldn't happen)
            cursor = new_cursor

    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    out = pd.DataFrame(rows)
    out["volume"] = out["volume"].astype(float)
    out = out.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return out
