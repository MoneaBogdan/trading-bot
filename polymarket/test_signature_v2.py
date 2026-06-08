"""Probe whether the NEW polymarket-client SDK accepts orders from our
deposit-wallet account.

Posts a non-marketable limit BUY (price 0.01) on an upcoming BTC market,
then cancels. Goal: confirm signature/auth config is correct so we can
swap live_trader.py to the new SDK with confidence.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    pk = os.environ["POLY_PRIVATE_KEY"]
    funder = os.environ["POLY_FUNDER_ADDRESS"]

    from polymarket import SecureClient
    from polymarket.auth import RelayerApiKey

    # Pull an upcoming BTC 5-min market token so the order is non-marketable
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gamma import discover_btc_markets
    mks = discover_btc_markets(window_horizon_min=30)
    if not mks:
        print("[fail] no upcoming BTC market in 30min")
        return 1
    mk = mks[0]
    print(f"[market] {mk.title}")
    print(f"  up_token: {mk.up_token_id[:16]}…")
    print()

    # Build the client. `wallet` = deposit wallet (proxy). `private_key` =
    # MetaMask private key (the EOA that is authorized to sign for the proxy).
    print(f"[client] signer EOA derived from POLY_PRIVATE_KEY, wallet={funder}")
    client = SecureClient.create(
        private_key=pk,
        wallet=funder,
    )
    print(f"[client] OK")

    print(f"\n[order] limit BUY 5 @ 0.01 on UP token (non-marketable, should rest)")
    try:
        # create_limit_order signs locally. post_order submits to CLOB.
        signed = client.create_limit_order(
            token_id=mk.up_token_id, side="BUY", price=0.01, size=5.0,
        )
        print(f"[signed] {signed}")
        order_resp = client.post_order(signed)
        print(f"[ok] order accepted: {order_resp}")
        # Try to cancel immediately
        oid = getattr(order_resp, "id", None) or getattr(order_resp, "order_id", None)
        if oid:
            try:
                cancel_resp = client.cancel_order(oid)
                print(f"[cancel] {cancel_resp}")
            except Exception as e:
                print(f"[warn] cancel: {e}")
        print(f"\n✅ Signature config works with polymarket-client SDK.")
        return 0
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
