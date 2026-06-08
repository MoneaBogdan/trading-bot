"""Fetch Binance BTCUSDT 1-second candles for the backtest window.

1s is fine-enough granularity to replay our 60s-rolling-return signal generation
with near-tick fidelity — vastly higher resolution than the 1m candles the
previous backtest used. ~86,400 candles/day, ~200MB for 30 days.

We paginate by `startTime` in 1000-candle chunks (~16.6 minutes per chunk).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

BINANCE = "https://api.binance.com/api/v3/klines"
CHUNK = 1000  # candles per request
CACHE_DIR = Path(__file__).parent / "cache"


def fetch_1s(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows: list[dict] = []
    cursor = start_ms
    n_calls = 0
    t0 = time.time()
    with httpx.Client(timeout=30.0) as client:
        while cursor < end_ms:
            r = client.get(BINANCE, params={
                "symbol": symbol, "interval": "1s",
                "startTime": cursor, "endTime": end_ms,
                "limit": CHUNK,
            })
            if r.status_code == 429:
                print(f"  [rate-limited] sleep 30s")
                time.sleep(30); continue
            r.raise_for_status()
            data = r.json()
            n_calls += 1
            if not data:
                break
            for k in data:
                rows.append({"ts": int(k[0]) // 1000, "open": float(k[1]),
                             "high": float(k[2]), "low": float(k[3]),
                             "close": float(k[4]), "volume": float(k[5])})
            cursor = data[-1][6] + 1  # use closeTime + 1ms
            if n_calls % 50 == 0:
                elapsed = time.time() - t0
                cur_t = datetime.fromtimestamp(cursor / 1000, tz=timezone.utc)
                progress = (cursor - start_ms) / (end_ms - start_ms) * 100
                print(f"  [{n_calls} calls] cursor={cur_t:%Y-%m-%d %H:%M}  "
                      f"{progress:.1f}%  ({len(rows):,} candles, {len(rows)/elapsed:.0f}/s)")
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets-file", required=True)
    ap.add_argument("--lookback-min", type=int, default=20)
    args = ap.parse_args()

    with open(args.markets_file) as f:
        markets = json.load(f)
    earliest = min(m["end_ts"] for m in markets) - args.lookback_min * 60
    latest = max(m["end_ts"] for m in markets) + 60
    start = datetime.fromtimestamp(earliest, tz=timezone.utc)
    end = datetime.fromtimestamp(latest, tz=timezone.utc)
    days = (end - start).total_seconds() / 86400
    print(f"[btc-1s] fetching BTCUSDT 1s from {start} to {end}  ({days:.1f} days)")
    print(f"[btc-1s] expected ~{int(days * 86400):,} candles in ~{int(days * 86400 / CHUNK)} requests")

    df = fetch_1s("BTCUSDT", earliest * 1000, latest * 1000)
    print(f"[btc-1s] fetched {len(df):,} candles")

    CACHE_DIR.mkdir(exist_ok=True)
    out = CACHE_DIR / f"btc_1s_{start:%Y%m%d}_{end:%Y%m%d}.parquet"
    df.to_parquet(out, index=False)
    sz = out.stat().st_size / 1e6
    print(f"[done] wrote {out}  ({sz:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
