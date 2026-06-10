"""Live latency-arb trader for Polymarket BTC Up/Down 5-min markets.

Strategy (validated on 30-day replay; see polymarket/backtest/):
  - Stream Binance BTC trades.
  - When 60s return crosses threshold (default 0.10%):
      * Find market resolving within 30-300s.
      * If current ask on our side is in 0.30..0.70 (sweet spot), place a
        marketable FOK BUY at the ask, sized at POLY_MAX_ORDER_USDC.
      * Skip if ask is outside the band — either too cheap (no edge) or
        already chased (we'd lift the offer for ~0.02 expected payoff).
  - One open position at a time (cooldown until market resolves).
  - Logs every signal + fill to logs/live_<date>.jsonl.

Safety defaults:
  - POLY_DRY_RUN=true (no orders placed until you flip this)
  - POLY_MAX_ORDER_USDC=5
  - POLY_MAX_DAILY_USDC=50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from binance_stream import stream_btc_trades  # noqa: E402
from clob import get_orderbook  # noqa: E402
from coinbase_stream import stream_btc_trades as stream_coinbase_trades  # noqa: E402
from gamma import discover_btc_markets  # noqa: E402
from monitor import MoveTracker, _pick_market  # noqa: E402
from trader import trader_from_env  # noqa: E402


class PriceHistory:
    """Bounded buffer of Binance trades for arbitrary-timestamp price lookup.

    Needed for the window-open anchor: a 5-min market's window_start can be up
    to ~5 min before signal time, outside MoveTracker's 60s buffer. Sized at
    360s so we comfortably cover any signal time within the window.
    """

    def __init__(self, window_s: float = 360.0):
        self.window = timedelta(seconds=window_s)
        self._trades: deque = deque()

    def add(self, t) -> None:
        self._trades.append(t)
        cutoff = t.ts - self.window
        while self._trades and self._trades[0].ts < cutoff:
            self._trades.popleft()

    def price_at(self, ts: datetime) -> float | None:
        """Last price at or before `ts`. None if ts is before our buffer's start."""
        if not self._trades or self._trades[0].ts > ts:
            return None
        result = None
        for t in self._trades:
            if t.ts <= ts:
                result = t.price
            else:
                break
        return result


async def _track_coinbase(tracker: MoveTracker, hb: dict) -> None:
    """Background loop: feed Coinbase BTC-USD trades into a separate MoveTracker.
    Used as a cross-exchange confirmation gate — Polymarket 5m markets settle
    against Chainlink Data Streams which aggregates multiple venues, so a
    Binance-only signal can diverge from the aggregate during fast moves."""
    async for trade in stream_coinbase_trades():
        tracker.add(trade)
        hb["cb"] = datetime.now(timezone.utc)


async def _watchdog(hb: dict, stale_s: float, require_confirm: bool) -> None:
    """Exit the process if a stream has been silent for > stale_s. The bash
    wrapper (`run_live.sh`) then restarts cleanly, recovering from half-dead
    TCP states that the WS ping/pong didn't catch."""
    while True:
        await asyncio.sleep(15.0)
        now = datetime.now(timezone.utc)
        for stream in ("binance",) + (("cb",) if require_confirm else ()):
            last = hb.get(stream)
            if last and (now - last).total_seconds() > stale_s:
                print(f"[watchdog] {stream} feed stale > {stale_s:.0f}s — exiting", flush=True)
                os._exit(2)


async def _refresh_markets(state: dict, interval_s: float = 60.0) -> None:
    """Background loop: refresh upcoming markets every interval_s."""
    while True:
        try:
            mks = await asyncio.get_running_loop().run_in_executor(
                None, lambda: discover_btc_markets(window_horizon_min=10)
            )
            state["markets"] = mks
            state["markets_updated"] = datetime.now(timezone.utc)
        except Exception as e:
            print(f"[markets] refresh error: {e}", flush=True)
        await asyncio.sleep(interval_s)


def _log(path: Path, payload: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(payload) + "\n")


