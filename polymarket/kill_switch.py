"""Live-mode kill switch.

Tracks realized PnL and consecutive losses across resolved fills, halts new
orders when either limit trips. Only active when POLY_DRY_RUN=false — dry-run
doesn't need throttling.

Resolution piggy-backs on the same gamma endpoint daily_report uses: every
POLY_KILL_POLL_INTERVAL_S seconds a background task asks gamma whether any
pending fill's market has closed, scores it WIN/LOSS, updates state.

State resets at UTC midnight (matching daily_spent in trader.py).

Env:
  POLY_KILL_DAILY_PNL_USDC   default -10.0  (halt when day pnl <= this)
  POLY_KILL_LOSS_STREAK      default 3      (halt after this many in a row)
  POLY_KILL_POLL_INTERVAL_S  default 60
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from polymarket.backtest.daily_report import _resolve_markets  # noqa: E402


@dataclass
class PendingFill:
    market_id: str
    outcome_name: str
    size_usdc: float
    fill_price: float
    fill_ts: datetime
    market_end_iso: str | None  # earliest time we should bother polling gamma


@dataclass
class KillSwitch:
    daily_pnl_limit_usdc: float = -10.0
    loss_streak_limit: int = 3
    poll_interval_s: int = 60

    daily_pnl_usdc: float = 0.0
    loss_streak: int = 0
    halt_reason: str | None = None
    day_key: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%d", time.gmtime())
    )
    _pending: list[PendingFill] = field(default_factory=list)
    _on_event: Callable[[str, dict[str, Any]], None] | None = None

    def set_event_sink(self, sink: Callable[[str, dict[str, Any]], None]) -> None:
        self._on_event = sink

    def _emit(self, event: str, **payload: Any) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event, payload)
        except Exception as e:
            print(f"[kill-switch] event sink failed: {e}", flush=True)

    def _roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today == self.day_key:
            return
        prior = {
            "prior_day": self.day_key,
            "daily_pnl_usdc": round(self.daily_pnl_usdc, 4),
            "loss_streak_at_rollover": self.loss_streak,
            "had_halt": self.halt_reason is not None,
        }
        self.day_key = today
        self.daily_pnl_usdc = 0.0
        self.loss_streak = 0
        if self.halt_reason is not None:
            self.halt_reason = None
            self._emit("kill_switch_resume", reason="day_rollover", **prior)

    def can_trade(self) -> tuple[bool, str | None]:
        self._roll_day()
        if self.halt_reason is not None:
            return False, self.halt_reason
        return True, None

    def record_fill(self, *, market_id: str, outcome_name: str,
                    size_usdc: float, fill_price: float, fill_ts: datetime,
                    market_end_iso: str | None) -> None:
        self._roll_day()
        self._pending.append(PendingFill(
            market_id=market_id,
            outcome_name=(outcome_name or "").upper(),
            size_usdc=float(size_usdc),
            fill_price=float(fill_price or 0.0),
            fill_ts=fill_ts,
            market_end_iso=market_end_iso,
        ))

    def _settle(self, fill: PendingFill, winner: str) -> None:
        """Realize PnL for one fill and update streak/halt."""
        if winner == "UNKNOWN":
            # Treat as void — drop the fill, neither win nor loss.
            return
        won = fill.outcome_name == winner
        if won and fill.fill_price > 0:
            # Each share pays $1; cost was fill_price * shares.
            shares = fill.size_usdc / fill.fill_price
            pnl = shares - fill.size_usdc   # payout - cost
        else:
            pnl = -fill.size_usdc
        self.daily_pnl_usdc += pnl
        if won:
            self.loss_streak = 0
        else:
            self.loss_streak += 1

        self._emit(
            "kill_switch_settle",
            market_id=fill.market_id, outcome_name=fill.outcome_name,
            winner=winner, won=won, pnl_usdc=round(pnl, 4),
            daily_pnl_usdc=round(self.daily_pnl_usdc, 4),
            loss_streak=self.loss_streak,
        )

        if self.halt_reason is None:
            if self.daily_pnl_usdc <= self.daily_pnl_limit_usdc:
                self.halt_reason = (
                    f"daily_pnl {self.daily_pnl_usdc:.2f} "
                    f"<= limit {self.daily_pnl_limit_usdc:.2f}"
                )
                self._emit("kill_switch_halt", reason=self.halt_reason,
                           daily_pnl_usdc=round(self.daily_pnl_usdc, 4),
                           loss_streak=self.loss_streak)
            elif self.loss_streak >= self.loss_streak_limit:
                self.halt_reason = (
                    f"loss_streak {self.loss_streak} "
                    f">= limit {self.loss_streak_limit}"
                )
                self._emit("kill_switch_halt", reason=self.halt_reason,
                           daily_pnl_usdc=round(self.daily_pnl_usdc, 4),
                           loss_streak=self.loss_streak)

    async def poll_loop(self) -> None:
        """Resolve any pending fill whose market_end is past."""
        while True:
            try:
                self._roll_day()
                now = datetime.now(timezone.utc)
                # Group pending by date for one gamma call per date.
                due_by_date: dict[str, list[PendingFill]] = defaultdict(list)
                for fill in self._pending:
                    if fill.market_end_iso:
                        try:
                            end = datetime.fromisoformat(
                                fill.market_end_iso.replace("Z", "+00:00")
                            )
                            if end > now:
                                continue
                            due_by_date[end.strftime("%Y-%m-%d")].append(fill)
                        except (ValueError, TypeError):
                            due_by_date[fill.fill_ts.strftime("%Y-%m-%d")].append(fill)
                    else:
                        due_by_date[fill.fill_ts.strftime("%Y-%m-%d")].append(fill)

                resolved_ids: set[str] = set()
                for date_str, fills in due_by_date.items():
                    cids = {f.market_id for f in fills if f.market_id}
                    if not cids:
                        continue
                    loop = asyncio.get_running_loop()
                    winners = await loop.run_in_executor(
                        None, _resolve_markets, cids, date_str
                    )
                    for fill in fills:
                        winner = winners.get(fill.market_id)
                        if winner:
                            self._settle(fill, winner)
                            resolved_ids.add(id(fill))
                if resolved_ids:
                    self._pending = [f for f in self._pending if id(f) not in resolved_ids]
            except Exception as e:
                print(f"[kill-switch] poll failed: {type(e).__name__}: {e}",
                      flush=True)
            await asyncio.sleep(self.poll_interval_s)


def kill_switch_from_env() -> KillSwitch:
    return KillSwitch(
        daily_pnl_limit_usdc=float(
            os.environ.get("POLY_KILL_DAILY_PNL_USDC", "-10")
        ),
        loss_streak_limit=int(
            os.environ.get("POLY_KILL_LOSS_STREAK", "3")
        ),
        poll_interval_s=int(
            os.environ.get("POLY_KILL_POLL_INTERVAL_S", "60")
        ),
    )
