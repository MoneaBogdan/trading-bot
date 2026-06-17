"""Shared types for news source adapters.

Each source pushes `Headline` objects onto an `asyncio.Queue` consumed by
either the recorder (writes to JSONL) or the live bot runner (feeds the
classifier).

The schema is intentionally minimal — anything extra goes into `raw` so we
can backtest with the original payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Headline:
    ts: datetime                 # event time at the source (UTC)
    ts_received: datetime        # when our process saw it (UTC)
    source: str                  # source kind, e.g. "telegram" | "treeofalpha_rest"
    channel: str                 # subsource label, e.g. channel handle or REST sourceName
    message_id: str              # source-native unique id (string for portability)
    text: str                    # headline body
    raw: dict[str, Any]          # original payload — kept for replay
