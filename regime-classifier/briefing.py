"""Daily morning briefing generator.

Reads the current regime classifier output and renders a one-page markdown
briefing. Designed to be cron'd at 06:00 UTC (before London open):

    0 6 * * 1-5  cd /path/to/regime-classifier && .venv/bin/python briefing.py > briefing.md

Pair with the regime-classifier cron (which should run earlier) so the
briefing always uses fresh classifier output.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from cache import RegimeCache
from schema import EventRisk

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]


def format_briefing(pairs: list[str], cache_path: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    cache = RegimeCache(cache_path)
    regime = cache.current(pairs=pairs, now=now)

    fresh = regime.valid_until > now
    age_min = (now - regime.as_of).total_seconds() / 60

    lines: list[str] = []
    lines.append(f"# FX Briefing — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    if not fresh:
        lines.append("> ⚠️ **Regime data is stale** (classifier hasn't run or output expired). Trading is blocked across all pairs as a safety default.")
        lines.append("")

    g = regime.global_
    lines.append("## Global regime")
    lines.append("")
    lines.append(f"- **Risk environment:** {g.risk.value.replace('_', ' ')}")
    lines.append(f"- **Volatility:** {g.volatility.value}")
    lines.append(f"- **USD bias:** {g.usd_bias.value}")
    lines.append(f"- **Classifier confidence:** {g.confidence:.0%}")
    lines.append(f"- **Output age:** {age_min:.0f} min  (valid until {regime.valid_until.strftime('%H:%M UTC')})")
    lines.append("")

    lines.append("## Per-pair status")
    lines.append("")
    lines.append("| Pair | Trade | Regime | Vol | Event risk (8h) | Size × | Stop × | Conf |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for sym, p in regime.pairs.items():
        gate = "✅" if p.trade_allowed else "🚫"
        event_risk_icon = {EventRisk.HIGH: "🔴", EventRisk.MEDIUM: "🟡", EventRisk.LOW: "🟢", EventRisk.NONE: "—"}[p.event_risk_next_8h]
        lines.append(
            f"| {sym} | {gate} | {p.regime.value.replace('_', ' ')} | {p.vol_state.value} | "
            f"{event_risk_icon} {p.event_risk_next_8h.value} | "
            f"{p.suggested_size_mult:.2f} | {p.suggested_stop_mult:.2f} | {p.confidence:.0%} |"
        )
    lines.append("")

    lines.append("## Classifier rationale")
    lines.append("")
    lines.append(f"> {regime.rationale}")
    lines.append("")

    if regime.flags:
        lines.append("## Flags")
        lines.append("")
        for f in regime.flags:
            lines.append(f"- `{f}`")
        lines.append("")

    if regime.cited_headline_ids:
        lines.append(f"_Headlines cited: {len(regime.cited_headline_ids)}_")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    load_dotenv()
    cache_path = os.environ.get("REGIME_CACHE_PATH", "./regime_cache.json")
    out = format_briefing(DEFAULT_PAIRS, cache_path)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
