"""Polymarket Gamma API client — market discovery only, no auth required."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Each Bitcoin Up/Down market title looks like:
#   "Bitcoin Up or Down - June 7, 4:25AM-4:30AM ET"
TITLE_REGEX = re.compile(
    r"Bitcoin Up or Down\s*-\s*([A-Za-z]+ \d+),\s*"
    r"(\d{1,2}:\d{2}(?:AM|PM))-(\d{1,2}:\d{2}(?:AM|PM))\s*ET",
    re.IGNORECASE,
)


@dataclass
class BtcMarket:
    """A single 5-min Bitcoin Up/Down market."""
    event_id: str
    market_id: str
    condition_id: str
    title: str
    window_start_iso: str           # market opens / window begins (UTC ISO)
    window_end_iso: str             # window resolution time (UTC ISO)
    end_dt: datetime                # parsed window end as UTC datetime
    up_token_id: str                # CLOB token id for "Up" outcome
    down_token_id: str              # CLOB token id for "Down" outcome
    up_price: float | None
    down_price: float | None
    liquidity: float
    volume: float
    min_tick: float
    min_size: float


def _parse_outcome_prices(s: str | None) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    try:
        prices = json.loads(s)
        if len(prices) == 2:
            return float(prices[0]), float(prices[1])
    except Exception:
        pass
    return None, None


def _parse_token_ids(s: str | None) -> tuple[str | None, str | None]:
    if not s:
        return None, None
    try:
        ids = json.loads(s)
        if len(ids) == 2:
            return str(ids[0]), str(ids[1])
    except Exception:
        pass
    return None, None


def discover_btc_markets(window_horizon_min: int = 60, client: httpx.Client | None = None) -> list[BtcMarket]:
    """Return upcoming Bitcoin Up/Down markets whose window ends within the next
    `window_horizon_min` minutes. Useful for picking which market to trade.

    Skips longer-window markets (e.g. 15-min "4:15AM-4:30AM ET" spans) — those
    have a different liquidity profile and aren't the latency-arb target.
    """
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        # Pull events ending now-or-later, soonest first.
        # NB: without end_date_min, the API returns events sorted by absolute
        # endDate including ancient unresolved ones — we'd get garbage.
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = client.get(
            f"{GAMMA_BASE}/events",
            params={
                "limit": 500,
                "closed": "false",
                "end_date_min": now_iso,
                "order": "endDate",
                "ascending": "true",
            },
        )
        r.raise_for_status()
        events = r.json()
    finally:
        if own:
            client.close()

    now = datetime.now(timezone.utc)
    horizon = now.timestamp() + window_horizon_min * 60
    out: list[BtcMarket] = []
    for e in events:
        title = e.get("title", "")
        m = TITLE_REGEX.search(title)
        if not m:
            continue
        # Only keep 5-minute windows.
        date_part, t1, t2 = m.groups()
        if _minutes_between(t1, t2) != 5:
            continue
        end_iso = e.get("endDate") or ""
        try:
            end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        except ValueError:
            continue
        if end_dt <= now or end_dt.timestamp() > horizon:
            continue
        if not e.get("markets"):
            continue
        mk = e["markets"][0]
        up_tok, down_tok = _parse_token_ids(mk.get("clobTokenIds"))
        if not up_tok or not down_tok:
            continue
        up_p, down_p = _parse_outcome_prices(mk.get("outcomePrices"))
        out.append(BtcMarket(
            event_id=str(e.get("id")),
            market_id=str(mk.get("id")),
            condition_id=str(mk.get("conditionId")),
            title=title,
            window_start_iso=e.get("startDate", ""),
            window_end_iso=end_iso,
            end_dt=end_dt,
            up_token_id=up_tok,
            down_token_id=down_tok,
            up_price=up_p,
            down_price=down_p,
            liquidity=float(mk.get("liquidityNum") or 0),
            volume=float(mk.get("volumeNum") or 0),
            min_tick=float(mk.get("orderPriceMinTickSize") or 0.01),
            min_size=float(mk.get("orderMinSize") or 5.0),
        ))
    out.sort(key=lambda x: x.end_dt)
    return out


def _minutes_between(t1: str, t2: str) -> int:
    """Parse '4:25AM' and '4:30AM' → 5. Handles AM/PM rollover within a day."""
    def to_min(s: str) -> int:
        h, rest = s.split(":")
        m = int(rest[:2])
        ampm = rest[2:].upper()
        hh = int(h) % 12
        if ampm == "PM":
            hh += 12
        return hh * 60 + m
    a = to_min(t1)
    b = to_min(t2)
    if b < a:
        b += 24 * 60
    return b - a
