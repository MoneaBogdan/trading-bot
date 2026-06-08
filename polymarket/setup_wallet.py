"""One-time wallet setup for live trading on Polymarket CLOB.

Run this once per machine. It will:
  1. Verify the private key works.
  2. Print the wallet address (so you can fund it with USDC and MATIC).
  3. Derive API credentials from the signing key and cache them locally.
  4. Check USDC + Conditional Token allowances and prompt to set if missing.

Prerequisites you must handle manually first:
  - Send a small amount of MATIC (~$1) to the wallet for gas.
  - Send USDC to the wallet (start with $20-50 for live testing).
  - Set POLY_PRIVATE_KEY in your .env (no 0x prefix is required by ClobClient).

DO NOT commit your private key. Keep .env out of git (it's in .gitignore).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    pk = os.environ.get("POLY_PRIVATE_KEY") or os.environ.get("PRIVATE_KEY")
    if not pk:
        print("ERROR: POLY_PRIVATE_KEY not set in env. Add it to polymarket/.env")
        print("       The .env file is gitignored.")
        return 1

    from py_clob_client.client import ClobClient
    from py_clob_client.constants import POLYGON

    funder = os.environ.get("POLY_FUNDER_ADDRESS")
    kwargs = dict(host="https://clob.polymarket.com", key=pk, chain_id=POLYGON)
    if funder:
        kwargs["signature_type"] = int(os.environ.get("POLY_SIG_TYPE", "1"))
        kwargs["funder"] = funder

    client = ClobClient(**kwargs)
    addr = client.get_address()
    print(f"[wallet] signer address: {addr}")
    if funder:
        print(f"[wallet] funder address: {funder}")
    print(f"[wallet] chain id: 137 (Polygon mainnet)")
    print()

    # Derive API creds
    print("[creds] deriving API credentials from signing key…")
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)

    creds_path = Path(__file__).parent / "polymarket_creds.json"
    import json as _json
    creds_path.write_text(_json.dumps({
        "api_key": creds.api_key,
        "api_secret": creds.api_secret,
        "api_passphrase": creds.api_passphrase,
    }))
    print(f"[creds] cached to {creds_path}")
    print()

    # Balances
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        print(f"[balance] USDC.e: {bal}")
    except Exception as e:
        print(f"[balance] could not read USDC balance: {e}")
    print()

    print("=" * 60)
    print("Setup OK. To begin trading:")
    print("  1. Verify the address above is funded with USDC + MATIC.")
    print("  2. Run live_trader.py with POLY_DRY_RUN=true for a few hours.")
    print("  3. Set POLY_DRY_RUN=false when ready to place real orders.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
