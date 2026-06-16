"""Per-bot JSONL logger writing the new event schema.

Writes to: <base>/bot=<bot_name>/YYYY-MM-DD.jsonl  (one row per event, append-only,
date computed at write time so a long-running process rotates cleanly at UTC midnight).

Schema (every row): {ts, bot, strategy, event, ...event-specific fields}
Event vocabulary: boot, shutdown, fire, skip, fill, pnl, bot_crashed, position_orphaned.
Skip reasons: see ARCHITECTURE.md §10.4 controlled vocabulary.

This logger runs in PARALLEL with the legacy `logs/live_*.jsonl` writer in
live_trader.py until Phase B parity is validated. Neither replaces the other yet.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KNOWN_EVENTS = frozenset({
    "boot", "shutdown", "fire", "skip", "fill", "pnl",
    "bot_crashed", "position_orphaned",
})

KNOWN_SKIP_REASONS = frozenset({
    "threshold_not_met", "cooldown_active", "cb_confirm_fail",
    "no_market_in_window", "no_window_open_price", "window_anchor_disagree",
    "no_ask_on_side", "ask_outside_sweet_band",
    "daily_cap_reached", "open_position_cap_reached",
})


@dataclass
class BotLogger:
    bot: str                          # config name, e.g. "btc-5m"
    strategy: str                     # strategy class name, e.g. "PolymarketLatencyArb"
    base_dir: Path = field(default_factory=lambda: Path("logs"))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)
        (self.base_dir / f"bot={self.bot}").mkdir(parents=True, exist_ok=True)

    def _path(self, ts: datetime) -> Path:
        return self.base_dir / f"bot={self.bot}" / f"{ts:%Y-%m-%d}.jsonl"

    def log(self, event: str, *, ts: datetime | None = None, **fields: Any) -> None:
        if event not in KNOWN_EVENTS:
            raise ValueError(f"unknown event {event!r}; add to KNOWN_EVENTS first")
        ts = ts or datetime.now(timezone.utc)
        row = {
            "ts": ts.isoformat(timespec="milliseconds"),
            "bot": self.bot,
            "strategy": self.strategy,
            "event": event,
            **fields,
        }
        line = json.dumps(row, default=str) + "\n"
        path = self._path(ts)
        with self._lock:
            with path.open("a") as f:
                f.write(line)

    def boot(self, **config: Any) -> None:
        self.log("boot", pid=os.getpid(), config=config)

    def shutdown(self, reason: str = "normal") -> None:
        self.log("shutdown", reason=reason)

    def skip(self, reason: str, *, ts: datetime | None = None, **debug: Any) -> None:
        if reason not in KNOWN_SKIP_REASONS:
            raise ValueError(
                f"unknown skip reason {reason!r}; add to KNOWN_SKIP_REASONS first"
            )
        self.log("skip", ts=ts, reason=reason, debug=debug or None)

    def fire(self, *, ts: datetime, intent_id: str, venue: str, market_id: str,
             market_title: str, outcome_name: str, side: str, order_type: str,
             size_usdc: float, limit_price: float, filled_size: float | None,
             filled_price: float | None, cost_usdc: float | None, order_ok: bool,
             order_id: str | None, dry_run: bool, **debug: Any) -> None:
        self.log(
            "fire", ts=ts, intent_id=intent_id, venue=venue, market_id=market_id,
            market_title=market_title, outcome_name=outcome_name, side=side,
            order_type=order_type, size_usdc=size_usdc, limit_price=limit_price,
            filled_size=filled_size, filled_price=filled_price, cost_usdc=cost_usdc,
            order_ok=order_ok, order_id=order_id, dry_run=dry_run,
            debug=debug or None,
        )

    def crashed(self, exc: BaseException) -> None:
        self.log("bot_crashed", error_type=type(exc).__name__, error=str(exc))
