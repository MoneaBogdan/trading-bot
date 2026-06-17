"""Offline backtest for news_alpha.

Replays recorded news headlines against captured Polymarket WS orderbook history
and gamma resolution data.

Inputs:
  --news-glob   pattern for raw news jsonl files (default: logs/news/*.jsonl)
  --ob-glob     pattern for orderbook ws jsonl  (default: polymarket/logs/orderbook_ws_*.jsonl)
  --since       ISO date or YYYY-MM-DD, lower bound on news ts
  --dry-llm     skip LLM calls; use deterministic stub classifier (for plumbing tests)

Pipeline per news event:
  1. keyword_prefilter — skip if miss
  2. LLMClassifier.classify — skip if direction=neutral or low confidence
  3. Match candidate Polymarket markets by asset + open at news ts
     (via gamma snapshot if available, else live gamma — flagged in output)
  4. Look up best ask at news ts + decision_latency_s in recorded orderbook
  5. Apply same gates as decide() — sweet band, horizon
  6. Roll forward to market resolution, compute PnL on $size_usdc fill

Output:
  CSV row per fire decision + summary aggregate by asset/reason/confidence-bucket.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from src.strategies.news_alpha.classifier import keyword_prefilter, LLMClassifier
from src.strategies.news_alpha.strategy import Classification, Params


def _iter_news(globs: list[str], since: datetime | None) -> Iterator[dict[str, Any]]:
    for pat in globs:
        for path in sorted(glob.glob(pat)):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        ts = datetime.fromisoformat(row["ts"])
                    except (KeyError, ValueError):
                        continue
                    if since is not None and ts < since:
                        continue
                    row["_ts"] = ts
                    yield row


class _StubLLM:
    """Deterministic classifier for plumbing tests when --dry-llm is set.
    Always 'up' with confidence 0.8 for BTC headlines, else 'neutral'."""
    model = "stub"

    def classify(self, text: str, prefilter=None) -> Classification:
        asset = (prefilter.assets[0] if prefilter and prefilter.assets else "OTHER")
        direction = "up" if asset != "OTHER" else "neutral"
        return Classification(
            asset=asset, direction=direction, confidence=0.8,
            horizon_min=5, reason="stub_test",
        )


def _bucket(conf: float) -> str:
    if conf < 0.6:
        return "lo"
    if conf < 0.8:
        return "mid"
    return "hi"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--news-glob", default="logs/news/*.jsonl",
                    help="glob for raw news jsonl files")
    ap.add_argument("--ob-glob", default="polymarket/logs/orderbook_ws_*.jsonl",
                    help="glob for orderbook WS jsonl files (used in stage 4)")
    ap.add_argument("--since", default=None,
                    help="ISO date or YYYY-MM-DD lower bound on news ts")
    ap.add_argument("--dry-llm", action="store_true",
                    help="use deterministic stub classifier (skip API calls)")
    ap.add_argument("--out", default="logs/news_backtest.csv")
    ap.add_argument("--min-confidence", type=float, default=0.70)
    args = ap.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    params = Params(min_confidence=args.min_confidence)

    if args.dry_llm:
        llm: Any = _StubLLM()
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        llm = LLMClassifier(client=client)

    # NOTE: stages 3-6 (market lookup + ask snapshot + PnL roll-forward) need
    # gamma snapshot + ob WS replay infra that the existing backtest in
    # polymarket/backtest/ has. To avoid duplicating that machinery here, this
    # first pass only outputs the classification step. PnL stage is a TODO that
    # wires into the existing replay_tick.py once the corpus exists.
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_total = n_prefilter_hit = n_llm = n_decision = 0
    by_bucket = defaultdict(int)

    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "channel", "headline", "prefilter_assets",
                    "prefilter_topics", "asset", "direction", "confidence",
                    "horizon_min", "reason", "confidence_bucket", "would_fire"])

        for row in _iter_news([args.news_glob], since):
            n_total += 1
            text = row.get("text", "")
            hit = keyword_prefilter(text)
            if hit is None:
                continue
            n_prefilter_hit += 1
            try:
                cls = llm.classify(text, prefilter=hit)
            except Exception as e:
                print(f"[backtest] LLM error on {row['_ts']}: {e}", file=sys.stderr)
                continue
            n_llm += 1

            would_fire = (
                cls.direction in ("up", "down")
                and cls.confidence >= params.min_confidence
                and cls.asset in params.allowed_assets
            )
            if would_fire:
                n_decision += 1
            by_bucket[(cls.asset, _bucket(cls.confidence), cls.direction)] += 1

            w.writerow([
                row["_ts"].isoformat(),
                row.get("channel", ""),
                text[:200],
                ",".join(hit.assets),
                ",".join(hit.topics),
                cls.asset, cls.direction, f"{cls.confidence:.3f}",
                cls.horizon_min, cls.reason, _bucket(cls.confidence),
                int(would_fire),
            ])

    print(f"\n=== news_alpha classifier-stage backtest ===")
    print(f"news rows scanned: {n_total}")
    print(f"prefilter hits:    {n_prefilter_hit}")
    print(f"LLM classified:    {n_llm}")
    print(f"would-fire (pre-market-checks): {n_decision}")
    print(f"\nby (asset, conf-bucket, direction):")
    for k in sorted(by_bucket):
        print(f"  {k}: {by_bucket[k]}")
    print(f"\nCSV: {out_path}")
    print(f"\nNEXT: feed --would-fire rows into polymarket/backtest/replay_tick.py")
    print(f"      to attach orderbook fills + resolution PnL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
