"""Polymarket trader using the official `polymarket-client` SDK.

Handles deposit-wallet accounts (signature_type=3 / POLY_1271) natively —
no SDK patching required, unlike the older py-clob-client.

Safety:
  - DRY RUN by default. Only places real orders when explicitly enabled.
  - Hard cap on per-order USDC notional (default $5).
  - Hard cap on total daily notional (default $50).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TraderConfig:
    private_key: str
    funder_address: str | None = None  # the deposit-wallet proxy
    max_order_usdc: float = 5.0
    max_daily_usdc: float = 50.0
    dry_run: bool = True


@dataclass
class OrderResult:
    ok: bool
    order_id: str | None
    filled_size: Decimal | float
    filled_price: float
    error: str | None
    raw: object | None


class Trader:
    def __init__(self, config: TraderConfig):
        self.config = config
        self._client = None
        self._spent_today_usdc = 0.0
        self._day_key = time.strftime("%Y-%m-%d", time.gmtime())

    def _client_lazy(self):
        if self._client is not None:
            return self._client
        from polymarket import SecureClient
        kwargs = dict(private_key=self.config.private_key)
        if self.config.funder_address:
            kwargs["wallet"] = self.config.funder_address
        self._client = SecureClient.create(**kwargs)
        return self._client

    def _check_day_rollover(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day_key:
            self._day_key = today
            self._spent_today_usdc = 0.0

    def can_trade(self, size_usdc: float) -> tuple[bool, str]:
        self._check_day_rollover()
        if size_usdc > self.config.max_order_usdc:
            return False, f"size {size_usdc} > per-order cap {self.config.max_order_usdc}"
        if self._spent_today_usdc + size_usdc > self.config.max_daily_usdc:
            return False, (f"would exceed daily cap "
                           f"({self._spent_today_usdc + size_usdc:.2f} > {self.config.max_daily_usdc})")
        return True, "ok"

    def place_buy_fok(self, token_id: str, ask_price: float, size_usdc: float) -> OrderResult:
        """Place a marketable BUY (FOK) for `size_usdc` worth of `token_id`.

        ask_price is only used for the DRY-RUN log + as a sanity check; the
        real fill price is determined by the orderbook at the time of execution.
        """
        ok, reason = self.can_trade(size_usdc)
        if not ok:
            return OrderResult(ok=False, order_id=None, filled_size=0, filled_price=0,
                              error=f"risk_cap: {reason}", raw=None)
        if self.config.dry_run:
            logger.info(f"[DRY] would BUY token={token_id[:12]}… "
                       f"ask~{ask_price} size_usdc={size_usdc}")
            return OrderResult(ok=True, order_id="DRY",
                              filled_size=size_usdc / ask_price,
                              filled_price=ask_price, error=None, raw={"dry_run": True})

        client = self._client_lazy()
        try:
            resp = client.place_market_order(
                token_id=token_id,
                side="BUY",
                amount=size_usdc,            # USDC notional
                order_type="FOK",            # fill-or-kill — no resting bid left behind
            )
        except Exception as e:
            return OrderResult(ok=False, order_id=None, filled_size=0, filled_price=0,
                              error=f"sdk_error: {e}", raw=None)

        ok_field = getattr(resp, "ok", False)
        order_id = getattr(resp, "order_id", None)
        making_amount = getattr(resp, "making_amount", Decimal(0))  # USDC spent
        taking_amount = getattr(resp, "taking_amount", Decimal(0))  # tokens received
        avg_price = float(making_amount / taking_amount) if taking_amount else 0.0
        if ok_field:
            self._spent_today_usdc += float(making_amount or 0)
        return OrderResult(
            ok=ok_field,
            order_id=order_id,
            filled_size=taking_amount,
            filled_price=avg_price,
            error=None if ok_field else getattr(resp, "error_message", str(resp)),
            raw=resp,
        )


def trader_from_env() -> Trader:
    """Build a Trader from env vars. PRIVATE_KEY only required when dry_run=False."""
    dry_run = os.environ.get("POLY_DRY_RUN", "true").lower() != "false"
    pk = os.environ.get("POLY_PRIVATE_KEY") or os.environ.get("PRIVATE_KEY") or ""
    if not pk and not dry_run:
        raise RuntimeError("POLY_PRIVATE_KEY must be set when POLY_DRY_RUN=false")
    cfg = TraderConfig(
        private_key=pk,
        funder_address=os.environ.get("POLY_FUNDER_ADDRESS") or None,
        max_order_usdc=float(os.environ.get("POLY_MAX_ORDER_USDC", "5")),
        max_daily_usdc=float(os.environ.get("POLY_MAX_DAILY_USDC", "50")),
        dry_run=dry_run,
    )
    return Trader(cfg)
