"""Phase B parity check — legacy vs new-schema fire counts.

Compares the two parallel loggers the live trader writes:
  legacy: polymarket/logs/live[_<variant>_]<YYYYMMDD>.jsonl   (rows with type=trade)
  new:    polymarket/logs/bot=<variant>/<YYYY-MM-DD>.jsonl    (rows with event=fire)

For each (variant, date) the counts should match exactly. A mismatch means
one logger is dropping rows (or the strategy extraction in Phase C changed
fire semantics), which would invalidate the dry-run record we're using to
gate going live.

Usage:
  python3 -m polymarket.backtest.parity_check --logs /path/to/logs [--variant eth-5m-wide]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


LEGACY_RE = re.compile(r"^live(?:_(.+?))?_(\d{8})\.jsonl$")


def _count_legacy(path: Path) -> int:
    n = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("type") == "trade":
                    n += 1
            except json.JSONDecodeError:
                pass
    return n


def _count_new(path: Path) -> int:
    n = 0
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("event") == "fire":
                    n += 1
            except json.JSONDecodeError:
                pass
    return n


def collect_legacy(logs_dir: Path) -> dict[tuple[str, str], int]:
    """Walk legacy files. Returns {(variant, YYYY-MM-DD): fire_count}."""
    out: dict[tuple[str, str], int] = {}
    for path in sorted(logs_dir.glob("live*.jsonl")):
        m = LEGACY_RE.match(path.name)
        if not m:
            continue
        variant = m.group(1) or "btc-5m"   # untagged file is the legacy btc-5m bot
        ymd = m.group(2)
        iso = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        out[(variant, iso)] = _count_legacy(path)
    return out


def collect_new(logs_dir: Path) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for vdir in sorted(logs_dir.glob("bot=*")):
        variant = vdir.name[len("bot="):]
        for path in sorted(vdir.glob("*.jsonl")):
            iso = path.stem  # YYYY-MM-DD
            out[(variant, iso)] = _count_new(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="~/trading-bot-logs")
    ap.add_argument("--variant", default=None,
                    help="restrict to one bot (e.g. eth-5m-wide)")
    ap.add_argument("--show-matches", action="store_true",
                    help="also print pairs that agree (default: only mismatches)")
    args = ap.parse_args()

    logs_dir = Path(os.path.expanduser(args.logs))
    if not logs_dir.exists():
        print(f"logs dir not found: {logs_dir}", file=sys.stderr)
        return 1

    legacy = collect_legacy(logs_dir)
    new = collect_new(logs_dir)
    all_keys = sorted(set(legacy) | set(new))
    if args.variant:
        all_keys = [k for k in all_keys if k[0] == args.variant]

    by_variant: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for k in all_keys:
        variant, date = k
        L = legacy.get(k, 0)
        N = new.get(k, 0)
        by_variant[variant].append((date, L, N))

    total_legacy = total_new = total_mismatch = 0
    print(f"\nParity check — {logs_dir}\n")
    for variant in sorted(by_variant):
        rows = by_variant[variant]
        v_legacy = sum(L for _, L, _ in rows)
        v_new = sum(N for _, _, N in rows)
        mismatches = [(d, L, N) for d, L, N in rows if L != N]
        total_legacy += v_legacy
        total_new += v_new
        total_mismatch += len(mismatches)

        status = "OK" if not mismatches else f"MISMATCH ({len(mismatches)} day(s))"
        diff = v_new - v_legacy
        diff_str = f"{'+' if diff > 0 else ''}{diff}"
        print(f"{variant:30s}  legacy={v_legacy:5d}  new={v_new:5d}  "
              f"diff={diff_str:>5}  [{status}]")
        if mismatches or args.show_matches:
            for d, L, N in rows:
                if L == N and not args.show_matches:
                    continue
                marker = "  " if L == N else "!!"
                print(f"  {marker} {d}  legacy={L:4d}  new={N:4d}  diff={N-L:+d}")

    print()
    diff = total_new - total_legacy
    print(f"TOTAL  legacy={total_legacy}  new={total_new}  diff={diff:+d}  "
          f"variants_with_mismatch={total_mismatch}")
    return 0 if total_mismatch == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
