"""Runtime patch enabling signature_type=3 (POLY_1271 / DepositWallet) for py-clob-client.

py-clob-client 0.34.6 only validates sig types 0/1/2. New Polymarket users
(email signup) get a DepositWallet proxy (Type 3 per docs.polymarket.com).
The on-chain signing scheme is the same EIP-712 EOA signature; the only
difference is the `signatureType` field in the order. We import this module
before py_clob_client to relax the validation.

Import this at the top of any script that calls py-clob-client with sig_type=3.
"""
from __future__ import annotations


def _apply() -> None:
    from py_order_utils.builders import order_builder as ob
    from py_order_utils.model import sides as _sides
    from py_order_utils.model.signatures import EOA, POLY_PROXY, POLY_GNOSIS_SAFE

    POLY_1271 = 3

    # ---- patch 1: allow signatureType=3 (POLY_1271 / DepositWallet) ----
    def patched_validate(self, data):
        return not (
            data.maker is None
            or data.tokenId is None
            or data.makerAmount is None
            or data.takerAmount is None
            or data.side is None
            or data.side not in [_sides.BUY, _sides.SELL]
            or not data.feeRateBps.isnumeric()
            or int(data.feeRateBps) < 0
            or not data.nonce.isnumeric()
            or int(data.nonce) < 0
            or not data.expiration.isnumeric()
            or int(data.expiration) < 0
            or data.signatureType is None
            or data.signatureType not in [EOA, POLY_GNOSIS_SAFE, POLY_PROXY, POLY_1271]
        )

    ob.OrderBuilder._validate_inputs = patched_validate

    # ---- patch 2: relax "signer must equal EOA" check for sig_type=3 ----
    # For POLY_1271, the order's `signer` field is the proxy contract (not the
    # EOA) because EIP-1271 validates via the contract's isValidSignature().
    # The actual EIP-712 hash is still signed by the EOA private key.
    original_build_order = ob.OrderBuilder.build_order

    def patched_build_order(self, data):
        if data.signatureType == POLY_1271:
            # For deposit-wallet flow, the order's signer must be the maker (proxy).
            # py-order-utils' default sets data.signer = data.maker if None,
            # AND then rejects if data.signer != self.signer.address(). Skip the latter.
            if data.signer is None:
                data.signer = data.maker
            # Bypass the strict EOA equality check by short-circuiting that one line:
            saved_address = self.signer.address
            self.signer.address = lambda: data.signer  # pretend EOA matches
            try:
                return original_build_order(self, data)
            finally:
                self.signer.address = saved_address
        return original_build_order(self, data)

    ob.OrderBuilder.build_order = patched_build_order

    # ---- patch 3: in py-clob-client's wrapper, set order signer to funder
    # when sig_type=3, so the maker/signer fields both end up as the proxy. ----
    from py_clob_client.order_builder import builder as cb

    original_create_order = cb.OrderBuilder.create_order

    def patched_create_order(self, order_args, options):
        if self.sig_type == POLY_1271:
            real_signer_addr = self.signer.address()
            # Temporarily swap so OrderData.signer = self.funder (proxy)
            class _SignerWrapper:
                def __init__(self, inner, override_addr):
                    self._inner = inner
                    self._override = override_addr
                def address(self):
                    return self._override
                def __getattr__(self, name):
                    return getattr(self._inner, name)
            self.signer = _SignerWrapper(self.signer, self.funder)
            try:
                return original_create_order(self, order_args, options)
            finally:
                # Restore the real signer
                self.signer = self.signer._inner

        return original_create_order(self, order_args, options)

    cb.OrderBuilder.create_order = patched_create_order


_apply()
