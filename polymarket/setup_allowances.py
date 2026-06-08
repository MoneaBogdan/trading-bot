"""One-time helper: ask py-clob-client to set the proxy's USDC.e + Conditional
Token allowances against Polymarket's three CLOB exchange contracts.

Equivalent to placing a tiny manual trade in the Polymarket UI, but without
the trade. Polymarket usually sponsors the gas for these via their relayer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    pk = os.environ["POLY_PRIVATE_KEY"]
    funder = os.environ["POLY_FUNDER_ADDRESS"]

    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
    from py_clob_client.constants import POLYGON

    creds_path = Path(__file__).parent / "polymarket_creds.json"
    creds_data = json.loads(creds_path.read_text())
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=pk,
        chain_id=POLYGON,
        signature_type=int(os.environ.get("POLY_SIG_TYPE", "1")),
        funder=funder,
        creds=ApiCreds(
            api_key=creds_data["api_key"],
            api_secret=creds_data["api_secret"],
            api_passphrase=creds_data["api_passphrase"],
        ),
    )

    print(f"[setup] proxy: {funder}")
    print(f"[setup] signer: {client.get_address()}")
    print()

    # Before
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"[before] USDC.e balance: {bal.get('balance')}")
    print(f"[before] allowances: {bal.get('allowances')}")
    print()

    # Ask Polymarket relayer to set the allowance(s)
    print("[setup] requesting allowance update for COLLATERAL (USDC.e)…")
    try:
        r = client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"  response: {r}")
    except Exception as e:
        print(f"  error: {e}")
    print()

    # After
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print(f"[after] USDC.e balance: {bal.get('balance')}")
    print(f"[after] allowances: {bal.get('allowances')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
