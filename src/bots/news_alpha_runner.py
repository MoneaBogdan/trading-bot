"""News-driven Polymarket trader runner.

Wires together:
  * Telegram source (Telethon) → raw headlines
  * Layered classifier (keyword prefilter + Claude Sonnet) → Classification
  * Polymarket gamma market discovery → MarketCandidate[]
  * Pure decide() function → Intent (or skip)
  * Polymarket trader execution (Trader.place_buy_fok)
  * BotLogger (new-schema JSONL) + legacy print log

Defaults to DRY-RUN. Caps via env (POLY_MAX_ORDER_USDC, POLY_MAX_DAILY_USDC).

The runner contains ALL the side effects. The strategy module (decide()) is
pure. The classifier's prefilter is pure; the LLM call is contained to
classifier.LLMClassifier and only invoked here.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# Make repo root importable so `polymarket.*` and `src.*` both work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from telethon import TelegramClient, events
except ImportError:
    sys.stderr.write("telethon not installed: pip install telethon\n")
    raise

try:
    import anthropic
except ImportError:
    sys.stderr.write("anthropic SDK not installed: pip install anthropic\n")
    raise

import httpx

from src.core.logger import BotLogger
from src.strategies.news_alpha.classifier import (
    LLMClassifier,
    keyword_prefilter,
)
from src.strategies.news_alpha.strategy import (
    Classification,
    Intent,
    MarketCandidate,
    NewsEvent,
    Params,
    State,
    decide,
    explain_skip,
)

# Polymarket integration
from polymarket.gamma import discover_markets, BtcMarket  # noqa: E402
from polymarket.clob import get_orderbook  # noqa: E402
from polymarket.trader import Trader, TraderConfig  # noqa: E402


BOT_NAME = "news-alpha"
STRATEGY_NAME = "NewsAlpha"


# ---- env → Params ----

def _params_from_env() -> Params:
    return Params(
        min_confidence=float(os.environ.get("NEWS_MIN_CONFIDENCE", "0.70")),
        cooldown_s=float(os.environ.get("NEWS_COOLDOWN_S", "60")),
        sweet_lo=float(os.environ.get("NEWS_SWEET_LO", "0.30")),
        sweet_hi=float(os.environ.get("NEWS_SWEET_HI", "0.40")),
        size_usdc=float(os.environ.get("POLY_MAX_ORDER_USDC", "5")),
        max_horizon_min=int(os.environ.get("NEWS_MAX_HORIZON_MIN", "60")),
        max_fires_per_day=int(os.environ.get("NEWS_MAX_FIRES_PER_DAY", "10")),
    )


def _trader_config_from_env() -> TraderConfig:
    return TraderConfig(
        private_key=os.environ["POLY_PRIVATE_KEY"],
        funder_address=os.environ.get("POLY_FUNDER_ADDRESS") or None,
        max_order_usdc=float(os.environ.get("POLY_MAX_ORDER_USDC", "5")),
        max_daily_usdc=float(os.environ.get("POLY_MAX_DAILY_USDC", "20")),
        dry_run=os.environ.get("POLY_DRY_RUN", "true").lower() != "false",
    )


# ---- Polymarket market discovery (across all assets we trade) ----

# Map (asset, horizon_min from classifier) → which Polymarket timeframes to scan.
# For news-driven trades, the closest-resolving market is best, so we scan both
# the asset's 5-min and 60-min variants and let decide() pick the soonest.
_ASSETS_TO_SCAN = ("BTC", "ETH", "SOL")
_TIMEFRAMES_TO_SCAN = (5, 60)


def _gather_candidates(asset: str, http: httpx.Client) -> list[MarketCandidate]:
    """Discover open Up/Down markets for `asset` and fetch live orderbook asks."""
    candidates: list[MarketCandidate] = []
    for tf in _TIMEFRAMES_TO_SCAN:
        try:
            markets: list[BtcMarket] = discover_markets(
                asset=asset, timeframe_min=tf, window_horizon_min=120, client=http,
            )
        except Exception as e:
            print(f"[runner] discover_markets({asset},{tf}) failed: {e}", flush=True)
            continue
        for m in markets:
            try:
                ob_up = get_orderbook(m.up_token_id)
                ob_down = get_orderbook(m.down_token_id)
                ask_up = float(ob_up.asks[0].price) if ob_up.asks else 1.0
                ask_down = float(ob_down.asks[0].price) if ob_down.asks else 1.0
            except Exception as e:
                print(f"[runner] orderbook fetch failed for {m.market_id}: {e}", flush=True)
                continue
            candidates.append(MarketCandidate(
                market_id=m.market_id,
                condition_id=m.condition_id,
                title=m.title,
                end_dt=m.end_dt,
                up_token_id=m.up_token_id,
                down_token_id=m.down_token_id,
                ask_up=ask_up,
                ask_down=ask_down,
            ))
    return candidates


# ---- Main loop ----

async def main_async() -> None:
    # Boot config
    params = _params_from_env()
    trader_cfg = _trader_config_from_env()

    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session_name = os.environ.get("TG_SESSION_NAME", "tree_news_recorder")
    channels_raw = os.environ.get("TG_CHANNELS", "treeofalpha")
    channels = [c.strip().lstrip("@") for c in channels_raw.split(",") if c.strip()]

    log_base = Path(os.environ.get("LOG_DIR", "logs"))
    bot_logger = BotLogger(bot=BOT_NAME, strategy=STRATEGY_NAME, base_dir=log_base)

    state = State()
    trader = Trader(trader_cfg)
    http = httpx.Client(timeout=20.0)

    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    llm = LLMClassifier(client=anthropic_client)

    bot_logger.boot(
        params={
            "min_confidence": params.min_confidence,
            "cooldown_s": params.cooldown_s,
            "sweet_lo": params.sweet_lo,
            "sweet_hi": params.sweet_hi,
            "size_usdc": params.size_usdc,
            "max_horizon_min": params.max_horizon_min,
            "max_fires_per_day": params.max_fires_per_day,
        },
        dry_run=trader_cfg.dry_run,
        channels=channels,
        model=llm.model,
    )
    print(f"[news-alpha] boot dry_run={trader_cfg.dry_run} channels={channels} "
          f"model={llm.model}", flush=True)

    tg = TelegramClient(session_name, api_id, api_hash)
    await tg.start()
    resolved = []
    for ch in channels:
        try:
            entity = await tg.get_entity(ch)
            resolved.append(entity)
            print(f"[news-alpha] subscribed: @{ch}", flush=True)
        except Exception as e:
            print(f"[news-alpha] failed to resolve @{ch}: {e}", flush=True)
    if not resolved:
        raise SystemExit("no Telegram channels resolved")

    @tg.on(events.NewMessage(chats=resolved))
    async def on_message(event: events.NewMessage.Event) -> None:
        msg = event.message
        text = msg.message or ""
        ts = (msg.date or datetime.now(timezone.utc)).astimezone(timezone.utc)

        # Stage 1: keyword prefilter (free, instant)
        hit = keyword_prefilter(text)
        if hit is None:
            # Don't log every prefilter miss — too noisy. Sample 1% if needed.
            return

        # Stage 2: LLM classification
        try:
            cls = llm.classify(text, prefilter=hit)
        except Exception as e:
            bot_logger.skip("news_classifier_error", ts=ts, headline=text[:200],
                            error_type=type(e).__name__, error=str(e))
            return

        # Asset must be one we scan, otherwise discovery returns nothing.
        if cls.asset not in _ASSETS_TO_SCAN:
            bot_logger.skip("news_asset_out_of_scope", ts=ts, headline=text[:200],
                            classification=_cls_dict(cls))
            return

        # Discover open markets for this asset (sync HTTP; could be moved to executor)
        loop = asyncio.get_event_loop()
        candidates = await loop.run_in_executor(None, _gather_candidates, cls.asset, http)

        news_event = NewsEvent(
            ts=ts,
            headline=text,
            classification=cls,
            candidates=tuple(candidates),
        )

        # Predict skip before calling decide (so we can log it cleanly)
        skip_reason = explain_skip(state, news_event, params)
        if skip_reason is not None:
            bot_logger.skip(skip_reason, ts=ts, headline=text[:200],
                            classification=_cls_dict(cls),
                            n_candidates=len(candidates))
            return

        intents = decide(state, news_event, params)
        if not intents:
            # Defensive — explain_skip and decide should agree, but just in case
            bot_logger.skip("news_no_open_market", ts=ts, headline=text[:200],
                            classification=_cls_dict(cls))
            return

        intent = intents[0]
        await _execute_intent(intent, trader, bot_logger, news_event)

    print("[news-alpha] listening for headlines...", flush=True)
    try:
        await tg.run_until_disconnected()
    finally:
        bot_logger.shutdown(reason="normal")
        http.close()


def _cls_dict(c: Classification) -> dict[str, Any]:
    return {
        "asset": c.asset,
        "direction": c.direction,
        "confidence": c.confidence,
        "horizon_min": c.horizon_min,
        "reason": c.reason,
    }


async def _execute_intent(intent: Intent, trader: Trader, logger: BotLogger,
                          news_event: NewsEvent) -> None:
    """Run trader.place_buy_fok off-thread and log a fire row."""
    intent_id = str(uuid4())
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            trader.place_buy_fok,
            intent.token_id, intent.limit_price, intent.size_usdc,
        )
    except Exception as e:
        logger.skip("news_classifier_error", ts=intent.ts,  # repurpose for exec err
                    headline=news_event.headline[:200],
                    error_type=type(e).__name__, error=str(e))
        return

    # Find the candidate market title for richer logging
    market_title = next(
        (c.title for c in news_event.candidates if c.market_id == intent.market_id),
        "",
    )
    cost = (float(result.filled_size) * float(result.filled_price)
            if (result.filled_size and result.filled_price) else None)

    logger.fire(
        ts=intent.ts,
        intent_id=intent_id,
        venue="polymarket",
        market_id=intent.market_id,
        market_title=market_title,
        outcome_name=intent.outcome,
        side=intent.side,
        order_type="fok_limit",
        size_usdc=intent.size_usdc,
        limit_price=intent.limit_price,
        filled_size=float(result.filled_size) if result.filled_size else None,
        filled_price=float(result.filled_price) if result.filled_price else None,
        cost_usdc=cost,
        order_ok=result.ok,
        order_id=result.order_id,
        dry_run=trader.config.dry_run,
        condition_id=intent.condition_id,
        token_id=intent.token_id,
        classification=_cls_dict(news_event.classification),
        headline=news_event.headline[:300],
        reason=intent.reason,
    )
    print(f"[news-alpha] FIRE {intent.outcome} @ {intent.limit_price:.3f} "
          f"({news_event.classification.reason}) ok={result.ok}", flush=True)


def main() -> int:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("[news-alpha] shutdown (KeyboardInterrupt)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
