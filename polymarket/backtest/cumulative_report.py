"""Cumulative per-bot PnL across all `bot=*/YYYY-MM-DD.jsonl` logs.

Walks every `bot=*/` directory, collects every `fire` event up to `asof_date`,
resolves each fire's market via Polymarket gamma, and produces a markdown table
with per-bot first-day, last-day, fires, resolved counts, win rate, and total
PnL.

Used as a CLI for ad-hoc roll-ups, and imported by `daily_report.py` so every
daily report ends with a "Cumulative since first fire" section anchored to that
report's date (so re-generating an old report does not retroactively include
future fires).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from polymarket.backtest.daily_report import _fire_cid, _fire_pnl, _resolve_markets


def _collect_fires(logs_dir: Path, asof_date: str | None
                   ) -> tuple[dict[str, list[tuple[str, dict]]],
                              dict[str, set[str]]]:
    """Return (fires_by_bot, cids_by_date), filtered to dates <= asof_date."""
    fires_by_bot: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    cids_by_date: dict[str, set[str]] = defaultdict(set)
    for vdir in sorted(logs_dir.glob("bot=*")):
        variant = vdir.name[len("bot="):]
        for path in sorted(vdir.glob("*.jsonl")):
            date_str = path.stem  # YYYY-MM-DD
            if asof_date is not None and date_str > asof_date:
                continue
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("event") != "fire":
                        continue
                    fires_by_bot[variant].append((date_str, r))
                    cid = _fire_cid(r)
                    if cid:
                        cids_by_date[date_str].add(cid)
    return fires_by_bot, cids_by_date


def build_cumulative_markdown(logs_dir: Path, asof_date: str | None = None,
                              verbose: bool = False) -> str:
    fires_by_bot, cids_by_date = _collect_fires(logs_dir, asof_date)
    if not fires_by_bot:
        return "_(no fires found in logs)_"

    winners: dict[str, str] = {}
    if verbose:
        n = sum(len(v) for v in cids_by_date.values())
        print(f"[cumulative] resolving {n} fires across {len(cids_by_date)} dates...",
              file=sys.stderr)
    for d in sorted(cids_by_date):
        won = _resolve_markets(cids_by_date[d], d)
        winners.update(won)
        if verbose:
            print(f"  {d}: resolved {len(won)}/{len(cids_by_date[d])}", file=sys.stderr)

    rows = []
    grand_pnl = 0.0
    grand_fires = grand_wins = grand_losses = grand_unres = 0
    for bot in sorted(fires_by_bot):
        fires = fires_by_bot[bot]
        first = min(d for d, _ in fires)
        last = max(d for d, _ in fires)
        wins = losses = unresolved = 0
        pnl = 0.0
        for _, fire in fires:
            cid = _fire_cid(fire)
            winner = winners.get(cid) if cid else None
            p, status = _fire_pnl(fire, winner)
            pnl += p
            if status == "WIN":
                wins += 1
            elif status == "LOSS":
                losses += 1
            else:
                unresolved += 1
        resolved = wins + losses
        win_rate = (wins / resolved * 100) if resolved else 0.0
        rows.append((bot, first, last, len(fires), resolved, wins, losses,
                     unresolved, win_rate, pnl))
        grand_pnl += pnl
        grand_fires += len(fires)
        grand_wins += wins
        grand_losses += losses
        grand_unres += unresolved

    lines = []
    asof_note = f" (as of {asof_date})" if asof_date else ""
    lines.append(f"## Cumulative since each bot's first fire{asof_note}")
    lines.append("")
    lines.append("Per-bot totals across ALL `bot=*/` logs the script can see, "
                 "filtered to fires on or before this report's date so re-running "
                 "an old report stays anchored. PnL assumes $5/order on rows missing "
                 "`size_usdc` and resolves via Polymarket gamma — unresolved markets "
                 "contribute $0 until they settle.")
    lines.append("")
    lines.append("| Bot | First → Last | Fires | Resolved | W | L | Pending | Win% | PnL (USDC) |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        pnl_str = f"{'+' if r[9] >= 0 else ''}{r[9]:.2f}"
        win_str = f"{r[8]:.0f}%" if (r[5] + r[6]) else "—"
        lines.append(
            f"| `{r[0]}` | {r[1]} → {r[2]} | {r[3]} | {r[4]} | "
            f"{r[5]} | {r[6]} | {r[7]} | {win_str} | {pnl_str} |"
        )
    grand_res = grand_wins + grand_losses
    grand_wr = f"{(grand_wins / grand_res * 100):.0f}%" if grand_res else "—"
    grand_pnl_str = f"{'+' if grand_pnl >= 0 else ''}{grand_pnl:.2f}"
    lines.append(
        f"| **TOTAL** | — | **{grand_fires}** | **{grand_res}** | "
        f"**{grand_wins}** | **{grand_losses}** | **{grand_unres}** | "
        f"**{grand_wr}** | **{grand_pnl_str}** |"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="~/trading-bot-logs",
                    help="dir containing bot=*/ trees")
    ap.add_argument("--asof", default=None,
                    help="UTC date YYYY-MM-DD; include fires on or before this date "
                         "(default: all fires)")
    args = ap.parse_args()
    logs_dir = Path(os.path.expanduser(args.logs))
    if not logs_dir.exists():
        print(f"logs dir not found: {logs_dir}", file=sys.stderr)
        return 1
    print(build_cumulative_markdown(logs_dir, args.asof, verbose=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
