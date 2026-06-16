"""Skeleton for a new strategy. Copy this file, rename, fill in `decide`.

The shape is deliberately close to the target architecture (ARCHITECTURE.md §7)
so when Phase C extracts strategies into a real bus-driven framework, every
strategy that followed this skeleton can be moved in with mechanical changes
only — no logic rewrites.

Rules (do not break — extraction depends on them):
  1. `decide` is a PURE function over (state, event) → list of order intents.
     No I/O. No `print`. No HTTP. No `time.sleep`. No reading env vars inside
     the function. If you need clock time, take it from the event.
  2. All tunables (thresholds, cooldowns, sizes) come from `Params` built at
     boot from env vars in `main()`. Never read os.environ inside `decide`.
  3. Strategy module imports ONLY: stdlib, src.core.*, dataclasses, typing.
     No imports from polymarket/, no imports from other strategies. If you
     need market data, the bot loop fetches it and passes it via the event.
  4. State is one mutable object passed back into each `decide` call. If you
     need persistent state across restarts, write it via `BotLogger` and
     rebuild on boot — do not pickle.
  5. Side effects (orders, logging) happen OUTSIDE `decide`, in the runner
     that calls it. The strategy only returns intents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---- Parameters: built once at boot from env, then frozen ----

@dataclass(frozen=True)
class Params:
    threshold_pct: float
    cooldown_s: float
    size_usdc: float


# ---- State: mutated by decide(), survives across events within one process ----

@dataclass
class State:
    last_fire_ts: datetime | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ---- Intent: what the strategy asks the runner to do ----

@dataclass(frozen=True)
class Intent:
    """Minimal intent shape — Phase C replaces this with src.core.types.OrderIntent."""
    ts: datetime
    venue: str
    market_id: str
    side: str           # "buy" / "sell"
    outcome: str        # venue-specific outcome name
    size_usdc: float
    limit_price: float
    reason: str         # short tag for the fire log


# ---- The decision function (PURE — no I/O) ----

def decide(state: State, event: Any, params: Params) -> list[Intent]:
    """Return zero-or-more intents in response to an event.

    Replace the body. Keep the signature.
    """
    # Example skeleton (always returns no intents):
    return []


# ---- Boot-time wiring (lives in the bot runner, NOT in this module in v1) ----
#
# The runner does:
#   params = Params(threshold_pct=float(os.environ["THRESHOLD"]), ...)
#   state = State()
#   logger = BotLogger(bot=..., strategy=__name__)
#   logger.boot(**asdict(params))
#   async for event in stream:
#       for intent in decide(state, event, params):
#           result = execute(intent)
#           logger.fire(...)
