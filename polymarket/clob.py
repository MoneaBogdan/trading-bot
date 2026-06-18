"""Polymarket CLOB API — read-only public endpoints (no auth needed).

We only consume orderbook snapshots and midpoints for the observation harness.
Order placement requires authentication; not built yet.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

CLOB_BASE = "https://clob.polymarket.com"

# Shared HTTP client reused across `get_orderbook` calls when the caller
# doesn't supply its own. Avoids paying TCP + TLS handshake cost (typically
# 100-300ms cold, 30-80ms warm-DNS) on every hot-path orderbook fetch.
# `httpx.Client` is thread-safe for concurrent requests, which matters
# because `get_orderbook` is invoked from a thread pool via run_in_executor.
_SHARED_CLIENT: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None:
        _SHARED_CLIENT = httpx.Client(
            timeout=10.0,
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300.0),
            http2=False,  # CLOB is HTTP/1.1; keep-alive alone is the win
        )
    return _SHARED_CLIENT


@dataclass
class Orderbook:
    token_id: str
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    bid_size: float
    ask_size: float
    spread: float | None


def get_orderbook(token_id: str, client: httpx.Client | None = None) -> Orderbook:
    """Fetch top of book for a CLOB token. Best-effort: if the endpoint shape
    changes, returns None fields rather than raising.

    When `client` is None we reuse a module-level keep-alive client. Pass an
    explicit client only when you need isolated lifecycle (e.g. tests)."""
    own_close = False
    if client is None:
        client = _shared_client()
    try:
        r = client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        r.raise_for_status()
        data = r.json()
    except Exception:
        return Orderbook(token_id, None, None, None, 0.0, 0.0, None)
    finally:
        if own_close:
            client.close()

    bids = data.get("bids") or []
    asks = data.get("asks") or []
    # Bids are highest first, asks lowest first; book endpoint typically returns
    # them sorted, but we sort defensively.
    bids_sorted = sorted([(float(b["price"]), float(b["size"])) for b in bids], reverse=True)
    asks_sorted = sorted([(float(a["price"]), float(a["size"])) for a in asks])
    best_bid = bids_sorted[0][0] if bids_sorted else None
    best_ask = asks_sorted[0][0] if asks_sorted else None
    bid_size = bids_sorted[0][1] if bids_sorted else 0.0
    ask_size = asks_sorted[0][1] if asks_sorted else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
    spread = (best_ask - best_bid) if best_bid and best_ask else None
    return Orderbook(
        token_id=token_id,
        best_bid=best_bid, best_ask=best_ask, mid=mid,
        bid_size=bid_size, ask_size=ask_size, spread=spread,
    )
