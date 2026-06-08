"""Replay engine: simulate monitor.py decisions over cached historical data.

Reads:
  - cache/markets_<from>_<to>.json — closed market metadata + winners
  - cache/prices/*.json — per-market UP/DOWN price-history at 1-min fidelity
  - cache/btc_1m_<from>_<to>.parquet — Binance BTC 1-min candles

Decision logic (mirrors live monitor.py):
  At every minute, compute the BTC 1-minute return. If |ret| >= threshold:
    - Find the market ending within max_lookahead_s seconds.
    - side = UP if ret > 0 else DOWN
    - entry = that side's mid price at the same minute
    - cooldown next N seconds
  When market resolves: payoff = (1 - entry) if winner == side else -entry

Outputs: trades.jsonl (one row per simulated trade) + summary print.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
CACHE_DIR = ROOT / "cache"
PRICES_DIR = CACHE_DIR / "prices"


@dataclass
class Trade:
    market_id: str
    title: str
    signal_ts: int          # minute of decision (epoch s)
    market_end_ts: int      # window close time
    seconds_to_close: int
    btc_60s_ret_pct: float
    side: str               # "UP" | "DOWN"
    entry_price: float
    winner: str
    correct: bool
    payoff_per_unit: float  # (1 - entry) if win else -entry


def _price_at(series: list[dict], ts: int) -> float | None:
    """Find the price-history point closest to ts (within 90s tolerance)."""
    if not series:
        return None
    ts_list = [p["t"] for p in series]
    idx = bisect.bisect_left(ts_list, ts)
    candidates = []
    if idx < len(series):
        candidates.append(series[idx])
    if idx > 0:
        candidates.append(series[idx - 1])
    best = min(candidates, key=lambda p: abs(p["t"] - ts))
    if abs(best["t"] - ts) > 90:
        return None
    return float(best["p"])


def load_markets_index(markets_file: Path) -> list[dict]:
    """Markets sorted by end_ts ascending (needed for upcoming-market lookup)."""
    with markets_file.open() as f:
        ms = json.load(f)
    return sorted(ms, key=lambda m: m["end_ts"])


def find_upcoming_market(markets: list[dict], now_ts: int,
                        max_lookahead_s: int) -> dict | None:
    """Earliest market whose end_ts is in (now_ts, now_ts + max_lookahead_s]."""
    end_times = [m["end_ts"] for m in markets]
    idx = bisect.bisect_right(end_times, now_ts)
    if idx >= len(markets):
        return None
    mk = markets[idx]
    delta = mk["end_ts"] - now_ts
    if delta <= 0 or delta > max_lookahead_s:
        return None
    return mk


def load_price_payload(cond_id: str) -> dict | None:
    p = PRICES_DIR / f"{cond_id}.json"
    if not p.exists():
        return None
    with p.open() as f:
        return json.load(f)


def replay(markets: list[dict], btc: pd.DataFrame, threshold_pct: float,
          cooldown_s: int, max_lookahead_s: int, min_seconds_to_close: int,
          entry_lag_s: int = 0) -> list[Trade]:
    """Walk forward through BTC candles, emit signals + outcomes."""
    closes = btc["close"].astype(float).values
    # pd Timestamp → epoch seconds (robust to datetime64 unit being [ms] or [ns])
    # Robust conversion to epoch seconds — astype('int64') returns the raw value
    # in the dtype's native unit (ms or ns), so we use .apply for correctness.
    timestamps = btc["ts"].apply(lambda x: int(x.timestamp())).values

    last_signal_ts = 0
    trades: list[Trade] = []
    skipped_no_market = skipped_no_price = skipped_cooldown = skipped_too_close = 0

    for i in range(1, len(closes)):
        ret_pct = (closes[i] / closes[i - 1] - 1) * 100
        if abs(ret_pct) < threshold_pct:
            continue
        now_ts = int(timestamps[i])
        if now_ts - last_signal_ts < cooldown_s:
            skipped_cooldown += 1
            continue
        mk = find_upcoming_market(markets, now_ts, max_lookahead_s)
        if not mk:
            skipped_no_market += 1
            continue
        delta = mk["end_ts"] - now_ts
        if delta < min_seconds_to_close:
            skipped_too_close += 1
            continue
        payload = load_price_payload(mk["condition_id"])
        if not payload:
            skipped_no_price += 1
            continue
        side = "UP" if ret_pct > 0 else "DOWN"
        series = payload["up_series"] if side == "UP" else payload["down_series"]
        entry = _price_at(series, now_ts + entry_lag_s)
        if entry is None:
            skipped_no_price += 1
            continue
        correct = (mk["winner"] == side)
        payoff = (1.0 - entry) if correct else -entry
        trades.append(Trade(
            market_id=mk["condition_id"],
            title=mk["title"],
            signal_ts=now_ts,
            market_end_ts=mk["end_ts"],
            seconds_to_close=delta,
            btc_60s_ret_pct=ret_pct,
            side=side,
            entry_price=entry,
            winner=mk["winner"],
            correct=correct,
            payoff_per_unit=payoff,
        ))
        last_signal_ts = now_ts

    print(f"[replay] {len(trades)} trades  "
          f"(skipped: no_market={skipped_no_market}, no_price={skipped_no_price}, "
          f"cooldown={skipped_cooldown}, too_close={skipped_too_close})")
    return trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets-file", required=True)
    ap.add_argument("--btc-file", required=True)
    ap.add_argument("--threshold", type=float, default=0.10,
                    help="BTC 60s return threshold in percent (default 0.10)")
    ap.add_argument("--cooldown", type=int, default=60)
    ap.add_argument("--max-lookahead", type=int, default=300)
    ap.add_argument("--min-seconds-to-close", type=int, default=30)
    ap.add_argument("--entry-lag", type=int, default=0,
                    help="seconds to add to signal_ts before looking up entry price "
                         "(simulates latency between BTC observation and Polymarket fill)")
    ap.add_argument("--out", default="cache/trades.jsonl")
    args = ap.parse_args()

    markets = load_markets_index(Path(args.markets_file))
    btc = pd.read_parquet(args.btc_file)
    print(f"[replay] {len(markets)} markets, {len(btc)} BTC candles, threshold={args.threshold}%")

    trades = replay(markets, btc, args.threshold, args.cooldown, args.max_lookahead,
                    args.min_seconds_to_close, entry_lag_s=args.entry_lag)

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True, parents=True)
    with out.open("w") as f:
        for t in trades:
            f.write(json.dumps(asdict(t)) + "\n")
    print(f"[done] wrote {len(trades)} trades to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
