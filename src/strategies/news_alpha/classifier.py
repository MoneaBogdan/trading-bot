"""Layered news classifier: keyword/asset prefilter, then Claude Sonnet.

Two-stage to control cost and latency:

  1. `keyword_prefilter(text)` — instant, free, rule-based. Returns a candidate
     dict `{assets: [...], topics: [...]}` if the headline contains at least
     one trading-relevant keyword AND mentions at least one known asset, else
     None. Drops ~95% of noise headlines (price commentary, generic news, etc).

  2. `classify_llm(text, prefilter_hit)` — Claude Sonnet call with a strict
     JSON schema. Only invoked on prefilter hits. ~$0.003-0.01/headline. Returns
     a `Classification` from strategy.py.

This module is PURE in the prefilter (no I/O) and isolated in the LLM call
(takes an `anthropic.Client` instance — callers create it once at boot).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .strategy import Classification


# ---- Keyword & asset detection ----

# Known assets: maps tokens/words → canonical ticker. Order-insensitive match,
# word-boundary anchored.
ASSET_ALIASES: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ether": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
}

# Trading-relevant keyword sets — presence of ANY one of these alongside a known
# asset promotes the headline to LLM classification. Tuned to be permissive
# (rather miss precision in stage 1 than miss recall — stage 2 is the filter).
KEYWORDS = {
    # Regulatory / institutional
    "regulatory": [
        r"\bsec\b", r"\bcftc\b", r"\bfinma\b", r"\bfca\b", r"\besma\b",
        r"\betf\b", r"\bspot etf\b",
        r"approv(ed|al|es)", r"reject(ed|s)?", r"deni(ed|al|es)",
        r"halt(ed|s)?", r"ban(ned|s)?",
        r"\binvestigat(ion|ing|es)\b", r"\bsubpoena\b", r"\blawsuit\b",
        r"\bsettl(ed|ement)\b", r"\bfin(ed|e of)\b",
    ],
    # Macro
    "macro": [
        r"\bcpi\b", r"\bppi\b", r"\bnfp\b", r"\bfomc\b", r"\bfed\b",
        r"rate (cut|hike|decision)", r"\bgdp\b", r"\bunemployment\b",
        r"\binflation\b",
    ],
    # Exchange / market structure
    "exchange": [
        r"\blist(ing|ed|s)\b", r"\bdelist(ing|ed|s)\b",
        r"\bbinance\b", r"\bcoinbase\b", r"\bbybit\b", r"\bokx\b", r"\bkraken\b",
        r"\bhack(ed|s)?\b", r"\bexploit(ed|s)?\b", r"\bdrain(ed|s)?\b",
        r"\boutage\b", r"\bdown\b.*\bexchange\b",
    ],
    # Issuer / ETF flow
    "issuer": [
        r"blackrock", r"fidelity", r"grayscale", r"vaneck", r"bitwise",
        r"\bishares\b",
        r"\binflow(s)?\b", r"\boutflow(s)?\b",
    ],
    # On-chain / protocol
    "protocol": [
        r"\bhardfork\b", r"\bfork\b", r"\bupgrade\b", r"\bmainnet\b",
        r"\bdepeg\b", r"\bliquidation(s)?\b", r"\bwhale\b",
    ],
    # Sentiment-strong phrasing
    "sentiment": [
        r"\bbreaking\b", r"\bjust in\b", r"\bofficial\b",
        r"\bcrash(ed|es|ing)?\b", r"\bsurge(d|s|ing)?\b", r"\bplunge(d|s|ing)?\b",
        r"\bspike(d|s|ing)?\b", r"\brally(ing|ies|ied)?\b",
        r"\ball.?time high\b", r"\bath\b", r"\brecord\b",
    ],
}

_keyword_patterns: dict[str, list[re.Pattern]] = {
    topic: [re.compile(p, re.IGNORECASE) for p in patterns]
    for topic, patterns in KEYWORDS.items()
}
_asset_pattern = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in ASSET_ALIASES) + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PrefilterHit:
    assets: tuple[str, ...]
    topics: tuple[str, ...]


def keyword_prefilter(text: str) -> PrefilterHit | None:
    """Return a hit if `text` mentions at least one known asset AND at least
    one trading-relevant keyword. Otherwise None. PURE."""
    if not text:
        return None
    asset_matches = {ASSET_ALIASES[m.group(1).lower()] for m in _asset_pattern.finditer(text)}
    if not asset_matches:
        return None
    topics_hit: list[str] = []
    for topic, patterns in _keyword_patterns.items():
        if any(p.search(text) for p in patterns):
            topics_hit.append(topic)
    if not topics_hit:
        return None
    return PrefilterHit(
        assets=tuple(sorted(asset_matches)),
        topics=tuple(topics_hit),
    )


# ---- LLM classification (Claude Sonnet) ----

# Default model — overridable via constructor argument so callers can switch
# without code changes (e.g., to Haiku for cost-sensitive batch classification).
DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a crypto trading news classifier. Given a short news \
headline (and optional metadata), output a strict JSON object indicating which \
asset is affected, the expected price direction over a short horizon, and your \
confidence.

Output JSON ONLY, no prose, with this exact shape:
{
  "asset": "BTC" | "ETH" | "SOL" | "OTHER",
  "direction": "up" | "down" | "neutral",
  "confidence": <float 0..1>,
  "horizon_min": <integer in {1, 5, 15, 60}>,
  "reason": "<lower_snake_case, ≤24 chars, e.g. etf_approval, hack, listing>"
}

Calibration:
- "up" / "down" only when the headline is materially price-moving in that \
direction on the order of minutes to one hour. Otherwise "neutral".
- Be conservative with confidence. 0.9+ is reserved for unambiguous, primary-\
source-style headlines ("SEC approves ETH spot ETF"). 0.5-0.7 is typical for \
plausible-but-noisy items. Below 0.5, prefer "neutral".
- "horizon_min": typical time for the move to play out. ETF approvals ~60min. \
Exchange hack ~5-15min. CPI surprise ~5min. Tweet/rumor ~1-5min.
- "OTHER" for assets we don't trade; downstream will skip.
- "reason" is a stable tag we'll use to bucket performance later. Use the same \
tag for the same kind of event across headlines.
"""


