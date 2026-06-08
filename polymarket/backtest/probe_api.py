"""Probe what Polymarket APIs return for a recently-closed BTC Up/Down market.

We want to confirm:
  1. Gamma can list closed BTC 5-min markets (last 24h)
  2. CLOB /prices-history returns time-series for a known token_id
  3. Data API /trades returns executed-trade history for a market
  4. Either source has enough granularity for backtest replay

Output: prints summaries + sample rows for each endpoint we hit.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

import httpx

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

TITLE_REGEX = re.compile(
    r"Bitcoin Up or Down\s*-\s*([A-Za-z]+ \d+),\s*"
    r"(\d{1,2}:\d{2}(?:AM|PM))-(\d{1,2}:\d{2}(?:AM|PM))\s*ET",
    re.IGNORECASE,
)


def find_closed_btc_market(client: httpx.Client) -> dict | None:
    """Look back over the last 24h for a resolved BTC 5-min market."""
    now = datetime.now(timezone.utc)
    end_max = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_min = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[gamma] searching closed BTC markets ending between {end_min} and {end_max}")
    r = client.get(
        f"{GAMMA}/events",
        params={
            "limit": 500,
            "closed": "true",
            "end_date_min": end_min,
            "end_date_max": end_max,
            "order": "endDate",
            "ascending": "false",
        },
    )
    r.raise_for_status()
    events = r.json()
    print(f"[gamma] received {len(events)} closed events in window")
    for e in events:
        title = e.get("title", "")
        if not TITLE_REGEX.search(title):
            continue
        if not e.get("markets"):
            continue
        return e
    return None


def probe_prices_history(client: httpx.Client, token_id: str, start_ts: int, end_ts: int) -> None:
    print(f"\n[clob] /prices-history?market={token_id[:12]}…  range={start_ts}..{end_ts}")
    for fidelity in (1, 5, 60):
        r = client.get(
            f"{CLOB}/prices-history",
            params={"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": fidelity},
        )
        if r.status_code != 200:
            print(f"  fidelity={fidelity}min -> HTTP {r.status_code}: {r.text[:200]}")
            continue
        data = r.json()
        pts = data.get("history", [])
        print(f"  fidelity={fidelity}min -> {len(pts)} points")
        if pts:
            print(f"    first: {pts[0]}")
            print(f"    last:  {pts[-1]}")
            if len(pts) >= 2:
                gap = pts[1]["t"] - pts[0]["t"]
                print(f"    gap between samples: {gap}s")


def probe_trades(client: httpx.Client, condition_id: str) -> None:
    print(f"\n[data-api] /trades?market={condition_id[:14]}…")
    for path in ("/trades", "/v1/trades"):
        r = client.get(f"{DATA}{path}", params={"market": condition_id, "limit": 500})
        print(f"  {path} -> HTTP {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            n = len(data) if isinstance(data, list) else len(data.get("data", []) or [])
            print(f"    rows: {n}")
            rows = data if isinstance(data, list) else data.get("data", [])
            if rows:
                print(f"    sample[0]: {json.dumps(rows[0], indent=2)[:400]}")
            return
        else:
            print(f"    body: {r.text[:200]}")


def main() -> int:
    with httpx.Client(timeout=20.0) as client:
        event = find_closed_btc_market(client)
        if not event:
            print("[fail] no recently closed BTC Up/Down market found in last 24h")
            return 1

        mk = event["markets"][0]
        title = event["title"]
        condition_id = mk.get("conditionId")
        tok_ids = json.loads(mk.get("clobTokenIds") or "[]")
        end_iso = event.get("endDate", "")
        start_iso = event.get("startDate", "")
        print(f"\n[picked] {title}")
        print(f"  conditionId: {condition_id}")
        print(f"  tokens: up={tok_ids[0][:12]}… down={tok_ids[1][:12]}…")
        print(f"  start: {start_iso}")
        print(f"  end:   {end_iso}")
        print(f"  outcomePrices: {mk.get('outcomePrices')}  (final resolved values)")

        # Window: 30 min before market close, generous padding
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        end_ts = int(end_dt.timestamp())
        start_ts = end_ts - 30 * 60

        probe_prices_history(client, tok_ids[0], start_ts, end_ts + 60)
        probe_trades(client, condition_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
