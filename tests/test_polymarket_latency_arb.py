from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.strategies.polymarket_latency_arb import (
    BookEvent,
    BookRequest,
    Ignore,
    OrderIntent,
    Params,
    SignalEvent,
    Skip,
    State,
    decide_prebook,
    decide_signal_gate,
    decide_with_book,
)


@dataclass(frozen=True)
class Market:
    title: str = "Bitcoin Up or Down"
    condition_id: str = "condition-1"
    end_dt: datetime = datetime(2026, 6, 21, 12, 5, tzinfo=timezone.utc)
    window_end_iso: str = "2026-06-21T12:05:00+00:00"
    up_token_id: str = "up-token"
    down_token_id: str = "down-token"


def params(**overrides) -> Params:
    values = {
        "threshold_pct": 0.10,
        "cooldown_s": 60.0,
        "sweet_lo": 0.60,
        "sweet_hi": 0.75,
        "require_confirm": False,
        "snipe_window_s": 90.0,
        "require_window_anchor": False,
        "asset": "BTC",
        "timeframe_min": 5,
    }
    values.update(overrides)
    return Params(**values)


def signal(**overrides) -> SignalEvent:
    values = {
        "ts": datetime(2026, 6, 21, 12, 4, tzinfo=timezone.utc),
        "price": 100.0,
        "ret_60s_pct": 0.12,
        "cb_ret_60s_pct": None,
        "market": Market(),
        "window_open_price": None,
    }
    values.update(overrides)
    return SignalEvent(**values)


class PolymarketLatencyArbTests(unittest.TestCase):
    def test_ignores_missing_or_below_threshold_return(self) -> None:
        state = State()

        self.assertIsInstance(
            decide_prebook(state, signal(ret_60s_pct=None), params()),
            Ignore,
        )
        self.assertIsInstance(
            decide_prebook(state, signal(ret_60s_pct=0.09), params()),
            Ignore,
        )
        self.assertIsNone(state.last_signal_ts)

    def test_ignores_cooldown_without_extending_it(self) -> None:
        ts = datetime(2026, 6, 21, 12, 4, tzinfo=timezone.utc)
        state = State(last_signal_ts=ts - timedelta(seconds=30))

        decision = decide_prebook(state, signal(ts=ts), params(cooldown_s=60.0))

        self.assertIsInstance(decision, Ignore)
        self.assertEqual(state.last_signal_ts, ts - timedelta(seconds=30))

    def test_coinbase_confirmation_skip_marks_signal(self) -> None:
        state = State()
        event = signal(cb_ret_60s_pct=-0.13)

        decision = decide_signal_gate(state, event, params(require_confirm=True))

        self.assertIsInstance(decision, Skip)
        self.assertEqual(decision.reason, "cb_confirm_fail")
        self.assertEqual(decision.debug["ret_60s_pct"], 0.12)
        self.assertEqual(decision.debug["cb_ret_60s_pct"], -0.13)
        self.assertEqual(state.last_signal_ts, event.ts)

    def test_no_market_skip_marks_signal(self) -> None:
        state = State()
        event = signal(market=None)

        decision = decide_prebook(state, event, params())

        self.assertIsInstance(decision, Skip)
        self.assertEqual(decision.reason, "no_market_in_window")
        self.assertEqual(decision.debug["snipe_window_s"], 90.0)
        self.assertEqual(state.last_signal_ts, event.ts)

    def test_window_anchor_missing_open_price_skips(self) -> None:
        state = State()
        event = signal(window_open_price=None)

        decision = decide_prebook(state, event, params(require_window_anchor=True))

        self.assertIsInstance(decision, Skip)
        self.assertEqual(decision.reason, "no_window_open_price")
        self.assertIn("window_open_iso", decision.debug)
        self.assertEqual(state.last_signal_ts, event.ts)

    def test_window_anchor_disagreement_skips(self) -> None:
        state = State()
        event = signal(price=100.0, ret_60s_pct=0.12, window_open_price=101.0)

        decision = decide_prebook(state, event, params(require_window_anchor=True))

        self.assertIsInstance(decision, Skip)
        self.assertEqual(decision.reason, "window_anchor_disagree")
        self.assertLess(decision.debug["window_ret_pct"], 0)
        self.assertEqual(state.last_signal_ts, event.ts)

    def test_prebook_returns_directional_book_request(self) -> None:
        state = State()

        up = decide_prebook(state, signal(ret_60s_pct=0.12), params())
        down = decide_prebook(state, signal(ret_60s_pct=-0.12), params())

        self.assertIsInstance(up, BookRequest)
        self.assertEqual(up.direction, "Up")
        self.assertEqual(up.target_token, "up-token")
        self.assertIsInstance(down, BookRequest)
        self.assertEqual(down.direction, "Down")
        self.assertEqual(down.target_token, "down-token")

    def test_no_ask_skip_marks_signal(self) -> None:
        state = State()
        event = BookEvent(
            ts=datetime(2026, 6, 21, 12, 4, tzinfo=timezone.utc),
            ret_60s_pct=0.12,
            market=Market(),
            direction="Up",
            target_token="up-token",
            ask=None,
        )

        decision = decide_with_book(state, event, params())

        self.assertIsInstance(decision, Skip)
        self.assertEqual(decision.reason, "no_ask_on_side")
        self.assertEqual(decision.debug["direction"], "Up")
        self.assertEqual(state.last_signal_ts, event.ts)

    def test_ask_outside_sweet_band_skips(self) -> None:
        state = State()
        event = BookEvent(
            ts=datetime(2026, 6, 21, 12, 4, tzinfo=timezone.utc),
            ret_60s_pct=0.12,
            market=Market(),
            direction="Up",
            target_token="up-token",
            ask=0.80,
        )

        decision = decide_with_book(state, event, params())

        self.assertIsInstance(decision, Skip)
        self.assertEqual(decision.reason, "ask_outside_sweet_band")
        self.assertEqual(decision.debug["ask"], 0.80)
        self.assertEqual(state.last_signal_ts, event.ts)

    def test_valid_book_event_returns_order_intent_without_marking_signal(self) -> None:
        state = State()
        event = BookEvent(
            ts=datetime(2026, 6, 21, 12, 4, tzinfo=timezone.utc),
            ret_60s_pct=0.12,
            market=Market(),
            direction="Up",
            target_token="up-token",
            ask=0.66,
        )

        decision = decide_with_book(state, event, params())

        self.assertIsInstance(decision, OrderIntent)
        self.assertEqual(decision.venue, "polymarket")
        self.assertEqual(decision.market_id, "condition-1")
        self.assertEqual(decision.outcome_name, "Up")
        self.assertEqual(decision.limit_price, 0.66)
        self.assertIsNone(state.last_signal_ts)


if __name__ == "__main__":
    unittest.main()
