"""Trade-tape replay — simulate realistic fills using executed-trade history.

Key differences vs replay.py:
  - Uses cache/trades/<cond>.json (Data API tape) instead of mid prices.
  - At signal time t, takes the FIRST trade on the side we want with
    timestamp >= t + min_latency_s. That trade's price = our fill.
    This models: "we sent a marketable order, it filled at the next
    available counterparty quote, paying whatever ask was sitting there."
  - Optionally adds a fee_bps cost per trade.

Polymarket trade rows look like:
    { side: "BUY"|"SELL", asset: <token_id>, price: float,
      timestamp: int, size: float }

We treat a SELL of a token as someone offloading that side — meaning
the BUY side of the orderbook printed. To enter LONG on UP we want a
SELL of UP (someone selling UP to us). We use SELL prints on our side
as the fillable ask proxy.

Caveat: this still assumes liquidity was sufficient to fill our size
at the printed price. For realistic capital, layer in size impact.
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
TRADES_DIR = CACHE_DIR / "trades"


@dataclass
class FillTrade:
    market_id: str
    title: str
    signal_ts: int
    fill_ts: int
    fill_latency_s: int   # actual wait between signal and fill
    market_end_ts: int
    btc_60s_ret_pct: float
    side: str             # "UP" | "DOWN"
    entry_price: float
    winner: str
    correct: bool
    payoff_per_unit: float


def find_next_fill(trades: list[dict], target_token: str, after_ts: int,
                   side_filter: str = "SELL") -> tuple[float, int] | None:
    """Find first trade in `trades` for `target_token` with timestamp >= after_ts
    and side == side_filter. Returns (price, timestamp) or None.

    A SELL print for a token means someone hit the bid / lifted offer — we
    use it as the executable ask proxy.
    """
    for tr in trades:
        if tr.get("timestamp", 0) < after_ts:
            continue
        if tr.get("asset") != target_token:
            continue
        if side_filter and tr.get("side") != side_filter:
            continue
        return float(tr["price"]), int(tr["timestamp"])
    return None


def load_tape(cond_id: str) -> dict | None:
    p = TRADES_DIR / f"{cond_id}.json"
    if not p.exists():
        return None
    with p.open() as f:
        return json.load(f)


def find_upcoming_market(end_times: list[int], markets: list[dict], now_ts: int,
                        max_lookahead_s: int) -> dict | None:
    idx = bisect.bisect_right(end_times, now_ts)
    if idx >= len(markets):
        return None
    mk = markets[idx]
    delta = mk["end_ts"] - now_ts
    if delta <= 0 or delta > max_lookahead_s:
        return None
    return mk


def replay(markets: list[dict], btc: pd.DataFrame, threshold_pct: float,
          cooldown_s: int, max_lookahead_s: int, min_seconds_to_close: int,
          min_latency_s: int, max_fill_wait_s: int, fee_bps: float,
          side_filter: str = "BUY") -> list[FillTrade]:
    closes = btc["close"].astype(float).values
    timestamps = btc["ts"].apply(lambda x: int(x.timestamp())).values
    end_times = [m["end_ts"] for m in markets]

    last_signal = 0
    trades: list[FillTrade] = []
    skipped = {"no_market": 0, "no_tape": 0, "no_fill": 0, "cooldown": 0,
               "too_close": 0, "fill_after_close": 0}

    for i in range(1, len(closes)):
        ret = (closes[i] / closes[i - 1] - 1) * 100
        if abs(ret) < threshold_pct:
            continue
        now_ts = int(timestamps[i])
        if now_ts - last_signal < cooldown_s:
            skipped["cooldown"] += 1
            continue
        mk = find_upcoming_market(end_times, markets, now_ts, max_lookahead_s)
        if not mk:
            skipped["no_market"] += 1
            continue
        if mk["end_ts"] - now_ts < min_seconds_to_close:
            skipped["too_close"] += 1
            continue
        tape = load_tape(mk["condition_id"])
        if not tape:
            skipped["no_tape"] += 1
            continue
        side = "UP" if ret > 0 else "DOWN"
        target_tok = mk["up_token_id"] if side == "UP" else mk["down_token_id"]
        fill_search_start = now_ts + min_latency_s
        # Use SELL prints (someone selling our side = ask we can lift)
        fill = find_next_fill(tape["trades"], target_tok, fill_search_start, side_filter)
        if not fill:
            skipped["no_fill"] += 1
            continue
        entry, fill_ts = fill
        if fill_ts - fill_search_start > max_fill_wait_s:
            skipped["no_fill"] += 1
            continue
        if fill_ts >= mk["end_ts"]:
            skipped["fill_after_close"] += 1
            continue
        # Apply fee (paid on entry price as a percentage of notional)
        entry_after_fee = entry * (1 + fee_bps / 10_000)
        correct = (mk["winner"] == side)
        payoff = (1.0 - entry_after_fee) if correct else -entry_after_fee
        trades.append(FillTrade(
            market_id=mk["condition_id"],
            title=mk["title"],
            signal_ts=now_ts,
            fill_ts=fill_ts,
            fill_latency_s=fill_ts - now_ts,
            market_end_ts=mk["end_ts"],
            btc_60s_ret_pct=ret,
            side=side,
            entry_price=entry_after_fee,
            winner=mk["winner"],
            correct=correct,
            payoff_per_unit=payoff,
        ))
        last_signal = now_ts

    print(f"[replay] {len(trades)} trades  (skipped: {skipped})")
    return trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets-file", required=True)
    ap.add_argument("--btc-file", required=True)
    ap.add_argument("--threshold", type=float, default=0.10)
    ap.add_argument("--cooldown", type=int, default=60)
    ap.add_argument("--max-lookahead", type=int, default=300)
    ap.add_argument("--min-seconds-to-close", type=int, default=30)
    ap.add_argument("--min-latency", type=int, default=5,
                    help="seconds between BTC observation and earliest acceptable fill")
    ap.add_argument("--max-fill-wait", type=int, default=60,
                    help="if no fill within this many seconds of search start, skip")
    ap.add_argument("--fee-bps", type=float, default=0,
                    help="fee in basis points applied to entry price")
    ap.add_argument("--side-filter", default="BUY",
                    help="BUY = use ask-side prints (someone lifted ask = what we pay). "
                         "SELL = use bid-side prints (optimistic).")
    ap.add_argument("--out", default="cache/trades_tape.jsonl")
    args = ap.parse_args()

    with open(args.markets_file) as f:
        markets = json.load(f)
    markets.sort(key=lambda m: m["end_ts"])
    btc = pd.read_parquet(args.btc_file)
    print(f"[replay] {len(markets)} markets, {len(btc)} BTC candles, "
          f"threshold={args.threshold}%, min_latency={args.min_latency}s, "
          f"fee={args.fee_bps}bps")

    trades = replay(markets, btc, args.threshold, args.cooldown, args.max_lookahead,
                    args.min_seconds_to_close, args.min_latency, args.max_fill_wait,
                    args.fee_bps, side_filter=args.side_filter)

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True, parents=True)
    with out.open("w") as f:
        for t in trades:
            f.write(json.dumps(asdict(t)) + "\n")
    print(f"[done] wrote {len(trades)} trades to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
