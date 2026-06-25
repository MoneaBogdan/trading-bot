"""Pure decision logic for the Polymarket latency-arb bot.

The live runner owns streams, market discovery, orderbook reads, order
placement, and logging. This module owns only the decision tree.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol


class MarketLike(Protocol):
    title: str
    condition_id: str
    end_dt: datetime
    window_end_iso: str
    up_token_id: str
    down_token_id: str


@dataclass(frozen=True)
class Params:
    threshold_pct: float
    cooldown_s: float
    sweet_lo: float
    sweet_hi: float
    require_confirm: bool
    snipe_window_s: float
    require_window_anchor: bool
    asset: str
    timeframe_min: int


@dataclass
class State:
    last_signal_ts: datetime | None = None

    def mark_signal(self, ts: datetime) -> None:
        self.last_signal_ts = ts


@dataclass(frozen=True)
class SignalEvent:
    ts: datetime
    price: float
    ret_60s_pct: float | None
    cb_ret_60s_pct: float | None
    market: MarketLike | None
    window_open_price: float | None = None


@dataclass(frozen=True)
class BookEvent:
    ts: datetime
    ret_60s_pct: float
    market: MarketLike
    direction: str
    target_token: str
    ask: float | None


@dataclass(frozen=True)
class Ignore:
    kind: str = "ignore"


@dataclass(frozen=True)
class Skip:
    reason: str
    debug: dict
    kind: str = "skip"


@dataclass(frozen=True)
class Proceed:
    kind: str = "proceed"


@dataclass(frozen=True)
class BookRequest:
    market: MarketLike
    direction: str
    target_token: str
    debug: dict
    kind: str = "book_request"


@dataclass(frozen=True)
class OrderIntent:
    ts: datetime
    venue: str
    market_id: str
    market_title: str
    outcome_name: str
    side: str
    order_type: str
    target_token: str
    limit_price: float
    reason: str
    debug: dict
    kind: str = "order_intent"


SignalGateDecision = Ignore | Skip | Proceed
PreBookDecision = Ignore | Skip | BookRequest
BookDecision = Skip | OrderIntent


def decide_signal_gate(state: State, event: SignalEvent, params: Params) -> SignalGateDecision:
    """Run threshold, cooldown, and cross-exchange confirmation gates."""
    ret = event.ret_60s_pct
    if ret is None:
        return Ignore()
    if abs(ret) < params.threshold_pct:
        return Ignore()
    if (
        state.last_signal_ts
        and (event.ts - state.last_signal_ts).total_seconds() < params.cooldown_s
    ):
        return Ignore()

    if params.require_confirm:
        cb_ret = event.cb_ret_60s_pct
        if cb_ret is None or abs(cb_ret) < params.threshold_pct or (cb_ret > 0) != (ret > 0):
            state.mark_signal(event.ts)
            return Skip(
                reason="cb_confirm_fail",
                debug={"ret_60s_pct": ret, "cb_ret_60s_pct": cb_ret},
            )

    return Proceed()


def decide_prebook(state: State, event: SignalEvent, params: Params) -> PreBookDecision:
    """Run all gates that do not require fetching an orderbook."""
    signal_gate = decide_signal_gate(state, event, params)
    if isinstance(signal_gate, (Ignore, Skip)):
        return signal_gate

    ret = event.ret_60s_pct
    if ret is None:
        return Ignore()

    market = event.market
    if market is None:
        state.mark_signal(event.ts)
        return Skip(
            reason="no_market_in_window",
            debug={"ret_60s_pct": ret, "snipe_window_s": params.snipe_window_s},
        )

    if params.require_window_anchor:
        window_open = market.end_dt - timedelta(seconds=params.timeframe_min * 60)
        open_price = event.window_open_price
        if open_price is None or open_price <= 0:
            state.mark_signal(event.ts)
            return Skip(
                reason="no_window_open_price",
                debug={"ret_60s_pct": ret, "window_open_iso": window_open.isoformat()},
            )
        window_ret = (event.price / open_price - 1) * 100
        if (window_ret > 0) != (ret > 0):
            state.mark_signal(event.ts)
            return Skip(
                reason="window_anchor_disagree",
                debug={"ret_60s_pct": ret, "window_ret_pct": window_ret},
            )

    direction = "Up" if ret > 0 else "Down"
    target_token = market.up_token_id if direction == "Up" else market.down_token_id
    return BookRequest(
        market=market,
        direction=direction,
        target_token=target_token,
        debug={"ret_60s_pct": ret},
    )


def decide_with_book(state: State, event: BookEvent, params: Params) -> BookDecision:
    """Run gates that need the selected side's current top-of-book ask."""
    if event.ask is None:
        state.mark_signal(event.ts)
        return Skip(
            reason="no_ask_on_side",
            debug={
                "ret_60s_pct": event.ret_60s_pct,
                "direction": event.direction,
                "market_id": event.market.condition_id,
            },
        )

    if not (params.sweet_lo <= event.ask <= params.sweet_hi):
        state.mark_signal(event.ts)
        return Skip(
            reason="ask_outside_sweet_band",
            debug={
                "ret_60s_pct": event.ret_60s_pct,
                "ask": event.ask,
                "sweet_lo": params.sweet_lo,
                "sweet_hi": params.sweet_hi,
                "market_id": event.market.condition_id,
            },
        )

    return OrderIntent(
        ts=event.ts,
        venue="polymarket",
        market_id=event.market.condition_id,
        market_title=event.market.title,
        outcome_name=event.direction,
        side="buy",
        order_type="fok_limit",
        target_token=event.target_token,
        limit_price=event.ask,
        reason="latency_arb_signal",
        debug={"ret_60s_pct": event.ret_60s_pct},
    )
