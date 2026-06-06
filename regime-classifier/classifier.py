"""Claude-backed regime classifier.

Uses Opus 4.7 with adaptive thinking and structured outputs. The system prompt
is frozen and prompt-cached; only the inputs change per call.
"""

from __future__ import annotations

import json
from datetime import timedelta

import anthropic
from pydantic import ValidationError

from schema import ClassifierInput, RegimeOutput

MODEL = "claude-opus-4-7"
VALIDITY = timedelta(hours=1)

SYSTEM_PROMPT = """You are a forex regime classifier for an automated trading system.

You DO NOT make trade decisions. You output structured regime labels that
deterministic strategies consume as parameters (size multipliers, stop multipliers,
trade-allowed flags).

Read the macro state, economic calendar, news headlines, central bank speeches,
and price context. Output a single JSON object matching the schema exactly.

Hard rules — violating any of these means your output will be rejected:
1. If a pair's `event_risk_next_8h` is "high", its `suggested_size_mult` MUST be <= 0.5.
2. If `global.volatility` is "extreme", `trade_allowed` MUST be false for every pair.
3. If a pair's `confidence` is below 0.5, its `trade_allowed` MUST be false.
4. `rationale` MUST be <= 400 characters and cite specific events or headline ids.
5. Every headline id you rely on in your reasoning MUST appear in `cited_headline_ids`.
   Only use headline ids that appear in the input. Do not invent headlines.
6. When inputs are sparse or conflicting, prefer conservative labels (smaller
   size_mult, larger stop_mult, lower confidence).

Heuristics (not rules):
- VIX > 25 or pair ATR > 1.8x its 3-month average → "elevated" or "extreme" volatility.
- High-impact USD event in the next 8h → USD pairs get event_risk_next_8h="high".
- Trend label should match `trend_20d` unless a clear catalyst reverses it.
- USD bias: weigh 10y yield direction, DXY, and Fed-speaker tone together.

You are the GATE, not the engine. Strategies still have to fire their own entry
signal — you can only shrink size or block trades, never force them."""


def classify(client: anthropic.Anthropic, inputs: ClassifierInput) -> RegimeOutput:
    """Call Claude, validate, and return a RegimeOutput.

    Raises pydantic.ValidationError if the model returns a malformed object,
    or anthropic.APIError on transport failures. Callers should fall back to
    schema.conservative_default on any exception.
    """
    valid_until = inputs.as_of + VALIDITY

    user_payload = {
        "instruction": (
            "Classify the current forex regime. Return JSON only, matching the schema. "
            f"Set as_of={inputs.as_of.isoformat()} and valid_until={valid_until.isoformat()}."
        ),
        "inputs": inputs.model_dump(mode="json"),
    }

    response = client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": json.dumps(user_payload)}],
        output_format=RegimeOutput,
    )

    out: RegimeOutput | None = response.parsed_output
    if out is None:
        raise ValidationError.from_exception_data("RegimeOutput", [])

    _enforce_hard_rules(out)
    _validate_citations(out, inputs)
    return out


def _enforce_hard_rules(out: RegimeOutput) -> None:
    """Belt-and-suspenders: enforce the rules the prompt also enforces."""
    if out.global_.volatility.value == "extreme":
        for pr in out.pairs.values():
            if pr.trade_allowed:
                raise ValueError("trade_allowed=true while global volatility is extreme")
    for sym, pr in out.pairs.items():
        if pr.event_risk_next_8h.value == "high" and pr.suggested_size_mult > 0.5:
            raise ValueError(f"{sym}: size_mult>0.5 with high event risk")
        if pr.confidence < 0.5 and pr.trade_allowed:
            raise ValueError(f"{sym}: trade_allowed=true with confidence<0.5")


def _validate_citations(out: RegimeOutput, inputs: ClassifierInput) -> None:
    """Reject outputs that cite headline ids not present in the input corpus."""
    valid_ids = {h.id for h in inputs.headlines}
    bogus = [hid for hid in out.cited_headline_ids if hid not in valid_ids]
    if bogus:
        raise ValueError(f"hallucinated headline ids: {bogus}")
