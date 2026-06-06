"""Dukascopy candle fetcher via dukascopy-python.

Dukascopy publishes ~20 years of free forex tick + bar data from their bank's
own feed. No account, no key. The Python library handles the bi5 binary format
for us. Note: it caps each call to 30k bars, so we paginate by month chunks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import dukascopy_python as dp
import pandas as pd
from dukascopy_python import instruments as ins

# Map internal granularity to dukascopy-python interval constants.
GRANULARITY_MAP = {
    "M1":  dp.INTERVAL_MIN_1,
    "M5":  dp.INTERVAL_MIN_5,
    "M15": dp.INTERVAL_MIN_15,
    "M30": dp.INTERVAL_MIN_30,
    "H1":  dp.INTERVAL_HOUR_1,
    "H4":  dp.INTERVAL_HOUR_4,
    "D":   dp.INTERVAL_DAY_1,
}

# Approximate chunk size (days) per granularity so we stay under the 30k-bar cap.
# 24*60 = 1440 M1 bars/day → 30k limit ≈ 20 days. Use 14 to be safe.
CHUNK_DAYS = {
    "M1": 14,
    "M5": 60,
    "M15": 180,
    "M30": 365,
    "H1": 365 * 2,
    "H4": 365 * 5,
    "D":  365 * 20,
}

# Common pair → Dukascopy instrument constant.
PAIR_MAP = {
    "EURUSD": ins.INSTRUMENT_FX_MAJORS_EUR_USD,
    "GBPUSD": ins.INSTRUMENT_FX_MAJORS_GBP_USD,
    "USDJPY": ins.INSTRUMENT_FX_MAJORS_USD_JPY,
    "AUDUSD": ins.INSTRUMENT_FX_MAJORS_AUD_USD,
    "USDCAD": ins.INSTRUMENT_FX_MAJORS_USD_CAD,
    "USDCHF": ins.INSTRUMENT_FX_MAJORS_USD_CHF,
    "NZDUSD": ins.INSTRUMENT_FX_MAJORS_NZD_USD,
}


def to_dukascopy_instrument(pair: str) -> str:
    p = pair.upper().replace("/", "").replace("_", "")
    if p not in PAIR_MAP:
        raise ValueError(f"pair {pair!r} not mapped; add to PAIR_MAP. Available: {sorted(PAIR_MAP)}")
    return PAIR_MAP[p]


def fetch_candles(
    pair: str,
    granularity: str,
    start: datetime,
    end: datetime,
    offer_side: str = dp.OFFER_SIDE_BID,
) -> pd.DataFrame:
    if granularity not in GRANULARITY_MAP:
        raise ValueError(f"unsupported granularity {granularity!r}; use one of {list(GRANULARITY_MAP)}")
    instrument = to_dukascopy_instrument(pair)
    interval = GRANULARITY_MAP[granularity]

    start = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)

    chunk_days = CHUNK_DAYS[granularity]
    frames: list[pd.DataFrame] = []
    cursor = start

    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        # dukascopy-python expects tz-naive datetimes representing UTC.
        df = dp.fetch(
            instrument=instrument,
            interval=interval,
            offer_side=offer_side,
            start=cursor.replace(tzinfo=None),
            end=chunk_end.replace(tzinfo=None),
        )
        if df is not None and not df.empty:
            frames.append(df)
        cursor = chunk_end

    if not frames:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

    out = pd.concat(frames)
    # The lib returns a DataFrame indexed by timestamp with cols
    # ['open','high','low','close','volume']. Normalize to our schema.
    out = out.reset_index().rename(columns={
        "timestamp": "ts",
        "index": "ts",
    })
    ts_col = "ts" if "ts" in out.columns else out.columns[0]
    if ts_col != "ts":
        out = out.rename(columns={ts_col: "ts"})

    if out["ts"].dt.tz is None:
        out["ts"] = out["ts"].dt.tz_localize("UTC")
    else:
        out["ts"] = out["ts"].dt.tz_convert("UTC")

    out = out[["ts", "open", "high", "low", "close", "volume"]].copy()
    out["volume"] = out["volume"].fillna(0).astype(int)
    out = out.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return out