async def run(log_path: Path, threshold_pct: float, cooldown_s: float,
              sweet_lo: float, sweet_hi: float, require_confirm: bool,
              snipe_window_s: float, require_window_anchor: bool) -> None:
    trader = trader_from_env()
    print(f"[trader] dry_run={trader.config.dry_run}  "
          f"max_order=${trader.config.max_order_usdc}  "
          f"max_daily=${trader.config.max_daily_usdc}", flush=True)
    print(f"[strategy] threshold={threshold_pct}%  cooldown={cooldown_s}s  "
          f"sweet_spot=[{sweet_lo},{sweet_hi}]  "
          f"require_confirm={require_confirm}  "
          f"snipe_window={snipe_window_s}s  "
          f"require_window_anchor={require_window_anchor}", flush=True)

    state: dict = {"markets": [], "markets_updated": None}
    asyncio.create_task(_refresh_markets(state))

    tracker = MoveTracker(window_seconds=60.0)
    cb_tracker = MoveTracker(window_seconds=60.0)
    history = PriceHistory(window_s=360.0)
    hb: dict = {"binance": datetime.now(timezone.utc), "cb": datetime.now(timezone.utc)}
    if require_confirm:
        asyncio.create_task(_track_coinbase(cb_tracker, hb))
    asyncio.create_task(_watchdog(hb, stale_s=90.0, require_confirm=require_confirm))
    last_signal_ts: datetime | None = None

    async for trade in stream_btc_trades():
        tracker.add(trade)
        history.add(trade)
        hb["binance"] = datetime.now(timezone.utc)
        ret = tracker.return_pct
        if ret is None:
            continue
        if abs(ret) < threshold_pct:
            continue
        if last_signal_ts and (trade.ts - last_signal_ts).total_seconds() < cooldown_s:
            continue

        if require_confirm:
            cb_ret = cb_tracker.return_pct
            if cb_ret is None or abs(cb_ret) < threshold_pct or (cb_ret > 0) != (ret > 0):
                cb_disp = "n/a" if cb_ret is None else f"{cb_ret:+.3f}%"
                print(f"[skip] cb confirm fail  binance={ret:+.3f}% coinbase={cb_disp}", flush=True)
                last_signal_ts = trade.ts
                continue

        # Restrict to markets within the snipe window — the closer to close
        # the smaller the reversal risk between signal and resolution.
        market = _pick_market(state["markets"], trade.ts, max_lookahead_s=int(snipe_window_s))
        if market is None:
            last_signal_ts = trade.ts  # cooldown to avoid spinning on the same move
            continue

        # Window-open anchor: the *actual* resolution question is "is BTC above
        # window-open at window-close?". Our 60s return is only a proxy. Require
        # sign agreement so we don't fire when the proxy and the real question disagree.
        #
        # gamma.window_start_iso is the EVENT listing date, not the trading window
        # start — useless for this. gamma already filters to exactly 5-min windows,
        # so window_open = end_dt - 300s is always correct.
        if require_window_anchor:
            w_open = market.end_dt - timedelta(seconds=300)
            open_price = history.price_at(w_open)
            if open_price is None or open_price <= 0:
                print(f"[skip] no window-open price (buffer < window start)", flush=True)
                last_signal_ts = trade.ts
                continue
            window_ret = (trade.price / open_price - 1) * 100
            if (window_ret > 0) != (ret > 0):
                print(f"[skip] window-anchor disagree  60s={ret:+.3f}% window={window_ret:+.3f}%", flush=True)
                last_signal_ts = trade.ts
                continue

        direction = "Up" if ret > 0 else "Down"
        target_token = market.up_token_id if direction == "Up" else market.down_token_id

        loop = asyncio.get_running_loop()
        ob = await loop.run_in_executor(None, get_orderbook, target_token)
        ask = ob.best_ask
        if ask is None:
            print(f"[skip] no ask on {direction} side for {market.title[:50]}", flush=True)
            last_signal_ts = trade.ts
            continue
        if not (sweet_lo <= ask <= sweet_hi):
            print(f"[skip] ask {ask} outside sweet spot [{sweet_lo},{sweet_hi}]  "
                  f"market={market.title[:50]}", flush=True)
            last_signal_ts = trade.ts
            continue

        # Place order
        size_usdc = trader.config.max_order_usdc
        result = trader.place_buy_fok(target_token, ask, size_usdc)
        record = {
            "type": "trade",
            "ts": trade.ts.isoformat(),
            "btc_price": trade.price,
            "btc_ret_60s_pct": ret,
            "market_title": market.title,
            "market_condition_id": market.condition_id,
            "market_end": market.window_end_iso,
            "direction": direction,
            "token_id": target_token,
            "ask": ask,
            "size_usdc": size_usdc,
            "order_ok": result.ok,
            "order_id": result.order_id,
            "filled_size": result.filled_size,
            "filled_price": result.filled_price,
            "error": result.error,
            "dry_run": trader.config.dry_run,
        }
        _log(log_path, record)
        status = "DRY" if trader.config.dry_run else ("OK" if result.ok else "FAIL")
        print(f"[ORDER {status}] {trade.ts.strftime('%H:%M:%S')}  ret={ret:+.3f}%  "
              f"dir={direction}  ask={ask}  market='{market.title[:50]}'  "
              f"error={result.error}", flush=True)
        last_signal_ts = trade.ts


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.10)
    ap.add_argument("--cooldown", type=float, default=60.0)
    ap.add_argument("--sweet-lo", type=float, default=0.30)
    ap.add_argument("--sweet-hi", type=float, default=0.40)
    ap.add_argument("--require-confirm", action="store_true",
                    help="require Coinbase 60s return to agree with Binance before firing")
    ap.add_argument("--snipe-window-s", type=float, default=90.0,
                    help="only fire when seconds-to-close <= this (smaller = less reversal risk, fewer fires)")
    ap.add_argument("--require-window-anchor", action="store_true",
                    help="require BTC-now-vs-window-open return to agree with 60s return (sign check)")
    ap.add_argument("--log", default=f"logs/live_{datetime.now(timezone.utc):%Y%m%d}.jsonl")
    args = ap.parse_args()
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run(log_path, args.threshold, args.cooldown,
                        args.sweet_lo, args.sweet_hi, args.require_confirm,
                        args.snipe_window_s, args.require_window_anchor))
    except KeyboardInterrupt:
        print("\n[stop] interrupted by user", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
