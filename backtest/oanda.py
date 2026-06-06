"""Minimal OANDA v20 REST client. Candles only — orders live in live/."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import pandas as pd

OANDA_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "trade": "https://api-fxtrade.oanda.com",
}

# OANDA caps a single candles request at 5000 bars.
MAX_BARS_PER_REQ = 5000

# Map common pair strings to OANDA instrument names.
def to_oanda_instrument(pair: str) -> str:
    p = pair.upper().replace("/", "").replace("_", "")
    if len(p) != 6:
        raise ValueError(f"unrecognized pair: {pair}")
    return f"{p[:3]}_{p[3:]}"


class OandaClient:
    def __init__(self, token: str | None = None, env: str | None = None):
        self.token = token or os.environ.get("OANDA_API_TOKEN")
        if not self.token:
            raise RuntimeError("OANDA_API_TOKEN not set")
        env = env or os.environ.get("OANDA_ENV", "practice")
        if env not in OANDA_HOSTS:
            raise ValueError(f"OANDA_ENV must be 'practice' or 'trade', got {env!r}")
        self.host = OANDA_HOSTS[env]
        self._client = httpx.Client(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept-Datetime-Format": "RFC3339",
            },
        )

    def fetch_candles(
        self,
        pair: str,
        granularity: str,
        start: datetime,
        end: datetime,
        price: str = "M",  # M=mid, B=bid, A=ask
    ) -> pd.DataFrame:
        """Fetch all candles in [start, end). Pages through 5000-bar chunks."""
        instrument = to_oanda_instrument(pair)
        url = f"{self.host}/v3/instruments/{instrument}/candles"
        cursor = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        frames: list[pd.DataFrame] = []

        while cursor < end_utc:
            params = {
                "granularity": granularity,
                "price": price,
                "from": cursor.isoformat().replace("+00:00", "Z"),
                "to": end_utc.isoformat().replace("+00:00", "Z"),
                "count": MAX_BARS_PER_REQ,
                "includeFirst": "true",
            }
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json().get("candles", [])
            if not payload:
                break

            rows = []
            for c in payload:
                if not c.get("complete"):
                    continue
                ohlc = c["mid"] if price == "M" else c.get("bid" if price == "B" else "ask")
                rows.append({
                    "ts": pd.Timestamp(c["time"]).tz_convert("UTC"),
                    "open": float(ohlc["o"]),
                    "high": float(ohlc["h"]),
                    "low": float(ohlc["l"]),
                    "close": float(ohlc["c"]),
                    "volume": int(c.get("volume", 0)),
                })
            if not rows:
                break
            df = pd.DataFrame(rows)
            frames.append(df)

            last_ts = df["ts"].iloc[-1].to_pydatetime()
            # Advance past the last bar to avoid re-fetching it.
            if last_ts <= cursor:
                break
            cursor = last_ts + pd.Timedelta(seconds=1)

            if len(payload) < MAX_BARS_PER_REQ:
                break  # last page

        if not frames:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
        return out

    def close(self) -> None:
        self._client.close()
