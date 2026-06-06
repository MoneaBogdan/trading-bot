"""Entry point: run one classification cycle and update the cache.

Usage:
    python main.py            # use live data sources (currently stubbed)
    python main.py --demo     # use demo_input fixture (no live sources needed)

Schedule this from cron/systemd every hour during active sessions:
    0 6-22 * * 1-5  cd /path/to/regime-classifier && .venv/bin/python main.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

from cache import RegimeCache, log_run
from classifier import classify
from preprocessor import build_input, demo_input
from schema import conservative_default


def _summary(inputs) -> dict:
    return {
        "as_of": inputs.as_of.isoformat(),
        "pairs": inputs.pairs,
        "n_headlines": len(inputs.headlines),
        "n_calendar_events": len(inputs.calendar_next_24h),
        "n_cb_speeches": len(inputs.cb_speeches),
    }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Use fixture inputs instead of live data")
    args = parser.parse_args()

    cache_path = os.environ.get("REGIME_CACHE_PATH", "./regime_cache.json")
    log_path = os.environ.get("LOG_PATH", "./regime_runs.jsonl")

    inputs = demo_input() if args.demo else build_input()
    cache = RegimeCache(cache_path)
    client = anthropic.Anthropic()

    started = time.monotonic()
    output = None
    error = None
    try:
        output = classify(client, inputs)
        cache.write(output)
        print(f"OK: regime written to {cache_path}")
        print(f"  global: {output.global_.risk.value} / vol={output.global_.volatility.value} / usd={output.global_.usd_bias.value} (conf={output.global_.confidence:.2f})")
        for sym, pr in output.pairs.items():
            gate = "TRADE" if pr.trade_allowed else "BLOCK"
            print(f"  {sym}: {gate} regime={pr.regime.value} size_x{pr.suggested_size_mult:.2f} stop_x{pr.suggested_stop_mult:.2f} (conf={pr.confidence:.2f})")
        print(f"  rationale: {output.rationale}")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {error}", file=sys.stderr)
        # Don't overwrite the cache on failure — strategies fall back to
        # conservative defaults once the existing entry expires.
        fallback = conservative_default(
            as_of=datetime.now(timezone.utc),
            valid_until=datetime.now(timezone.utc),
            pairs=inputs.pairs,
        )
        output = fallback
    finally:
        latency_ms = (time.monotonic() - started) * 1000
        log_run(log_path, _summary(inputs), output, error, latency_ms)

    return 0 if error is None else 1


if __name__ == "__main__":
    sys.exit(main())
