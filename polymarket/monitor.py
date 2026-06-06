"""Latency-arb observation harness.

Hypothesis (from the YouTube digest): a large BTC move on Binance precedes
the corresponding Polymarket 5-min market repricing by 30-90 seconds. If true,
we can detect the move, look at the relevant Polymarket market, decide which
side has edge, and (in a later iteration) place an order.

For this first iteration we DO NOT trade. We only:
  1. Stream Binance BTC trades (websocket).
  2. Maintain a 60-second rolling return.
  3. Every N seconds, refresh the upcoming 5-min Polymarket markets (Gamma API).
  4. When |60s return| exceeds a threshold AND there's an open Polymarket
     market resolving in the next few minutes, log a "signal":
       - Binance BTC price now
       - Polymarket market title, current "Up" and "Down" prices
       - Direction we'd take (Up if Binance just rallied, Down if it just fell)
  5. After the market's window resolves, look at where BTC closed at window end,
     compute the hypothetical PnL (binary payoff, accounting for entry price).

Output: a JSONL log at signals.jsonl. Run for several hours / days, then
analyze: how often did signals fire? What was the realized win rate?
What was the average edge per trade?
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Local imports — module is run as `python -m polymarket.monitor` from the parent dir,
# OR as `python monitor.py` from inside polymarket/. Handle both.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from binance_stream import BinanceTrade, stream_btc_trades  # noqa: E402
from clob import Orderbook, get_orderbook  # noqa: E402
from gamma import BtcMarket, discover_btc_markets  # noqa: E402


@dataclass
class Signal:
    """Logged when a price spike triggers a hypothetical trade decision."""
    ts: str
    btc_price_now: float
    btc_return_60s_pct: float
    chosen_market_title: str
    chosen_market_end: str
    chosen_market_condition_id: str
    direction: str                    # "Up" or "Down"
    poly_up_bid: float | None
    poly_up_ask: float | None
    poly_down_bid: float | None
    poly_down_ask: float | None
    entry_price: float | None         # the ask we'd be paying for our chosen side
    counter_price: float | None       # the bid we'd be selling (for sanity check)


class MoveTracker:
    """Rolling 60-second return tracker built from incoming trades."""

    def __init__(self, window_seconds: float = 60.0):
        self.window = timedelta(seconds=window_seconds)
        self._trades: deque[BinanceTrade] = deque()

    def add(self, t: BinanceTrade) -> None:
        self._trades.append(t)
        cutoff = t.ts - self.window
        while self._trades and self._trades[0].ts < cutoff:
            self._trades.popleft()

    @property
    def latest_price(self) -> float | None:
        return self._trades[-1].price if self._trades else None

    @property
    def return_pct(self) -> float | None:
        if len(self._trades) < 2:
            return None
        start = self._trades[0].price
        end = self._trades[-1].price
        if start <= 0:
            return None
        return (end / start - 1.0) * 100


async def _refresh_markets_periodically(state: dict, interval_s: float = 60.0) -> None:
    """Background task: pull upcoming 5-min BTC markets every minute."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            try:
                # discover_btc_markets uses sync httpx; run in executor.
                markets = await asyncio.get_running_loop().run_in_executor(
                    None, discover_btc_markets, 60,
                )
                state["markets"] = markets
                state["markets_updated"] = datetime.now(timezone.utc)
                if markets:
                    next_m = markets[0]
                    print(f"[markets] {len(markets)} upcoming 5-min BTC markets; next ends at {next_m.end_dt.isoformat()}  liq=${next_m.liquidity:.0f}", flush=True)
                else:
                    print("[markets] no upcoming 5-min BTC markets in next 60min", flush=True)
            except Exception as exc:
                print(f"[markets] refresh error: {exc}", flush=True)
            await asyncio.sleep(interval_s)


def _pick_market(markets: list[BtcMarket], now: datetime, max_lookahead_s: int = 300) -> BtcMarket | None:
    """Pick the market whose window ends soonest (within max_lookahead). Prefer
    markets ending in the next 30s-5min — long enough to enter, short enough
    that the Binance signal still applies at resolution."""
    for m in markets:
        delta = (m.end_dt - now).total_seconds()
        if 30 <= delta <= max_lookahead_s:
            return m
    return None


def _log_signal(log_path: Path, signal: Signal) -> None:
    with log_path.open("a") as f:
        f.write(json.dumps(asdict(signal)) + "\n")


