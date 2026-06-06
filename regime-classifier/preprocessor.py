"""Assembles ClassifierInput from data sources.

Real-world sources are stubbed here. Wire up:
  - ForexFactory / Trading Economics for `calendar_next_24h`
  - Finnhub / Marketaux / RSS for `headlines`
  - Central bank RSS for `cb_speeches`
  - FRED for `macro`
  - Your broker / OANDA candles for `price_context`
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import httpx

from schema import (
    CBSpeech,
    CalendarEvent,
    ClassifierInput,
    Headline,
    MacroState,
    PairContext,
)

logger = logging.getLogger(__name__)

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
HEADLINE_LIMIT = 40
LOOKBACK_HOURS = 24

# Public ForexFactory weekly calendar feed (no auth, refreshed weekly).
# Maintained by FairEconomy as ForexFactory's data partner.
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_IMPACT_MAP = {"High": "high", "Medium": "medium", "Low": "low", "Holiday": "low"}
TRADED_CCYS = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}


def _hid(source: str, title: str, ts: datetime) -> str:
    h = hashlib.sha256(f"{source}|{title}|{ts.isoformat()}".encode()).hexdigest()
    return f"h_{h[:10]}"


def fetch_macro() -> MacroState:
    # TODO: FRED + a quote source (yfinance, broker API)
    return MacroState()


def fetch_calendar(window_hours: int = 24, now: datetime | None = None) -> list[CalendarEvent]:
    """Pull the ForexFactory weekly calendar and filter to the next `window_hours`.

    Returns events sorted by time, restricted to majors (`TRADED_CCYS`). Silently
    returns [] on network/parse error — caller treats missing calendar data as
    "no known events," which the classifier already handles conservatively.
    """
    now = now or datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)

    try:
        resp = httpx.get(FF_CALENDAR_URL, timeout=10.0, headers={"User-Agent": "regime-classifier/1.0"})
        resp.raise_for_status()
        raw = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ForexFactory fetch failed: %s", exc)
        return []

    events: list[CalendarEvent] = []
    for item in raw:
        ccy = item.get("country")
        if ccy not in TRADED_CCYS:
            continue
        impact = FF_IMPACT_MAP.get(item.get("impact", ""))
        if impact not in ("low", "medium", "high"):
            continue
        # FF feed uses ISO 8601 with offset, e.g. "2026-06-06T08:30:00-04:00".
        # Some entries have date-only (all-day events) — skip those for our purposes.
        raw_date = item.get("date", "")
        try:
            ts = datetime.fromisoformat(raw_date)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        if ts < now or ts > window_end:
            continue
        events.append(CalendarEvent(
            time=ts,
            ccy=ccy,
            event=item.get("title", "Unknown"),
            impact=impact,  # type: ignore[arg-type]
        ))

    events.sort(key=lambda e: e.time)
    return events


def fetch_headlines(lookback_hours: int = LOOKBACK_HOURS) -> list[Headline]:
    # TODO: Finnhub / Marketaux / RSS aggregator
    raw: list[tuple[str, str, datetime, list[str]]] = []
    out: list[Headline] = []
    for source, title, ts, tickers in raw:
        out.append(Headline(id=_hid(source, title, ts), ts=ts, source=source, title=title, tickers=tickers))
    return out[:HEADLINE_LIMIT]


def fetch_cb_speeches(lookback_hours: int = LOOKBACK_HOURS) -> list[CBSpeech]:
    # TODO: scrape Fed/ECB/BoE/BoJ RSS
    return []


def fetch_price_context(pairs: list[str]) -> dict[str, PairContext]:
    # TODO: pull from broker candles; compute ATR(14), 3-mo avg ATR, 20-day trend direction
    return {}


def build_input(pairs: list[str] | None = None, now: datetime | None = None) -> ClassifierInput:
    pairs = pairs or DEFAULT_PAIRS
    now = now or datetime.now(timezone.utc)
    return ClassifierInput(
        as_of=now,
        lookback_hours=LOOKBACK_HOURS,
        pairs=pairs,
        macro=fetch_macro(),
        calendar_next_24h=fetch_calendar(now=now),
        headlines=fetch_headlines(),
        cb_speeches=fetch_cb_speeches(),
        price_context=fetch_price_context(pairs),
    )


# --- demo fixture for end-to-end testing without live data sources ---

def demo_input(now: datetime | None = None) -> ClassifierInput:
    now = now or datetime.now(timezone.utc)
    pairs = DEFAULT_PAIRS

    headlines_raw = [
        ("Reuters", "Fed's Powell signals patience on rate cuts amid sticky services inflation", now - timedelta(hours=2), ["USD"]),
        ("Bloomberg", "ECB's Lagarde says disinflation 'on track', markets price June cut", now - timedelta(hours=5), ["EUR"]),
        ("FT", "Yen slides past 158 as BoJ holds, intervention chatter resumes", now - timedelta(hours=8), ["JPY", "USD"]),
        ("Reuters", "China stimulus disappoints, copper and AUD slip", now - timedelta(hours=11), ["AUD", "CNY"]),
        ("WSJ", "VIX climbs as tech earnings spark risk-off rotation", now - timedelta(hours=14), ["SPX", "VIX"]),
    ]
    headlines = [
        Headline(id=_hid(s, t, ts), ts=ts, source=s, title=t, tickers=tk)
        for s, t, ts, tk in headlines_raw
    ]

    cb_raw = [
        ("Powell", "Senate Banking testimony", "Stressed data-dependence; pushed back on imminent cuts.", now - timedelta(hours=2)),
        ("Lagarde", "ECB press conference", "Confident on disinflation; left door open to June easing.", now - timedelta(hours=5)),
    ]
    cb_speeches = [
        CBSpeech(id=f"cb_{i}", ts=ts, speaker=sp, venue=v, summary=sm)
        for i, (sp, v, sm, ts) in enumerate(cb_raw)
    ]

    return ClassifierInput(
        as_of=now,
        lookback_hours=LOOKBACK_HOURS,
        pairs=pairs,
        macro=MacroState(
            us_10y=4.21, us_10y_change_1d=0.04,
            dxy=104.3, dxy_change_1d=-0.20,
            vix=14.1, vix_change_1d=0.30,
            spx_change_1d=-0.40,
        ),
        calendar_next_24h=[
            CalendarEvent(time=now + timedelta(hours=4), ccy="USD", event="FOMC Minutes", impact="high"),
            CalendarEvent(time=now + timedelta(hours=20), ccy="EUR", event="ECB Bulletin", impact="medium"),
        ],
        headlines=headlines,
        cb_speeches=cb_speeches,
        price_context={
            "EURUSD": PairContext(atr_14d=0.0062, atr_pct_of_3m_avg=1.4, trend_20d="down", last_close=1.0712),
            "GBPUSD": PairContext(atr_14d=0.0078, atr_pct_of_3m_avg=1.2, trend_20d="down", last_close=1.2554),
            "USDJPY": PairContext(atr_14d=0.92,   atr_pct_of_3m_avg=1.6, trend_20d="up",   last_close=158.12),
            "AUDUSD": PairContext(atr_14d=0.0054, atr_pct_of_3m_avg=1.1, trend_20d="down", last_close=0.6588),
        },
    )
