"""Tick-aligned replay — uses 1-second BTC candles + identical MoveTracker
algorithm to live, eliminating the timing mismatch we identified.

Differences from replay_trades.py (the previous, lower-fidelity backtest):
  - BTC source: 1-second candles (was 1-minute)
  - Signal generation: rolling 60s return on every 1s tick (was 1-min close-to-close)
  - No "no_fill" filter — every sweet-spot signal counts, mirroring live
  - Same cooldown rule as live (after ANY signal)
  - Entry price still from trade tape (Path B includes WS orderbook for forward
    data, but historical replay falls back to trade tape with this caveat)
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
CACHE_DIR = ROOT / "cache"
TRADES_DIR = CACHE_DIR / "trades"


@dataclass
class TickTrade:
    market_id: str
    title: str
    signal_ts: int
    fill_ts: int
    fill_latency_s: int
    market_end_ts: int
    seconds_to_close: int
    btc_60s_ret_pct: float
    btc_price_at_signal: float
    side: str
    entry_price: float | None
    winner: str
    correct: bool | None
    payoff_per_unit: float | None
    skip_reason: str | None  # None = traded; otherwise tells us why we couldn't


class MoveTrackerSim:
    """Mirror of live monitor.py MoveTracker, replaying 1s candles as ticks.

    Each candle becomes a single tick at its close timestamp + close price.
    """

    def __init__(self, window_s: int = 60):
        self.window_s = window_s
        self._deque: deque[tuple[int, float]] = deque()  # (epoch_s, price)

    def add(self, ts: int, price: float) -> None:
        self._deque.append((ts, price))
        cutoff = ts - self.window_s
        while self._deque and self._deque[0][0] < cutoff:
            self._deque.popleft()

    @property
    def return_pct(self) -> float | None:
        if len(self._deque) < 2:
            return None
        start = self._deque[0][1]
        end = self._deque[-1][1]
        if start <= 0:
            return None
        return (end / start - 1.0) * 100


def find_upcoming_market(markets: list[dict], end_times: list[int], now_ts: int,
                        max_lookahead_s: int, min_secs_to_close: int) -> dict | None:
    idx = bisect.bisect_right(end_times, now_ts)
    if idx >= len(markets):
        return None
    mk = markets[idx]
    delta = mk["end_ts"] - now_ts
    if delta < min_secs_to_close or delta > max_lookahead_s:
        return None
    return mk


def find_next_fill(trades: list[dict], target_token: str, after_ts: int,
                   side_filter: str = "BUY", max_wait_s: int = 60) -> tuple[float, int] | None:
    """First trade for target_token, side=side_filter, ts >= after_ts."""
    for tr in trades:
        ts = tr.get("timestamp", 0)
        if ts < after_ts:
            continue
        if ts - after_ts > max_wait_s:
            return None
        if tr.get("asset") != target_token:
            continue
        if side_filter and tr.get("side") != side_filter:
            continue
        return float(tr["price"]), int(ts)
    return None


def load_tape(cond_id: str) -> dict | None:
    p = TRADES_DIR / f"{cond_id}.json"
    if not p.exists():
        return None
    with p.open() as f:
        return json.load(f)


def replay(markets: list[dict], btc_1s: pd.DataFrame, threshold_pct: float,
          cooldown_s: int, max_lookahead_s: int, min_secs_to_close: int,
          min_latency_s: int, sweet_lo: float, sweet_hi: float,
          fee_bps: float = 0) -> list[TickTrade]:
    closes = btc_1s["close"].astype(float).values
    timestamps = btc_1s["ts"].astype(int).values
    end_times = [m["end_ts"] for m in markets]

    tracker = MoveTrackerSim(window_s=60)
    last_signal_ts = 0
    trades: list[TickTrade] = []
    stats = {"signals_fired": 0, "skip_no_market": 0, "skip_outside_sweet": 0,
             "skip_no_tape": 0, "skip_no_fill": 0, "skip_cooldown": 0}

    for i in range(len(closes)):
        now_ts = int(timestamps[i])
        price = float(closes[i])
        tracker.add(now_ts, price)
        ret = tracker.return_pct
        if ret is None or abs(ret) < threshold_pct:
            continue
        if now_ts - last_signal_ts < cooldown_s:
            stats["skip_cooldown"] += 1
            continue
        stats["signals_fired"] += 1
        mk = find_upcoming_market(markets, end_times, now_ts, max_lookahead_s, min_secs_to_close)
        if not mk:
            last_signal_ts = now_ts
            stats["skip_no_market"] += 1
            continue
        side = "UP" if ret > 0 else "DOWN"
        target_token = mk["up_token_id"] if side == "Up" else mk["down_token_id"]

        # Look up the entry ask from trade tape — first BUY print at signal_ts+latency
        tape = load_tape(mk["condition_id"])
        entry_price = None
        fill_ts = now_ts + min_latency_s
        if tape:
            fill = find_next_fill(tape["trades"], target_token,
                                  now_ts + min_latency_s, "BUY", max_wait_s=60)
            if fill:
                entry_price, fill_ts = fill

        # CRITICAL DIFFERENCE FROM OLD BACKTEST: we don't skip if no fill found.
        # Instead we record the signal with skip_reason="no_fill" — same as live
        # which would have tried to lift the ask regardless of historical prints.
        skip_reason = None
        in_sweet = False
        if entry_price is None:
            skip_reason = "no_fill_in_tape"
        else:
            in_sweet = sweet_lo <= entry_price <= sweet_hi
            if not in_sweet:
                skip_reason = f"outside_sweet({entry_price:.2f})"

        winner = mk["winner"]
        if entry_price is not None and in_sweet:
            correct = (winner == side)
            entry_after_fee = entry_price * (1 + fee_bps / 10_000)
            payoff = (1.0 - entry_after_fee) if correct else -entry_after_fee
        else:
            correct = None
            payoff = None
            if skip_reason and "outside_sweet" in skip_reason:
                stats["skip_outside_sweet"] += 1
            elif skip_reason == "no_fill_in_tape":
                stats["skip_no_tape"] += 1

        trades.append(TickTrade(
            market_id=mk["condition_id"],
            title=mk["title"],
            signal_ts=now_ts,
            fill_ts=fill_ts,
            fill_latency_s=fill_ts - now_ts,
            market_end_ts=mk["end_ts"],
            seconds_to_close=mk["end_ts"] - now_ts,
            btc_60s_ret_pct=ret,
            btc_price_at_signal=price,
            side=side,
            entry_price=entry_price,
            winner=winner,
            correct=correct,
            payoff_per_unit=payoff,
            skip_reason=skip_reason,
        ))
        last_signal_ts = now_ts

    print(f"[replay] {stats['signals_fired']} signals fired")
    print(f"  cooldown skips:         {stats['skip_cooldown']}")
    print(f"  no upcoming market:     {stats['skip_no_market']}")
    print(f"  outside sweet spot:     {stats['skip_outside_sweet']}")
    print(f"  no fillable BUY in tape:{stats['skip_no_tape']}")
    return trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets-file", required=True)
    ap.add_argument("--btc-file", required=True)
    ap.add_argument("--threshold", type=float, default=0.10)
    ap.add_argument("--cooldown", type=int, default=60)
    ap.add_argument("--max-lookahead", type=int, default=300)
    ap.add_argument("--min-seconds-to-close", type=int, default=30)
    ap.add_argument("--min-latency", type=int, default=5)
    ap.add_argument("--sweet-lo", type=float, default=0.30)
    ap.add_argument("--sweet-hi", type=float, default=0.70)
    ap.add_argument("--fee-bps", type=float, default=200)
    ap.add_argument("--out", default="cache/tick_replay.jsonl")
    args = ap.parse_args()

    with open(args.markets_file) as f:
        markets = json.load(f)
    markets.sort(key=lambda m: m["end_ts"])
    btc = pd.read_parquet(args.btc_file)
    print(f"[replay] {len(markets)} markets, {len(btc):,} BTC 1s candles, "
          f"threshold={args.threshold}%, sweet=[{args.sweet_lo},{args.sweet_hi}], "
          f"latency={args.min_latency}s, fee={args.fee_bps}bps")

    trades = replay(markets, btc, args.threshold, args.cooldown, args.max_lookahead,
                    args.min_seconds_to_close, args.min_latency,
                    args.sweet_lo, args.sweet_hi, args.fee_bps)

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True, parents=True)
    with out.open("w") as f:
        for t in trades:
            f.write(json.dumps(asdict(t)) + "\n")
    print(f"\n[done] wrote {len(trades)} signal records to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
