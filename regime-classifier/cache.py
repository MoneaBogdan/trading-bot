"""On-disk regime cache.

Strategies read the cache; if the current regime is expired (now > valid_until),
they get the conservative default instead. This decouples strategy execution
from classifier latency / outages.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from schema import RegimeOutput, conservative_default


class RegimeCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, regime: RegimeOutput) -> None:
        self.path.write_text(regime.model_dump_json(by_alias=True, indent=2))

    def read(self) -> RegimeOutput | None:
        if not self.path.exists():
            return None
        return RegimeOutput.model_validate_json(self.path.read_text())

    def current(self, pairs: list[str], now: datetime | None = None) -> RegimeOutput:
        """Return the cached regime if still valid, else a conservative default."""
        now = now or datetime.now(timezone.utc)
        cached = self.read()
        if cached is None or now > cached.valid_until:
            return conservative_default(
                as_of=now,
                valid_until=now,
                pairs=pairs,
            )
        return cached


def log_run(log_path: str | Path, inputs_summary: dict, output: RegimeOutput | None, error: str | None, latency_ms: float) -> None:
    """Append-only JSONL log for observability and later A/B analysis."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "latency_ms": latency_ms,
        "inputs_summary": inputs_summary,
        "output": json.loads(output.model_dump_json(by_alias=True)) if output else None,
        "error": error,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")
