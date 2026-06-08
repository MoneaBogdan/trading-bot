"""Probe whether Polymarket accepts our signature config.

Places a non-marketable limit BUY order (price 0.01, well below any real ask),
verifies the API accepts the signed order, then cancels it. Total real-money
cost: zero, because nothing fills. Total time: a few seconds.

If signature_type is wrong for our proxy, Polymarket will reject with a
specific error mentioning sig type or order validation.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Allow sig_type=3 (POLY_1271 / DepositWallet) for new Polymarket users
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdk_patch  # noqa: F401


def main() -> int:
    load_dotenv()
    pk = os.environ["POLY_PRIVATE_KEY"]
    funder = os.environ["POLY_FUNDER_ADDRESS"]
    sig_type = int(os.environ.get("POLY_SIG_TYPE", "1"))

    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.constants import POLYGON

    creds_path = Path(__file__).parent / "polymarket_creds.json"
    creds_data = json.loads(creds_path.read_text())

    client = ClobClient(
        host="https://clob.polymarket.com", key=pk, chain_id=POLYGON,
        signature_type=sig_type, funder=funder,
        creds=ApiCreds(
            api_key=creds_data["api_key"],
            api_secret=creds_data["api_secret"],
            api_passphrase=creds_data["api_passphrase"],
        ),
    )
    print(f"[client] signer={client.get_address()}  funder={funder}  sig_type={sig_type}")

    # Pull an arbitrary upcoming BTC 5-min market token to test against
    sys.path.insert(0, str(Path(__file__).parent))
    from gamma import discover_btc_markets
    mks = discover_btc_markets(window_horizon_min=30)
    if not mks:
        print("[fail] no upcoming BTC market to test against right now")
        return 1
    mk = mks[0]
    token = mk.up_token_id
    print(f"[market] {mk.title}  testing against UP token {token[:12]}…")

    # Build a non-marketable BUY at price 0.01 (well below normal asks)
    test_price = 0.01
    test_size_tokens = 5  # tiny
    print(f"[order] BUY {test_size_tokens} tokens @ {test_price} (non-marketable)")

    try:
        order = client.create_order(OrderArgs(
            token_id=token, price=test_price, size=test_size_tokens, side="BUY",
        ))
        print(f"[order] signed OK")
    except Exception as e:
        print(f"[FAIL] order signing: {e}")
        return 2

    try:
        resp = client.post_order(order, OrderType.GTC)
        print(f"[post] response: {json.dumps(resp, indent=2)[:500]}")
        order_id = resp.get("orderID") or resp.get("orderId")
    except Exception as e:
        print(f"[FAIL] order post: {e}")
        return 3

    # Cancel immediately so the order doesn't sit
    if order_id:
        try:
            cancel_resp = client.cancel(order_id=order_id)
            print(f"[cancel] {cancel_resp}")
        except Exception as e:
            print(f"[warn] cancel failed: {e}")

    print("\n✅ Signature config works — Polymarket accepted the order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
