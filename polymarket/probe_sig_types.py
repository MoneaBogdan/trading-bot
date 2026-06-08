"""Try posting a non-marketable order with every signature type config.

This isolates whether the rejection is signer-level (account ban) or
config-level (wrong sig_type / wrong funder).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdk_patch  # noqa: F401  # enable sig_type=3


def attempt(label: str, sig_type: int, funder: str | None) -> None:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.constants import POLYGON

    pk = os.environ["POLY_PRIVATE_KEY"]
    creds_data = json.loads((Path(__file__).parent / "polymarket_creds.json").read_text())
    kwargs = dict(
        host="https://clob.polymarket.com", key=pk, chain_id=POLYGON,
        creds=ApiCreds(
            api_key=creds_data["api_key"],
            api_secret=creds_data["api_secret"],
            api_passphrase=creds_data["api_passphrase"],
        ),
    )
    if funder:
        kwargs["signature_type"] = sig_type
        kwargs["funder"] = funder
    elif sig_type != 0:
        # signer-only modes still need a sig type; pass it anyway
        kwargs["signature_type"] = sig_type

    try:
        client = ClobClient(**kwargs)
    except Exception as e:
        print(f"  [{label}] client init failed: {e}")
        return

    from gamma import discover_btc_markets
    mks = discover_btc_markets(window_horizon_min=30)
    if not mks:
        print("  no upcoming markets — abort")
        return
    token = mks[0].up_token_id
    try:
        order = client.create_order(OrderArgs(token_id=token, price=0.01, size=5, side="BUY"))
    except Exception as e:
        print(f"  [{label}] order signing failed: {e}")
        return
    try:
        resp = client.post_order(order, OrderType.GTC)
        # If we got here, try to cancel
        oid = resp.get("orderID") or resp.get("orderId")
        if oid:
            try:
                client.cancel(order_id=oid)
            except Exception:
                pass
        print(f"  [{label}] ✅ ACCEPTED  resp={str(resp)[:160]}")
    except Exception as e:
        print(f"  [{label}] ❌ {type(e).__name__}: {str(e)[:200]}")


def main() -> int:
    load_dotenv()
    funder = os.environ.get("POLY_FUNDER_ADDRESS") or ""
    print(f"signer (from pk): MetaMask EOA")
    print(f"funder:           {funder or '(none — EOA direct)'}")
    print()

    configs = [
        ("type 0 EOA, funder=signer (MetaMask direct)", 0, None),
        ("type 1 POLY_PROXY, funder=proxy",             1, funder),
        ("type 2 POLY_GNOSIS_SAFE, funder=proxy",       2, funder),
        ("type 3 POLY_1271, funder=proxy",              3, funder),
    ]
    for label, sig, f in configs:
        print(f">>> {label}")
        attempt(label, sig, f)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
