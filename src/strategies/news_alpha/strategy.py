"""News-driven Polymarket strategy — pure decide() function.

Inputs come from `NewsEvent` (raw headline + classifier verdict + currently-open
markets discovered by the runner). Output is zero-or-more `Intent` objects the
runner executes.

Follows STRATEGY_TEMPLATE.md:
  * Pure: no I/O, no env reads, no clock reads (clock comes from event.ts).
  * Imports only stdlib + dataclasses + typing — no polymarket/, no other strats.
  * All tunables in `Params`, built once at boot from env in the runner.
  * Mutable `State` survives across events within one process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


# ---- Parameters ----

@dataclass(frozen=True)
class Params:
    min_confidence: float = 0.70          # classifier confidence floor
    cooldown_s: float = 60.0              # min seconds between fires
    sweet_lo: float = 0.30                # Polymarket ask floor (skip if below)
    sweet_hi: float = 0.40                # Polymarket ask ceiling (skip if above)
    size_usdc: float = 5.0                # notional per fire
    max_horizon_min: int = 60             # don't fire on markets resolving >N min out
    max_fires_per_day: int = 10           # daily cap
    allowed_assets: frozenset[str] = field(
        default_factory=lambda: frozenset({"BTC", "ETH", "SOL"})
    )


# ---- State ----

@dataclass
class State:
    last_fire_ts: datetime | None = None
    fires_today: int = 0
    day_key: str = ""                     # YYYY-MM-DD UTC; rolls over each midnight


# ---- Event from the runner (after classification + market discovery) ----

@dataclass(frozen=True)
class Classification:
    asset: str                            # "BTC" | "ETH" | "SOL" | ...
    direction: str                        # "up" | "down" | "neutral"
    confidence: float                     # 0..1
    horizon_min: int                      # expected impact horizon
    reason: str                           # short tag, e.g. "etf_approval"


@dataclass(frozen=True)
class MarketCandidate:
    """One Polymarket Up/Down market that's currently open for `asset`."""
    market_id: str
    condition_id: str
    title: str
    end_dt: datetime
    up_token_id: str
    down_token_id: str
    ask_up: float                         # current best ask on UP token (0..1)
    ask_down: float                       # current best ask on DOWN token


@dataclass(frozen=True)
class NewsEvent:
    ts: datetime                          # message timestamp (UTC)
    headline: str
    classification: Classification
    candidates: tuple[MarketCandidate, ...]    # markets matching asset, open right now


# ---- Intent (what decide returns to the runner) ----

@dataclass(frozen=True)
class Intent:
    ts: datetime
    venue: str                            # "polymarket"
    market_id: str
    condition_id: str
    side: str                             # "buy"
    outcome: str                          # "up" | "down"
    token_id: str
    size_usdc: float
    limit_price: float
    reason: str                           # tag for the fire log


# ---- decide ----

def decide(state: State, event: NewsEvent, params: Params) -> list[Intent]:
    """Pure decision. Returns 0 or 1 intents.

    Skip reasons (the runner is responsible for logging these — decide does
    NOT log, it just decides):
      * direction == "neutral"  → no edge
      * confidence < min        → too uncertain
      * asset not allowed       → out of scope
      * cooldown active         → too soon since last fire
      * daily cap reached       → risk budget exhausted
      * no candidate            → no live market for this asset
      * horizon too far         → all candidates resolve outside max_horizon_min
      * ask outside sweet band  → either already priced in or too cheap (skip both)
    """
    # Day rollover handled here (pure — uses event.ts, not wall clock)
    day_key = f"{event.ts:%Y-%m-%d}"
    if state.day_key != day_key:
        state.day_key = day_key
        state.fires_today = 0

    c = event.classification
    if c.direction not in ("up", "down"):
        return []
    if c.confidence < params.min_confidence:
        return []
    if c.asset not in params.allowed_assets:
        return []
    if state.fires_today >= params.max_fires_per_day:
        return []
    if state.last_fire_ts is not None:
        elapsed = (event.ts - state.last_fire_ts).total_seconds()
        if elapsed < params.cooldown_s:
            return []

    horizon_cutoff = event.ts + timedelta(minutes=params.max_horizon_min)
    live = [m for m in event.candidates if m.end_dt <= horizon_cutoff and m.end_dt > event.ts]
    if not live:
        return []

    # Pick the soonest-resolving market (fastest payout).
    target = min(live, key=lambda m: m.end_dt)

    if c.direction == "up":
        ask = target.ask_up
        token_id = target.up_token_id
        outcome = "up"
    else:
        ask = target.ask_down
        token_id = target.down_token_id
        outcome = "down"

    if not (params.sweet_lo <= ask <= params.sweet_hi):
        return []

    # State mutation only happens on a real fire — runner records this AFTER
    # the order is placed, so decide() updates State at decision time and the
    # runner's job is to actually execute the intent and log it.
    state.last_fire_ts = event.ts
    state.fires_today += 1

    return [Intent(
        ts=event.ts,
        venue="polymarket",
        market_id=target.market_id,
        condition_id=target.condition_id,
        side="buy",
        outcome=outcome,
        token_id=token_id,
        size_usdc=params.size_usdc,
        limit_price=ask,
        reason=f"news:{c.reason}:conf={c.confidence:.2f}",
    )]


# ---- skip-reason helper (so runner + tests can derive the same reasons) ----

KNOWN_NEWS_SKIPS = (
    "news_neutral_direction",
    "news_low_confidence",
    "news_asset_out_of_scope",
    "news_cooldown_active",
    "news_daily_cap_reached",
    "news_no_open_market",
    "news_horizon_too_far",
    "news_ask_outside_sweet_band",
)


def explain_skip(state: State, event: NewsEvent, params: Params) -> str | None:
    """Mirror of decide()'s gates — returns the first reason `decide` would
    skip on, or None if decide would have fired. Used by the runner to log
    structured skips without parsing decide()'s return value.

    NOTE: this mutates day_key the same way decide does, so call BEFORE decide
    (or skip the duplicate work and call decide only).
    """
    day_key = f"{event.ts:%Y-%m-%d}"
    if state.day_key != day_key:
        state.day_key = day_key
        state.fires_today = 0

    c = event.classification
    if c.direction not in ("up", "down"):
        return "news_neutral_direction"
    if c.confidence < params.min_confidence:
        return "news_low_confidence"
    if c.asset not in params.allowed_assets:
        return "news_asset_out_of_scope"
    if state.last_fire_ts is not None:
        if (event.ts - state.last_fire_ts).total_seconds() < params.cooldown_s:
            return "news_cooldown_active"
    if state.fires_today >= params.max_fires_per_day:
        return "news_daily_cap_reached"
    horizon_cutoff = event.ts + timedelta(minutes=params.max_horizon_min)
    live = [m for m in event.candidates if m.end_dt <= horizon_cutoff and m.end_dt > event.ts]
    if not event.candidates:
        return "news_no_open_market"
    if not live:
        return "news_horizon_too_far"
    target = min(live, key=lambda m: m.end_dt)
    ask = target.ask_up if c.direction == "up" else target.ask_down
    if not (params.sweet_lo <= ask <= params.sweet_hi):
        return "news_ask_outside_sweet_band"
    return None
