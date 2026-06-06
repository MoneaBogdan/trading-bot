"""On-disk candle cache with pluggable source backends.

Sources currently supported:
  - "oanda":  requires OANDA_API_TOKEN (live or demo). Tight spreads, deep history.
  - "yahoo":  no account. Limited intraday history (60 days < 1d, 730 days for 1h).

Cache strategy is intentionally simple: we cache per (source, pair, granularity)
and re-fetch when the requested range isn't fully covered. Good enough for
backtesting; not for live tick data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

CACHE_DIR = Path(__file__).parent / "data_cache"
Source = Literal["oanda", "yahoo", "dukascopy", "binance"]


def _cache_path(source: Source, pair: str, granularity: str) -> Path:
    return CACHE_DIR / f"{source}_{pair.upper()}_{granularity}.parquet"


def load_candles(
    pair: str,
    granularity: str,
    start: datetime,
    end: datetime,
    source: Source = "yahoo",
) -> pd.DataFrame:
    """Return OHLC bars for [start, end). Cached locally; only missing ranges hit the source."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(source, pair, granularity)
    start = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)

    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    have_range = (
        not cached.empty
        and cached["ts"].min() <= pd.Timestamp(start)
        and cached["ts"].max() >= pd.Timestamp(end) - pd.Timedelta(minutes=1)
    )

    if not have_range:
        fresh = _fetch(source, pair, granularity, start, end)
        if not cached.empty:
            merged = pd.concat([cached, fresh], ignore_index=True)
            merged = merged.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
        else:
            merged = fresh
        if not merged.empty:
            merged.to_parquet(path, index=False)
        cached = merged

    if cached.empty:
        return cached
    mask = (cached["ts"] >= pd.Timestamp(start)) & (cached["ts"] < pd.Timestamp(end))
    return cached.loc[mask].reset_index(drop=True)


def _fetch(source: Source, pair: str, granularity: str, start: datetime, end: datetime) -> pd.DataFrame:
    if source == "oanda":
        from oanda import OandaClient
        return OandaClient().fetch_candles(pair, granularity, start, end)
    if source == "yahoo":
        from yahoo import fetch_candles as yf_fetch
        return yf_fetch(pair, granularity, start, end)
    if source == "dukascopy":
        from dukascopy_src import fetch_candles as dk_fetch
        return dk_fetch(pair, granularity, start, end)
    if source == "binance":
        from binance import fetch_candles as bn_fetch
        return bn_fetch(pair, granularity, start, end)
    raise ValueError(f"unknown source {source!r}")
