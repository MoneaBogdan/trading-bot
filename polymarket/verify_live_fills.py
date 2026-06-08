"""For each live signal, look up what the orderbook ACTUALLY had at signal
time, using the WS recorder data. Compares:

  - logged_ask: the best ask our live_trader saw via HTTP /book call
  - ws_ask:     the best ask in the orderbook events recorded via WS
  - ws_depth:   how many shares were sitting at ws_ask
  - 5_usdc_avg: what avg price we would have paid filling $5 across the book

Tells us if our $5 orders would actually have filled near the logged price
or walked up the book significantly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import defaultdict


def parse_book_event(rec: dict) -> tuple[str, list[tuple[float, float]]] | None:
    """Extract (token_id, asks_sorted_asc) from a book/price-change event.

    Returns None for events that don't carry book state.
    """
    t = rec.get("_type") or rec.get("type", "")
    if t == "MarketBookEvent":
        # Full snapshot: bids + asks arrays
        token_id = rec.get("asset_id") or rec.get("token_id")
        asks = rec.get("asks") or []
        asks_parsed = [(float(a.get("price")), float(a.get("size"))) for a in asks]
        return (token_id, sorted(asks_parsed)) if token_id else None
    if t == "MarketPriceChangeEvent":
        # Deltas: changes = [{price, size, side}, ...]
        token_id = rec.get("asset_id") or rec.get("token_id")
        if not token_id:
            return None
        return token_id, []  # delta — we'd need to apply incrementally; skip for now
    return None


def reconstruct_books_at(ws_path: Path, target_tokens: set[str],
                        signal_ts_by_token: dict[str, list[float]],
                        window_pre_s: float = 30.0,
                        window_post_s: float = 5.0) -> dict:
    """Walk the WS log, build a snapshot of each target token's ask side at
    or just before each signal timestamp.

    Returns {token_id: [(signal_ts, asks_at_signal), ...]}
    """
    # token → current ask side (list of (price, size))
    cur_asks: dict[str, list[tuple[float, float]]] = defaultdict(list)
    last_book_ts: dict[str, float] = {}

    results: dict[tuple[str, float], list[tuple[float, float]]] = {}

    # Pre-compute target lookups
    pending = {tok: sorted(ts_list) for tok, ts_list in signal_ts_by_token.items()}

    n_lines = 0
    n_match_books = 0
    with ws_path.open() as f:
        for line in f:
            n_lines += 1
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = rec.get("_type", "")
            if t != "MarketBookEvent":
                continue
            payload = rec.get("payload") or {}
            token_id = payload.get("token_id") or payload.get("asset_id")
            if not token_id or token_id not in target_tokens:
                continue
            n_match_books += 1
            asks = payload.get("asks") or []
            try:
                asks_parsed = sorted([(float(a.get("price")), float(a.get("size"))) for a in asks])
            except Exception:
                continue
            recv_ts = rec.get("_recv_ts", 0)
            cur_asks[token_id] = asks_parsed
            last_book_ts[token_id] = recv_ts

            # Check if this snapshot is the latest "at-or-before" any pending signal
            for sig_ts in pending.get(token_id, []):
                if last_book_ts[token_id] <= sig_ts:
                    # Possibly the right snapshot — overwrite (we want latest <= sig_ts)
                    results[(token_id, sig_ts)] = asks_parsed

    return results, n_lines, n_match_books


def avg_fill_price(asks: list[tuple[float, float]], usdc: float) -> tuple[float, float, bool]:
    """Simulate buying `usdc` worth across the ask ladder.

    Returns (avg_price, total_tokens, fully_filled).
    """
    spent = 0.0
    tokens = 0.0
    for price, size in asks:
        max_spend_here = price * size
        remaining = usdc - spent
        if remaining <= max_spend_here:
            buy_tokens = remaining / price
            tokens += buy_tokens
            spent += remaining
            return (spent / tokens, tokens, True)
        spent += max_spend_here
        tokens += size
    return (spent / tokens if tokens > 0 else 0, tokens, False)


def main() -> int:
    root = Path(__file__).parent
    sig_path = root / "logs" / "live_20260606.jsonl"
    ws_path = root / "logs" / "orderbook_ws_20260606.jsonl"

    signals = [json.loads(l) for l in sig_path.open()]
    target_tokens = {s["token_id"] for s in signals}
    signal_ts_by_token: dict[str, list[float]] = defaultdict(list)

    # Compute Unix epoch for each signal_ts string
    from datetime import datetime
    for s in signals:
        ts = datetime.fromisoformat(s["ts"]).timestamp()
        signal_ts_by_token[s["token_id"]].append(ts)
        s["_epoch"] = ts

    print(f"[input] {len(signals)} signals, {len(target_tokens)} unique tokens")
    print(f"[scan]  {ws_path.name}")

    results, n_lines, n_match = reconstruct_books_at(ws_path, target_tokens, signal_ts_by_token)
    print(f"[scan]  read {n_lines:,} WS lines, found {n_match} MarketBookEvent for our tokens\n")

    print(f"{'time':<10} {'side':>4} {'logged':>7} {'ws_ask':>7} {'depth@ask':>10} {'$5_avg':>7} {'5usdc_tokens':>13}")
    print("-" * 70)
    for s in signals:
        key = (s["token_id"], s["_epoch"])
        asks = results.get(key)
        if asks is None:
            # Try a fuzzy match: any recent snapshot for this token
            print(f"{s['ts'][11:19]:<10} {s['direction']:>4} {s['ask']:>7.2f}   (no book snapshot found in WS data)")
            continue
        ws_ask = asks[0][0] if asks else None
        ws_depth = asks[0][1] if asks else None
        avg_5, tok_5, full = avg_fill_price(asks, 5.0)
        depth_str = f"{ws_depth:.0f}" if ws_depth is not None else "?"
        print(f"{s['ts'][11:19]:<10} {s['direction']:>4} {s['ask']:>7.2f} {ws_ask:>7.3f} {depth_str:>10} {avg_5:>7.4f} {tok_5:>12.2f}  ({'full' if full else 'walked'})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
