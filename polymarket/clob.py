"""Polymarket CLOB API — read-only public endpoints (no auth needed).

We only consume orderbook snapshots and midpoints for the observation harness.
Order placement requires authentication; not built yet.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

CLOB_BASE = "https://clob.polymarket.com"


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
    changes, returns None fields rather than raising."""
    own = client is None
    client = client or httpx.Client(timeout=10.0)
    try:
        r = client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        r.raise_for_status()
        data = r.json()
    except Exception:
        return Orderbook(token_id, None, None, None, 0.0, 0.0, None)
    finally:
        if own:
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
