"""How a deterministic strategy consumes the regime cache.

The strategy keeps full veto power: the regime can only shrink size or block
trades, never force one. This file is illustrative — wire it into your actual
strategy / execution layer.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from cache import RegimeCache


def should_enter(symbol: str, entry_signal_fired: bool, base_size: float, base_stop: float) -> tuple[bool, float, float]:
    """Returns (enter, size, stop_distance)."""
    load_dotenv()
    cache = RegimeCache(os.environ.get("REGIME_CACHE_PATH", "./regime_cache.json"))
    regime = cache.current(pairs=[symbol])

    pair = regime.pairs.get(symbol)
    if pair is None or not pair.trade_allowed:
        return False, 0.0, 0.0
    if not entry_signal_fired:
        return False, 0.0, 0.0

    return True, base_size * pair.suggested_size_mult, base_stop * pair.suggested_stop_mult


if __name__ == "__main__":
    enter, size, stop = should_enter("EURUSD", entry_signal_fired=True, base_size=10_000, base_stop=20.0)
    print(f"enter={enter} size={size} stop_pips={stop}")
