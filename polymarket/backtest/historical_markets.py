"""Pull closed BTC Up/Down 5-min markets from Gamma over a date range.

Uses the /markets endpoint with 1-hour slices because:
  - /events caps responses at 100, but ~100+ FDV/sports events end in any
    12h window, crowding out the BTC 5-min ones (we lost ~135 BTC markets
    per slice the first try).
  - /markets returns one row per market and a 1-hour slice has ~50 "Up or
    Down" markets (BTC + SOL + ETH + XRP), well under the 100 cap.
  - Slug filters (slug_contains, tag, tag_id) are silently ignored by
    Gamma, so we filter client-side by title.

Output: JSON cache at polymarket/backtest/cache/markets_<from>_<to>.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

GAMMA = "https://gamma-api.polymarket.com"
CACHE_DIR = Path(__file__).parent / "cache"

TITLE_REGEX = re.compile(
    r"Bitcoin Up or Down\s*-\s*([A-Za-z]+ \d+),\s*"
    r"(\d{1,2}:\d{2}(?:AM|PM))-(\d{1,2}:\d{2}(?:AM|PM))\s*ET",
    re.IGNORECASE,
)


@dataclass
class HistMarket:
    """Closed BTC Up/Down market we can replay later."""
    event_id: str
    market_id: str
    condition_id: str
    title: str
    start_iso: str
    end_iso: str
    end_ts: int
    up_token_id: str
    down_token_id: str
    final_up_price: float       # 1.0 if UP won, 0.0 if DOWN won
    final_down_price: float
    winner: str                 # "UP" | "DOWN" | "UNKNOWN"
    liquidity: float
    volume: float


def _minutes_between(t1: str, t2: str) -> int:
    def to_min(s: str) -> int:
        h, rest = s.split(":")
        m = int(rest[:2])
        ampm = rest[2:].upper()
        hh = int(h) % 12
        if ampm == "PM":
            hh += 12
        return hh * 60 + m
    a, b = to_min(t1), to_min(t2)
    if b < a:
        b += 24 * 60
    return b - a


def _parse_outcome_prices(s: str | None) -> tuple[float, float]:
    if not s:
        return 0.5, 0.5
    try:
        prices = json.loads(s)
        if len(prices) == 2:
            return float(prices[0]), float(prices[1])
    except Exception:
        pass
    return 0.5, 0.5


def _parse_tokens(s: str | None) -> tuple[str, str] | None:
    if not s:
        return None
    try:
        ids = json.loads(s)
        if len(ids) == 2:
            return str(ids[0]), str(ids[1])
    except Exception:
        return None
    return None


def _market_to_hist(mk: dict) -> HistMarket | None:
    """Convert a /markets row to HistMarket (returns None if not a BTC 5-min)."""
    question = mk.get("question", "")
    m = TITLE_REGEX.search(question)
    if not m:
        return None
    _, t1, t2 = m.groups()
    if _minutes_between(t1, t2) != 5:
        return None
    toks = _parse_tokens(mk.get("clobTokenIds"))
    if not toks:
        return None
    end_iso = mk.get("endDate", "")
    try:
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    up_p, down_p = _parse_outcome_prices(mk.get("outcomePrices"))
    if up_p >= 0.99:
        winner = "UP"
    elif down_p >= 0.99:
        winner = "DOWN"
    else:
        winner = "UNKNOWN"
    return HistMarket(
        event_id="",
        market_id=str(mk.get("id")),
        condition_id=str(mk.get("conditionId")),
        title=question,
        start_iso=mk.get("startDate", ""),
        end_iso=end_iso,
        end_ts=int(end_dt.timestamp()),
        up_token_id=toks[0],
        down_token_id=toks[1],
        final_up_price=up_p,
        final_down_price=down_p,
        winner=winner,
        liquidity=float(mk.get("liquidity") or 0),
        volume=float(mk.get("volume") or 0),
    )


PAGE_SIZE = 100  # Gamma silently caps limit at 100


def fetch_window(client: httpx.Client, end_min: datetime, end_max: datetime) -> list[dict]:
    """All Gamma /markets rows covering [end_min, end_max] — paginated by offset.

    Polymarket's daily-expiry markets are very dense (we've seen 100+ markets
    resolving in a single 5-min window across all Up/Down series), so we
    must paginate or we'll miss BTC entries crowded out by SOL/ETH/XRP/etc.
    """
    all_rows: list[dict] = []
    offset = 0
    while True:
        r = client.get(
            f"{GAMMA}/markets",
            params={
                "limit": PAGE_SIZE,
                "closed": "true",
                "end_date_min": end_min.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": end_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "order": "endDate",
                "ascending": "false",
                "offset": offset,
            },
        )
        r.raise_for_status()
        rows = r.json()
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.05)
    return all_rows


def collect_markets(start: datetime, end: datetime, slice_minutes: int = 30) -> list[HistMarket]:
    """Walk backwards from `end` to `start` in slice_minutes chunks."""
    markets: dict[str, HistMarket] = {}
    cursor = end
    slices_done = 0
    with httpx.Client(timeout=30.0) as client:
        while cursor > start:
            window_min = max(start, cursor - timedelta(minutes=slice_minutes))
            rows = fetch_window(client, window_min, cursor)
            n_btc = 0
            for mk in rows:
                hm = _market_to_hist(mk)
                if hm and hm.condition_id not in markets:
                    markets[hm.condition_id] = hm
                    n_btc += 1
            slices_done += 1
            if slices_done % 24 == 0:
                print(
                    f"  [{window_min:%Y-%m-%d %H:%M}..{cursor:%H:%M}] "
                    f"{len(rows)} markets, +{n_btc} new BTC 5-min "
                    f"(total: {len(markets)})"
                )
            cursor = window_min
            time.sleep(0.15)
    out = sorted(markets.values(), key=lambda x: x.end_ts)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="how many days back to fetch")
    ap.add_argument("--slice-minutes", type=int, default=15)
    args = ap.parse_args()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    print(f"[markets] collecting closed BTC 5-min markets {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    markets = collect_markets(start, end, slice_minutes=args.slice_minutes)

    CACHE_DIR.mkdir(exist_ok=True)
    fname = f"markets_{start:%Y%m%d}_{end:%Y%m%d}.json"
    path = CACHE_DIR / fname
    with path.open("w") as f:
        json.dump([asdict(m) for m in markets], f, indent=2)

    resolved = sum(1 for m in markets if m.winner != "UNKNOWN")
    up_wins = sum(1 for m in markets if m.winner == "UP")
    down_wins = sum(1 for m in markets if m.winner == "DOWN")
    print(f"\n[done] wrote {len(markets)} markets to {path}")
    print(f"  resolved: {resolved}/{len(markets)} ({100*resolved/max(1,len(markets)):.1f}%)")
    print(f"  UP wins: {up_wins}  DOWN wins: {down_wins}  (base rate UP: {100*up_wins/max(1,resolved):.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
