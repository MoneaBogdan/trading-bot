"""Polymarket Gamma API client — market discovery only, no auth required."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Polymarket Up/Down market title formats observed 2026-06:
#   5-min / 15-min : "Bitcoin Up or Down - June 7, 4:25AM-4:30AM ET"
#   Hourly         : "Bitcoin Up or Down - June 16, 5AM ET"
ASSET_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
}


def _range_regex(asset: str) -> re.Pattern:
    """Matches '<Asset> Up or Down - DATE, START-END ET' (5-min, 15-min, etc.)."""
    asset_name = ASSET_NAMES[asset.upper()]
    return re.compile(
        rf"{asset_name} Up or Down\s*-\s*([A-Za-z]+ \d+),\s*"
        r"(\d{1,2}:\d{2}(?:AM|PM))-(\d{1,2}:\d{2}(?:AM|PM))\s*ET",
        re.IGNORECASE,
    )


def _hourly_regex(asset: str) -> re.Pattern:
    """Matches '<Asset> Up or Down - DATE, NAM ET' (hourly markets)."""
    asset_name = ASSET_NAMES[asset.upper()]
    return re.compile(
        rf"{asset_name} Up or Down\s*-\s*([A-Za-z]+ \d+),\s*"
        r"(\d{1,2}(?:AM|PM))\s*ET\s*$",
        re.IGNORECASE,
    )


TITLE_REGEX = _range_regex("BTC")  # backward-compat for any external import


@dataclass
class BtcMarket:
    """A single Up/Down market (any asset, any timeframe).
    Kept named BtcMarket for backward compat with existing imports.
    """
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


def discover_markets(
    asset: str = "BTC",
    timeframe_min: int = 5,
    window_horizon_min: int = 60,
    client: httpx.Client | None = None,
) -> list[BtcMarket]:
    """Return upcoming Up/Down markets for the given asset + timeframe whose
    window ends within the next `window_horizon_min` minutes.

    asset ∈ {"BTC", "ETH", "SOL"}.
    timeframe_min: window duration in minutes (5 for 5-min markets, 60 for hourly).
    """
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
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

    if timeframe_min == 60:
        title_re = _hourly_regex(asset)
        is_hourly = True
    else:
        title_re = _range_regex(asset)
        is_hourly = False
    now = datetime.now(timezone.utc)
    horizon = now.timestamp() + window_horizon_min * 60
    out: list[BtcMarket] = []
    for e in events:
        title = e.get("title", "")
        m = title_re.search(title)
        if not m:
            continue
        if not is_hourly:
            _date_part, t1, t2 = m.groups()
            if _minutes_between(t1, t2) != timeframe_min:
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


def discover_btc_markets(window_horizon_min: int = 60, client: httpx.Client | None = None) -> list[BtcMarket]:
    """Backward-compat alias: BTC 5-min markets only.
    New callers should use discover_markets(asset, timeframe_min, ...).
    """
    return discover_markets("BTC", timeframe_min=5,
                            window_horizon_min=window_horizon_min, client=client)


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
