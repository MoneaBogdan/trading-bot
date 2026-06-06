"""Yahoo Finance candle fetcher via yfinance. No account required.

Limitations to know up front:
  - 1m candles: only the last ~7 days are available.
  - <1d intraday (5m/15m/30m/60m): only the last ~60 days.
  - 1h: up to ~730 days.
  - 1d+: many years.
  - Volume is always 0 for FX — Yahoo doesn't publish real forex volume.
  - Timestamps can occasionally drift around weekends; we normalize to UTC.

For deep M1 backtests, you'll want Dukascopy or OANDA later.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

# Maps our internal (OANDA-style) granularity strings to yfinance intervals.
GRANULARITY_MAP = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "1h",   # yfinance has no H4; we'll resample post-fetch
    "D":  "1d",
}

# yfinance hard limits on how far back each interval is queryable.
INTERVAL_MAX_DAYS = {
    "1m":  7,
    "5m":  60,
    "15m": 60,
    "30m": 60,
    "1h":  730,
    "1d":  None,
}


def to_yahoo_symbol(pair: str) -> str:
    p = pair.upper().replace("/", "").replace("_", "")
    if len(p) != 6:
        raise ValueError(f"unrecognized pair: {pair}")
    return f"{p}=X"


def fetch_candles(
    pair: str,
    granularity: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Return a DataFrame with columns: ts, open, high, low, close, volume."""
    if granularity not in GRANULARITY_MAP:
        raise ValueError(f"unsupported granularity {granularity!r}; use {list(GRANULARITY_MAP)}")

    yf_interval = GRANULARITY_MAP[granularity]
    symbol = to_yahoo_symbol(pair)
    start = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)

    # Warn (not fail) if the user asks for more history than yfinance can give.
    max_days = INTERVAL_MAX_DAYS.get(yf_interval)
    if max_days is not None:
        earliest_allowed = datetime.now(timezone.utc) - pd.Timedelta(days=max_days)
        if start < earliest_allowed:
            import warnings
            warnings.warn(
                f"Yahoo limits {yf_interval} history to ~{max_days} days; "
                f"start={start.date()} is before earliest_allowed={earliest_allowed.date()}. "
                f"Truncating start.",
                stacklevel=2,
            )
            start = earliest_allowed

    df = yf.download(
        symbol,
        start=start,
        end=end,
        interval=yf_interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    if df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

    # yfinance returns MultiIndex columns when downloading via auto_adjust=False;
    # flatten by taking the first level (the OHLCV labels).
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index().rename(columns={
        "Datetime": "ts",
        "Date": "ts",  # daily downloads use "Date" instead
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })

    # Force UTC. Daily bars from Yahoo are tz-naive; intraday come tz-aware.
    if df["ts"].dt.tz is None:
        df["ts"] = df["ts"].dt.tz_localize("UTC")
    else:
        df["ts"] = df["ts"].dt.tz_convert("UTC")

    out = df[["ts", "open", "high", "low", "close", "volume"]].copy()
    out["volume"] = out["volume"].fillna(0).astype(int)

    # Synthesize H4 from H1 if requested.
    if granularity == "H4":
        out = _resample_to_h4(out)

    return out.reset_index(drop=True)


def _resample_to_h4(h1: pd.DataFrame) -> pd.DataFrame:
    if h1.empty:
        return h1
    s = h1.set_index("ts")
    agg = s.resample("4h", origin="start_day").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    return agg.reset_index()
