"""Fetch Binance 1-minute BTCUSDT klines spanning all market windows.

We pull one contiguous range covering all markets in the cache rather than
per-market windows — 1m candles are cheap and contiguous storage makes the
replay engine simpler. Output: cache/btc_1m_<from>_<to>.parquet.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Reuse the project's Binance fetcher
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backtest"))
from binance import fetch_candles  # noqa: E402

CACHE_DIR = Path(__file__).parent / "cache"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets-file", required=True)
    ap.add_argument("--lookback-min", type=int, default=20,
                    help="extra minutes BEFORE earliest market end_ts to fetch")
    args = ap.parse_args()

    with open(args.markets_file) as f:
        markets = json.load(f)
    if not markets:
        print("no markets")
        return 1

    earliest = min(m["end_ts"] for m in markets) - args.lookback_min * 60
    latest = max(m["end_ts"] for m in markets) + 60
    start = datetime.fromtimestamp(earliest, tz=timezone.utc)
    end = datetime.fromtimestamp(latest, tz=timezone.utc)
    print(f"[btc] fetching BTCUSDT 1m from {start} to {end}  ({(end-start).total_seconds()/3600:.1f}h)")

    df = fetch_candles("BTCUSDT", "M1", start, end)
    print(f"[btc] {len(df)} candles  ({df.iloc[0]['ts']} → {df.iloc[-1]['ts']})")

    CACHE_DIR.mkdir(exist_ok=True)
    out = CACHE_DIR / f"btc_1m_{start:%Y%m%d}_{end:%Y%m%d}.parquet"
    df.to_parquet(out, index=False)
    print(f"[done] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