async def _watch_resolution(signal: Signal, btc_now_ref, log_path: Path) -> None:
    """After a signal fires, wait until the market window ends, then log the
    hypothetical outcome. `btc_now_ref` is a callable that returns latest price."""
    end = datetime.fromisoformat(signal.chosen_market_end.replace("Z", "+00:00"))
    sleep_s = (end - datetime.fromisoformat(signal.ts)).total_seconds()
    if sleep_s <= 0:
        return
    await asyncio.sleep(sleep_s)
    # Snapshot BTC price at window end.
    close_price = btc_now_ref()
    if close_price is None:
        return
    # Compare to "price at start of window" approximation — we use the signal's
    # btc_price_now since this is roughly when the strategy would have known about
    # the move. Polymarket actually resolves on Coinbase BTC close-of-window vs
    # close-of-window-start, but for this first iteration we use Binance.
    btc_moved_up = close_price >= signal.btc_price_now
    correct = (signal.direction == "Up" and btc_moved_up) or (signal.direction == "Down" and not btc_moved_up)
    # Binary payoff math: paid `entry_price`, payout is $1 if correct, $0 if wrong.
    if signal.entry_price is None:
        return
    payoff = (1.0 - signal.entry_price) if correct else -signal.entry_price
    outcome = {
        "type": "outcome",
        "signal_ts": signal.ts,
        "market_end": signal.chosen_market_end,
        "direction": signal.direction,
        "btc_price_signal": signal.btc_price_now,
        "btc_price_resolve": close_price,
        "btc_moved_up": btc_moved_up,
        "correct": correct,
        "entry_price": signal.entry_price,
        "payoff_per_unit": payoff,
    }
    with log_path.open("a") as f:
        f.write(json.dumps(outcome) + "\n")
    print(f"[outcome] {'WIN ' if correct else 'LOSS'} payoff_per_unit={payoff:+.4f} dir={signal.direction} btc {signal.btc_price_now:.2f}->{close_price:.2f}", flush=True)


async def run(log_path: Path, move_threshold_pct: float, cooldown_s: float) -> None:
    state: dict = {"markets": [], "markets_updated": None}
    market_refresh = asyncio.create_task(_refresh_markets_periodically(state))
    tracker = MoveTracker(window_seconds=60.0)
    last_signal_at: datetime | None = None
    print(f"[start] move_threshold={move_threshold_pct:.3f}%  cooldown={cooldown_s}s  log={log_path}", flush=True)

    def latest_btc_price() -> float | None:
        return tracker.latest_price

    async for trade in stream_btc_trades():
        tracker.add(trade)
        ret = tracker.return_pct
        if ret is None:
            continue
        # Print a heartbeat every ~30s of new trades to show we're alive.
        if (trade.ts.second % 30 == 0 and trade.ts.microsecond < 100_000):
            print(f"[hb] {trade.ts.strftime('%H:%M:%S')}  btc=${trade.price:.2f}  60s_ret={ret:+.3f}%  pending_markets={len(state['markets'])}", flush=True)

        if abs(ret) < move_threshold_pct:
            continue
        if last_signal_at and (trade.ts - last_signal_at).total_seconds() < cooldown_s:
            continue

        markets = state.get("markets") or []
        market = _pick_market(markets, trade.ts)
        if market is None:
            continue

        direction = "Up" if ret > 0 else "Down"
        # Pull orderbooks for both legs to record current prices.
        loop = asyncio.get_running_loop()
        up_ob, down_ob = await asyncio.gather(
            loop.run_in_executor(None, get_orderbook, market.up_token_id),
            loop.run_in_executor(None, get_orderbook, market.down_token_id),
        )
        chosen_ob = up_ob if direction == "Up" else down_ob
        counter_ob = down_ob if direction == "Up" else up_ob

        signal = Signal(
            ts=trade.ts.isoformat(),
            btc_price_now=trade.price,
            btc_return_60s_pct=ret,
            chosen_market_title=market.title,
            chosen_market_end=market.window_end_iso,
            chosen_market_condition_id=market.condition_id,
            direction=direction,
            poly_up_bid=up_ob.best_bid,
            poly_up_ask=up_ob.best_ask,
            poly_down_bid=down_ob.best_bid,
            poly_down_ask=down_ob.best_ask,
            entry_price=chosen_ob.best_ask,
            counter_price=counter_ob.best_bid,
        )
        _log_signal(log_path, signal)
        last_signal_at = trade.ts
        print(f"[SIGNAL] {trade.ts.strftime('%H:%M:%S')}  ret={ret:+.3f}%  dir={direction}  market='{market.title}'  entry={signal.entry_price}  ends={market.end_dt.strftime('%H:%M:%S')}Z", flush=True)
        # Schedule the resolution check in background; don't block the stream.
        asyncio.create_task(_watch_resolution(signal, latest_btc_price, log_path))


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.30,
                        help="60s return threshold (%%) to trigger a signal")
    parser.add_argument("--cooldown", type=float, default=120.0,
                        help="Seconds to wait after a signal before allowing another")
    parser.add_argument("--log", default="signals.jsonl",
                        help="Path to append JSONL signal + outcome records")
    args = parser.parse_args()
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run(log_path, args.threshold, args.cooldown))
    except KeyboardInterrupt:
        print("\n[stop] interrupted by user", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
