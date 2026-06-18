"""In-memory top-of-book cache fed by Polymarket CLOB market WebSocket.

Replaces the per-fire HTTPS GET to /book with a synchronous dict lookup.
The cache subscribes to a dynamic set of token_ids (the active-window
markets the trader cares about) and updates on every book/price_change/
best_bid_ask message.

Falls back gracefully: callers can ask `is_fresh()` and route to HTTPS
when the cache is missing or stale.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class OrderbookSnapshot:
    token_id: str
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    bid_size: float
    ask_size: float
    spread: float | None
    updated_ts: float


class OrderbookCache:
    def __init__(self, ping_interval_s: float = 20.0,
                 max_backoff_s: float = 30.0) -> None:
        self._snapshots: dict[str, OrderbookSnapshot] = {}
        # Why: full ladders kept so we can recompute top-size after price_change
        # deltas (best_bid/best_ask are echoed in deltas but not their size).
        self._bids: dict[str, dict[float, float]] = {}
        self._asks: dict[str, dict[float, float]] = {}
        self._tokens: set[str] = set()
        self._desired: set[str] = set()
        self._resub_evt = asyncio.Event()
        self._ping_interval_s = ping_interval_s
        self._max_backoff_s = max_backoff_s
        self._ws = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    def update_subscriptions(self, token_ids: set[str]) -> None:
        desired = set(token_ids)
        if desired == self._desired:
            return
        self._desired = desired
        self._resub_evt.set()

    def get(self, token_id: str) -> OrderbookSnapshot | None:
        return self._snapshots.get(token_id)

    def is_fresh(self, token_id: str, max_age_ms: int = 5000) -> bool:
        snap = self._snapshots.get(token_id)
        if snap is None or snap.best_bid is None or snap.best_ask is None:
            return False
        return (time.time() - snap.updated_ts) * 1000.0 <= max_age_ms

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except Exception:
                pass

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                if not self._desired:
                    await asyncio.sleep(1.0)
                    continue
                async with websockets.connect(
                    WS_URL,
                    ping_interval=self._ping_interval_s,
                    ping_timeout=self._ping_interval_s,
                    close_timeout=5.0,
                    max_size=2**22,
                ) as ws:
                    self._ws = ws
                    backoff = 1.0
                    self._tokens = set(self._desired)
                    await self._send_subscribe(ws, self._tokens)
                    resub_task = asyncio.create_task(self._watch_resubscribe(ws))
                    try:
                        async for raw in ws:
                            self._handle_message(raw)
                            if self._desired != self._tokens:
                                break
                    finally:
                        resub_task.cancel()
                        self._ws = None
            except Exception as e:
                print(f"[book-cache] ws error: {e!r}; reconnect in {backoff:.1f}s",
                      flush=True)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    return
                backoff = min(backoff * 2.0, self._max_backoff_s)

    async def _watch_resubscribe(self, ws) -> None:
        try:
            while True:
                await self._resub_evt.wait()
                self._resub_evt.clear()
                if self._desired != self._tokens:
                    await ws.close()
                    return
        except asyncio.CancelledError:
            pass

    async def _send_subscribe(self, ws, tokens: set[str]) -> None:
        payload = {
            "assets_ids": sorted(tokens),
            "type": "market",
            "custom_feature_enabled": True,
        }
        await ws.send(json.dumps(payload))

    def _handle_message(self, raw) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            return
        events = data if isinstance(data, list) else [data]
        for ev in events:
            if not isinstance(ev, dict):
                continue
            et = ev.get("event_type")
            if et == "book":
                self._apply_book(ev)
            elif et == "price_change":
                self._apply_price_change(ev)
            elif et == "best_bid_ask":
                self._apply_bba(ev)

    def _apply_book(self, ev: dict) -> None:
        tid = ev.get("asset_id")
        if not tid:
            return
        bids: dict[float, float] = {}
        asks: dict[float, float] = {}
        for lvl in ev.get("bids") or []:
            try:
                p = float(lvl["price"]); s = float(lvl["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                bids[p] = s
        for lvl in ev.get("asks") or []:
            try:
                p = float(lvl["price"]); s = float(lvl["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                asks[p] = s
        self._bids[tid] = bids
        self._asks[tid] = asks
        self._recompute_top(tid)

    def _apply_price_change(self, ev: dict) -> None:
        for chg in ev.get("price_changes") or []:
            tid = chg.get("asset_id")
            if not tid:
                continue
            try:
                p = float(chg["price"]); s = float(chg["size"])
            except (KeyError, TypeError, ValueError):
                continue
            side = (chg.get("side") or "").upper()
            book = self._bids.setdefault(tid, {}) if side == "BUY" else \
                self._asks.setdefault(tid, {})
            if s == 0.0:
                book.pop(p, None)
            else:
                book[p] = s
            self._recompute_top(tid)

    def _apply_bba(self, ev: dict) -> None:
        tid = ev.get("asset_id")
        if not tid:
            return
        snap = self._snapshots.get(tid)
        try:
            bb = float(ev["best_bid"]) if ev.get("best_bid") not in (None, "") else None
            ba = float(ev["best_ask"]) if ev.get("best_ask") not in (None, "") else None
        except (TypeError, ValueError):
            return
        bid_size = self._bids.get(tid, {}).get(bb, 0.0) if bb is not None else 0.0
        ask_size = self._asks.get(tid, {}).get(ba, 0.0) if ba is not None else 0.0
        mid = (bb + ba) / 2 if bb is not None and ba is not None else None
        spread = (ba - bb) if bb is not None and ba is not None else None
        self._snapshots[tid] = OrderbookSnapshot(
            token_id=tid, best_bid=bb, best_ask=ba, mid=mid,
            bid_size=bid_size, ask_size=ask_size, spread=spread,
            updated_ts=time.time(),
        )
        _ = snap

    def _recompute_top(self, tid: str) -> None:
        bids = self._bids.get(tid) or {}
        asks = self._asks.get(tid) or {}
        bb = max(bids) if bids else None
        ba = min(asks) if asks else None
        bid_size = bids.get(bb, 0.0) if bb is not None else 0.0
        ask_size = asks.get(ba, 0.0) if ba is not None else 0.0
        mid = (bb + ba) / 2 if bb is not None and ba is not None else None
        spread = (ba - bb) if bb is not None and ba is not None else None
        self._snapshots[tid] = OrderbookSnapshot(
            token_id=tid, best_bid=bb, best_ask=ba, mid=mid,
            bid_size=bid_size, ask_size=ask_size, spread=spread,
            updated_ts=time.time(),
        )