@dataclass
class LLMClassifier:
    """Wraps an Anthropic client. Construct once at boot; reuse across calls."""
    client: Any                           # anthropic.Anthropic
    model: str = DEFAULT_MODEL
    max_tokens: int = 200

    def classify(self, text: str, prefilter: PrefilterHit | None = None) -> Classification:
        """Synchronous classification. Raises on API errors (caller decides
        whether to retry or drop the event)."""
        user_msg = self._format_user_msg(text, prefilter)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text if resp.content else ""
        return self._parse(raw)

    @staticmethod
    def _format_user_msg(text: str, prefilter: PrefilterHit | None) -> str:
        if prefilter is None:
            return f"Headline: {text}"
        return (
            f"Headline: {text}\n"
            f"Prefilter assets: {','.join(prefilter.assets)}\n"
            f"Prefilter topics: {','.join(prefilter.topics)}"
        )

    @staticmethod
    def _parse(raw: str) -> Classification:
        """Tolerant JSON extraction — handles models that wrap JSON in prose."""
        # Strip code fences if present
        s = raw.strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*", "", s)
            s = re.sub(r"\s*```\s*$", "", s)
        # Find the first {...} block
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON object in model output: {raw!r}")
        obj = json.loads(m.group(0))

        asset = str(obj.get("asset", "OTHER")).upper()
        direction = str(obj.get("direction", "neutral")).lower()
        if direction not in ("up", "down", "neutral"):
            direction = "neutral"
        confidence = float(obj.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        horizon_min = int(obj.get("horizon_min", 5))
        if horizon_min not in (1, 5, 15, 60):
            # Snap to nearest known bucket
            horizon_min = min((1, 5, 15, 60), key=lambda b: abs(b - horizon_min))
        reason = str(obj.get("reason", "unspecified"))[:24]

        return Classification(
            asset=asset,
            direction=direction,
            confidence=confidence,
            horizon_min=horizon_min,
            reason=reason,
        )


# ---- Convenience: full pipeline (prefilter + LLM) ----

def classify_headline(
    text: str,
    llm: LLMClassifier,
) -> tuple[Classification | None, PrefilterHit | None]:
    """Run the full two-stage classifier. Returns (classification, hit).

    * If prefilter misses, returns (None, None) — no LLM call made.
    * If prefilter hits, returns (Classification, hit).

    Callers should treat `None` as "skip this headline entirely".
    """
    hit = keyword_prefilter(text)
    if hit is None:
        return None, None
    cls = llm.classify(text, prefilter=hit)
    return cls, hit
