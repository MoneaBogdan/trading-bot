# Architecture & Roadmap

> **Audience:** future contributors, AI agents resuming work on this repo, and the operator.
> **Goal of this doc:** make it cheap to (a) understand what exists, (b) add new bots / strategies / markets, and (c) hand context off without re-deriving it.
>
> Read top-to-bottom on first visit. Later, jump via the TOC.

## Table of Contents

1. [North star](#1-north-star)
2. [What exists today](#2-what-exists-today)
3. [Pain points blocking expansion](#3-pain-points-blocking-expansion)
4. [Target architecture (component view)](#4-target-architecture-component-view)
5. [End-to-end runtime flow](#5-end-to-end-runtime-flow)
6. [Data model & event contracts](#6-data-model--event-contracts)
7. [Strategy contract](#7-strategy-contract)
8. [Stream bus & supervisor semantics](#8-stream-bus--supervisor-semantics)
9. [Configuration model](#9-configuration-model)
10. [Logging & event store](#10-logging--event-store)
11. [Execution semantics](#11-execution-semantics)
12. [Outcome attribution & PnL](#12-outcome-attribution--pnl)
13. [Backtest framework — unified](#13-backtest-framework--unified)
14. [Extension recipes (worked examples)](#14-extension-recipes-worked-examples)
15. [Failure & restart model](#15-failure--restart-model)
16. [Migration plan (non-breaking, phased)](#16-migration-plan-non-breaking-phased)
17. [Future UI dashboard](#17-future-ui-dashboard)
18. [Open design decisions](#18-open-design-decisions)
19. [Glossary](#19-glossary)
20. [Related docs](#20-related-docs)

---

## 1. North star

A **multi-venue, multi-strategy crypto trading platform** the operator can:

- run on a single VPS via docker-compose
- extend by dropping in a new strategy / market / venue with minimal scaffolding
- backtest any strategy × market combination against the same logged data
- monitor and tune from a web UI (future)

We are **not** building HFT or a market-making giant. The target operator is one technically literate person running 5–10k USD positions across edge sources where bigger funds are absent or asleep.

Design priorities, in order:

1. **Clarity** > cleverness. Plain code beats meta-frameworks.
2. **Plug-in extension** > inheritance hierarchies. New strategy = new file + new config.
3. **One log per event, ever.** No re-deriving truth from heterogeneous sources.
4. **Backtest parity.** Same code paths replay against logged data — no separate "backtest engine" that drifts from live.
5. **Crash-only design.** Any process can be SIGKILLed at any time. Restart is the only recovery path. No "graceful shutdown" logic that itself can fail.
6. **Boring infra.** Files, JSONL, a single Python process per pod. Postgres / Redis / Kafka enter only when measured pain demands them.

---

## 2. What exists today

### Top-level layout

```
trading-bot/
├── Dockerfile                  # single image, used by all containers
├── docker-compose.yml          # 9 trader variants (3 assets × {5m,15m,1h} + eth-5m-wide) + funding-monitor + ws-recorder
├── deploy.sh                   # cron-friendly auto-pull-and-rebuild script
├── README.md                   # user-facing setup & ops guide
├── RESEARCH_2026.md            # cited research + iterative follow-up log
├── ARCHITECTURE.md             # this file
├── polymarket/                 # live bot + Polymarket-specific replay backtest
├── hyperliquid/                # funding_monitor — Hyperliquid / Binance / Bybit / Drift / Paradex cross-DEX rate poller
├── backtest/                   # GENERIC bar-event backtest engine (forex/equity)
└── regime-classifier/          # standalone Claude-based regime classifier (not integrated)
```

### `polymarket/` — live bot + market-specific replay

```
polymarket/
├── binance_stream.py           # WS stream — Binance trades (asset-parameterized)
├── coinbase_stream.py          # WS stream — Coinbase matches (asset-parameterized)
├── gamma.py                    # Polymarket Gamma API client — market discovery
├── clob.py                     # Polymarket CLOB orderbook fetch (keep-alive httpx.Client)
├── orderbook_ws_cache.py       # Optional WS-fed top-of-book cache (POLY_BOOK_WS_CACHE=true); HTTPS fallback on miss/stale
├── trader.py                   # Order placement (live/dry, daily caps)
├── monitor.py                  # MoveTracker (60s rolling return), _pick_market
├── live_trader.py              # Live entry point: stream → signal → gates → fire
├── run_live.sh                 # Auto-restart wrapper for live_trader
├── orderbook_recorder_ws.py    # Long-running WS recorder → logs/orderbook_ws_*.jsonl
├── orderbook_recorder.py       # Polling fallback recorder (REST)
├── run_ws_recorder.sh          # Auto-restart wrapper for WS recorder
├── verify_live_fills.py        # Match live fill prices vs WS orderbook
├── setup_wallet.py             # CLOB API key creation (one-time)
├── setup_allowances.py         # USDC + CTF token approvals (one-time)
├── DEPLOY_NOTES.md             # Server deploy + log layout reference
├── HOSTING.md                  # VPS sizing notes
├── requirements.txt
├── backtest/                   # Polymarket-specific replay engine + market data cache
└── logs/                       # All runtime logs (not in git)
```

### `hyperliquid/` — cross-DEX funding-rate monitor

`funding_monitor.py` polls Hyperliquid, Binance, Bybit, Drift (`data.api.drift.trade/rateHistory`, public), and Paradex (`api.prod.paradex.trade/v1/markets/summary`, public) for the BTC/ETH/SOL perp funding rates and fires opportunity events when the widest pair-wise spread exceeds the threshold (currently 5 bps). Each `snapshot` event carries `drift_funding_8h_bps` and `paradex_funding_8h_bps` alongside the HL/Binance/Bybit values; `best_cross()` picks the widest pair. Drift may 403 from some egresses (CloudFront geo-restriction); the parser silently zeros out a failed venue so the remaining venues keep polling.

### `backtest/` — generic engine (forex/equity legacy)

Event-driven bar engine. Walk-forward, monte-carlo, permutation tests. Currently disconnected from the live bot — they evolved independently. Not used by Polymarket but the `Strategy` ABC and registry pattern are reusable.

### `regime-classifier/` — standalone Claude classifier

Hourly regime tags (risk-on/off, vol regime, etc.) intended to feed `StaticGate` in the forex engine. Built but not wired into either backtest or live.

---

## 3. Pain points blocking expansion

| # | Pain | Symptom | Cost |
|---|---|---|---|
| 1 | **Two backtest engines, neither portable.** | `backtest/` is bar-event (forex). `polymarket/backtest/` is binary-payoff (replay). New venue → invent a third. | Adding Kalshi arb requires writing a new backtest from scratch. |
| 2 | **`polymarket/` is the namespace AND the venue.** | Adding Kalshi or Hyperliquid means a sibling folder or polluting `polymarket/`. | No clear pattern for venue-adapter placement. |
| 3 | **Streams + execution + strategy intermixed in `live_trader.py`.** | The trading rule, market discovery, and order placement are one async loop. | Can't unit-test the strategy; can't run two strategies sharing one stream. |
| 4 | **No config layer.** | Variants are hard-coded as env vars in `run_live.sh` + `docker-compose.yml`. | Each new variant means editing two files in two places. |
| 5 | **Log schema is implicit.** | Each component writes its own JSONL with different fields. No central reader. | A UI cannot query "all fires from variant X in window Y" without bespoke parsing. |
| 6 | **No process-level orchestration.** | Each container runs one bot. Sharing a Binance stream across 3 strategies = 3 redundant subscriptions. | At 10 strategies the WS connection count alone becomes a problem. |
| 7 | **No resolution attribution.** | We log fires but never log the market's actual outcome — PnL is computed by ad-hoc scripts hitting REST after the fact. | Can't answer "what's my realized PnL by strategy this week" without re-scraping. |
| 8 | **`regime-classifier/` orphan.** | Built but not wired. | Existing investment wasted. |
| 9 | **No registry of live bots.** | "What's running?" answered only by `docker ps`. No metadata, no health, no PnL summary. | A UI has no source of truth. |

---

## 4. Target architecture (component view)

### Top-level layout (proposed)

```
trading-bot/
├── Dockerfile
├── docker-compose.yml           # supervisor service + ws-recorder service
├── deploy.sh
├── README.md
├── ARCHITECTURE.md
├── RESEARCH_2026.md
│
├── configs/                     # declarative bot configs (YAML, pydantic-validated)
│   ├── btc-5m.yaml
│   ├── eth-1h.yaml
│   ├── polymarket-mm.yaml
│   └── _shared.yaml             # reusable fragments (anchors, ${refs})
│
├── src/
│   ├── core/                    # framework code; no venue/strategy specifics
│   │   ├── types.py             # PriceEvent, OrderbookEvent, OrderIntent, Fill, Outcome, ...
│   │   ├── clock.py             # Clock abstraction (real-time vs replay)
│   │   ├── stream_bus.py        # In-process pub/sub
│   │   ├── logger.py            # JSONL writer + LogReader (returns DataFrames)
│   │   ├── config.py            # YAML loader + pydantic validation
│   │   ├── registry.py          # strategies / venues / data_sources registries
│   │   ├── bot.py               # Bot runtime: wires streams → strategy → venues → logger
│   │   ├── supervisor.py        # Loads N configs, dedups shared resources, runs N bots
│   │   ├── kill_switch.py       # Caps, daily-loss limits, hard stop signals
│   │   └── outcome_tracker.py   # Tracks open positions → resolves → logs PnL
│   │
│   ├── data_sources/            # Read-only feeds — emit events to stream_bus
│   │   ├── binance_ws.py
│   │   ├── coinbase_ws.py
│   │   ├── tree_of_alpha_ws.py  # future
│   │   ├── defillama_rest.py    # future
│   │   └── regime_classifier.py # future (wraps regime-classifier/ output)
│   │
│   ├── venues/                  # Read+write venue adapters
│   │   ├── base.py              # Venue ABC: place_order, fetch_orderbook, ...
│   │   ├── polymarket/
│   │   │   ├── gamma.py
│   │   │   ├── clob.py
│   │   │   ├── ws_orderbook.py  # also acts as a data_source AND a recorder
│   │   │   └── resolution.py    # poll Gamma for resolved markets → emit OutcomeEvent
│   │   ├── kalshi/              # future
│   │   └── hyperliquid/         # future
│   │
│   ├── strategies/              # Pure logic: events → order intents
│   │   ├── base.py              # Strategy ABC
│   │   ├── polymarket_latency_arb.py
│   │   ├── polymarket_maker_rebate.py     # future
│   │   ├── polymarket_kalshi_arb.py       # future
│   │   ├── news_headline_bucket.py        # future
│   │   └── _retired/
│   │
│   ├── backtest/                # Unified replayer
│   │   ├── replayer.py
│   │   ├── fill_model.py
│   │   ├── dataset.py
│   │   └── reports.py
│   │
│   └── ui/                      # future
│
├── scripts/                     # one-off tooling
├── data/                        # cached historical data (parquet/json)
├── logs/                        # runtime logs (JSONL, partitioned by bot + day)
└── tests/
    ├── unit/                    # strategies, core, fill_model — fast, no I/O
    └── integration/             # full-loop replay against a frozen dataset
```

### Component responsibilities (one-liner each)

| Component | Owns | Does NOT own |
|---|---|---|
| `data_sources/*` | Connecting to an external read-only feed, normalizing into Event types, emitting on the bus. | Order placement, strategy logic, persistence. |
| `venues/*` | Order placement, balance, position state, market discovery, market resolution. | Strategy decisions, signal logic. |
| `strategies/*` | Pure decision: given an Event + state, return zero or more OrderIntents. | I/O of any kind. No HTTP, no file writes, no `await`. |
| `core/bot.py` | One bot's runtime: subscribe to required streams, call strategy on events, hand intents to venue, log everything. | Cross-bot coordination. |
| `core/supervisor.py` | Multi-bot lifecycle: dedup data_sources, share stream_bus, restart bots on failure. | Strategy internals. |
| `core/stream_bus.py` | In-process pub/sub with bounded queues per subscriber. | Persistence (recorders do that separately). |
| `core/logger.py` | Writing JSONL, reading JSONL into DataFrames. Stable schema. | Aggregation logic — that's `outcome_tracker` / UI. |
| `core/outcome_tracker.py` | Watching for `MarketResolutionEvent`, matching to open positions, emitting `pnl` log entries. | Live decisioning. |
| `core/kill_switch.py` | Reading caps from config, halting a bot if breached. | Telling the strategy *why* — strategy is dumb to this. |
| `backtest/replayer.py` | Driving the same Strategy code with logged events, using a deterministic clock. | Live execution. |

The hard rule: **a strategy file imports only `core/types`, `core/registry`, and (if needed) helpers from `core/timeframe`.** It cannot import a venue, a data_source, or `httpx`. If it does, the layering is wrong.

---

## 5. End-to-end runtime flow

This section spells out, step by step, what happens from process start to a logged outcome. The current `live_trader.py` does most of this implicitly; the proposed architecture makes each step a discrete, testable unit.

### 5.1 Supervisor boot

```
docker compose up
   ↓
supervisor.py main()
   ↓
1. Load configs/*.yaml → list[BotConfig]
2. Validate each (pydantic). Bail on any error — never run partial fleet.
3. Compute the union of required data_sources across all bots:
       sources_needed = ⋃ bot.strategy.required_streams() for bot in bots
   Dedup by (source_name, params): one binance_ws("BTC") covers all bots that want it.
4. Compute the union of required venues. Instantiate one client per venue.
   Each venue client owns its own auth + connection pool.
5. Build stream_bus, register one publisher per dedup'd data_source.
6. For each bot:
       - create per-bot logger (logs/bot=<name>/YYYY-MM-DD.jsonl)
       - instantiate strategy(**cfg.params)
       - subscribe strategy to its required streams
       - create per-bot asyncio.Task running bot.run()
7. Install signal handlers (SIGTERM → cancel all bot tasks → close venues → flush logger).
8. await asyncio.gather(*bot_tasks) — supervisor stays alive until all bots exit.
```

Key invariants:

- **Boot is all-or-nothing.** If any config is bad, no bot starts. We never run a half-fleet.
- **Data sources outlive bots.** A bot can crash; the source keeps running and the other bots keep getting events.
- **Bot tasks are independently restartable.** Supervisor wraps each `bot.run()` in a retry loop with exponential backoff (1s → 2s → … → 60s cap), logging a `bot_crashed` event each time. Repeated crashes do not bring down the supervisor.

### 5.2 One bot's main loop

```
async def Bot.run(self):
    subs = [self.bus.subscribe(spec) for spec in self.strategy.required_streams()]
    async for ev in merge(subs):              # interleaves events by ts
        self.kill_switch.check_or_raise()     # caps / daily-loss / manual halt
        intents = self.strategy.on_event(ev, self.ctx)
        for intent in intents:
            await self._execute(intent)
```

`_execute` is the only place that does I/O on behalf of a strategy. Its job:

1. Resolve `intent.venue` to a Venue instance (already attached at boot).
2. Apply pre-trade gates the strategy is NOT trusted with: dry-run check, max-order-USDC, max-daily-USDC, cooldown.
   - These duplicate what the kill_switch does at a coarser level; here they are pre-place. The kill_switch is the trip-out wire if these slip.
3. Call `venue.place_order(intent)`. This is the only awaitable in the hot path.
4. Receive a `Fill` (possibly synthetic in dry-run, possibly `Fill(error=...)` on rejection).
5. Log a `fire` event (intent + fill, success or failure, both).
6. If `intent.kind == open_position`, hand the Fill to `outcome_tracker` so PnL gets resolved later.

### 5.3 The decision pipeline (concrete: latency-arb strategy)

For the existing Polymarket latency-arb strategy, `on_event` receives events of types `PriceEvent` (Binance, Coinbase), `MarketListEvent` (Polymarket markets refreshed every 60s), and `Tick` (a synthetic timer if needed). The strategy's internal state is:

```
PriceHistory (per asset)        # window-open lookup
MoveTracker (60s rolling)       # both Binance and Coinbase
last_signal_ts                  # cooldown
current_markets: list[Market]   # from MarketListEvent
```

The decision tree on each Binance `PriceEvent` (no I/O in the strategy):

```
update PriceHistory, update MoveTracker
ret_60s = MoveTracker.return_pct
if ret_60s is None: return []
if abs(ret_60s) < threshold: return []
if cooldown active: return []
if require_confirm:
    cb_ret = cb_MoveTracker.return_pct
    if cb_ret is None or |cb_ret| < threshold or sign(cb_ret) ≠ sign(ret_60s):
        emit SkipEvent(reason="cb_confirm_fail"); return []
market = pick_market(current_markets, ev.ts, max_lookahead=snipe_window)
if market is None: emit SkipEvent(reason="no_market_in_window"); return []
if require_window_anchor:
    open_p = PriceHistory.price_at(market.window_open)
    if open_p is None: emit SkipEvent(reason="no_window_open_price"); return []
    if sign(window_ret) ≠ sign(ret_60s): emit SkipEvent(...); return []
direction = "Up" if ret_60s > 0 else "Down"
return [OrderIntent(
    venue="polymarket", market_id=market.id, side=direction,
    size_usdc=size, kind="open_position",
    needs_orderbook_check=True,  # tells _execute to fetch ask + sweet-band gate
    sweet_lo=..., sweet_hi=...,
)]
```

Note the strategy emits a single `OrderIntent` — it does NOT call CLOB itself. The sweet-band check requires an orderbook fetch, which is I/O, which lives in the executor (`venue.place_order`). The intent carries `sweet_lo/sweet_hi` so the venue can reject with a structured "skip" before placing.

> **Why push the sweet-band check into the venue side?** Two reasons. (1) The orderbook is venue I/O; pulling it into the strategy forces the strategy to be async. (2) Pre-place reject = no order ever leaves the process, so the kill_switch's daily counter is unaffected — clean accounting. Counter-arg: it splits one decision across two layers. We accept that for the async purity.

### 5.4 Outcome attribution

Separately, `venues/polymarket/resolution.py` polls Gamma for resolved markets every 30s. For each newly resolved market, it emits a `MarketResolutionEvent(market_id, outcome, resolved_ts)` onto the bus. `outcome_tracker` is subscribed to this; for each open position whose `market_id` matches, it:

1. Computes realized PnL = `payout × filled_size − cost`.
2. Logs a `pnl` event tagged with the originating `fire` event's id.
3. Marks the position closed.

Positions that never resolve (data source gap) get a `position_orphaned` event after 24h, surfaced to the UI.

### 5.5 Sequence diagram (text)

```
┌─────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌────────┐  ┌──────────┐
│ binance │  │ coinbase │  │ gamma    │  │ stream │  │ bot    │  │ venue    │
│ ws      │  │ ws       │  │ refresh  │  │ bus    │  │ btc-5m │  │ polymkt  │
└────┬────┘  └────┬─────┘  └────┬─────┘  └───┬────┘  └───┬────┘  └────┬─────┘
     │            │             │            │           │            │
     │price tick ─┼─────────────┼───────────►│ ──ev────► │ on_event   │
     │            │price tick ──┼───────────►│ ──ev────► │ on_event   │
     │            │             │mkts refresh┼──────────►│ store      │
     │price tick ─┼─────────────┼───────────►│ ──ev────► │ on_event   │
     │            │             │            │           │ → intent   │
     │            │             │            │           │            │
     │            │             │            │           │ place_order┼─►CLOB
     │            │             │            │           │◄─ Fill ────┤
     │            │             │            │           │ log fire   │
     │            │             │            │           │ register w/│
     │            │             │            │           │ outcome_trk│
     │            │             │            │           │            │
     │            │             │resolution  │           │            │
     │            │             │poll (30s)  ┼──Resol───►│outcome_trk │
     │            │             │            │           │ log pnl    │
```

### 5.6 The minimum failure surface to think about

| Failure | Detection | Recovery |
|---|---|---|
| Binance WS goes silent | `_watchdog` (90s stale) | Process exits, bash wrapper restarts. |
| Coinbase WS silent (only if `require_confirm`) | Same watchdog | Same. |
| Gamma 5xx | Caught in `_refresh_markets` | Logged, next tick retries. State.markets goes stale; `_pick_market` may return None → skip with reason. |
| CLOB order rejected | `place_order` returns `Fill(error=...)` | Logged as `fire` with `order_ok=false`. Cooldown still applies. |
| Resolution poller gap | Position stays open in tracker | After 24h logs `position_orphaned`; UI surfaces. |
| Bot crashes (Python exception) | Supervisor's wrapper task catches | Logged as `bot_crashed`, exp-backoff restart. State (PriceHistory, cooldown) is lost — strategy must be okay with that. |
| Supervisor crashes | systemd / docker `restart: unless-stopped` | Fresh boot. All bots resync. Open positions still get resolved via outcome_tracker reading on-disk state. |

The implication: **strategies must be robust to having their in-memory state erased at any moment**. PriceHistory rebuilds in 60s from new stream data; that's acceptable. A strategy that depended on hours of context would need to checkpoint to disk — none currently do.

---

## 6. Data model & event contracts

All types live in `src/core/types.py`. Strategies, venues, and data_sources import from here only — never from each other.

### 6.1 Event types

Every event is a frozen dataclass with `ts: datetime` (UTC, tz-aware) as its first field. The bus uses `ts` to interleave events from multiple sources in time order.

```python
@dataclass(frozen=True)
class PriceEvent:
    ts: datetime
    asset: str              # "BTC" / "ETH" / "SOL"
    venue: str              # "binance" / "coinbase"
    price: float
    size: float             # trade size in base units (informational; used by some strategies)

@dataclass(frozen=True)
class OrderbookEvent:
    ts: datetime
    venue: str              # "polymarket"
    market_id: str
    bids: list[tuple[float, float]]   # [(price, size), ...] descending
    asks: list[tuple[float, float]]   # ascending
    # Note: emitted by venues/polymarket/ws_orderbook.py — also doubles as recorder source

@dataclass(frozen=True)
class MarketListEvent:
    ts: datetime
    venue: str
    asset: str
    timeframe_min: int
    markets: tuple[Market, ...]  # tuple so it's hashable / frozen

@dataclass(frozen=True)
class MarketResolutionEvent:
    ts: datetime
    venue: str
    market_id: str
    outcome: str            # "Up" / "Down" / "Cancelled"
    settle_price: float | None    # underlying close that drove resolution, if available

@dataclass(frozen=True)
class HeadlineEvent:
    ts: datetime
    source: str             # "tree_of_alpha" / ...
    text: str
    tags: tuple[str, ...]

@dataclass(frozen=True)
class RegimeEvent:
    ts: datetime
    regime: str             # "risk_on" / "risk_off" / "high_vol" / ...
    confidence: float       # 0..1
    payload: dict           # full classifier output

@dataclass(frozen=True)
class TickEvent:
    """Synthetic clock tick. Strategies that need wall-clock cadence (e.g. quoting bots
    that re-quote every N seconds even without market data) subscribe to this."""
    ts: datetime
    interval_ms: int

Event = Union[PriceEvent, OrderbookEvent, MarketListEvent, MarketResolutionEvent,
              HeadlineEvent, RegimeEvent, TickEvent]
```

### 6.2 Market

```python
@dataclass(frozen=True)
class Market:
    venue: str
    market_id: str                  # venue-specific id
    condition_id: str | None        # polymarket-specific; null for venues that don't have it
    asset: str
    timeframe_min: int
    title: str
    window_start: datetime
    window_end: datetime
    outcomes: tuple[Outcome, ...]   # ("Up", "Down") for binaries; ("Yes", "No") for Kalshi
    min_tick: float
    min_size: float

@dataclass(frozen=True)
class Outcome:
    name: str               # "Up" / "Down" / "Yes" / "No"
    token_id: str | None    # polymarket CLOB token id; null for other venues
    last_price: float | None
```

The current `BtcMarket` dataclass in `polymarket/gamma.py` is structurally equivalent and renames easily.

### 6.3 Order intents and fills

```python
@dataclass(frozen=True)
class OrderIntent:
    """A strategy's request to trade. Pure data, no I/O.

    The intent carries enough info for the executor to do venue-specific gates
    (e.g. sweet-band ask check) without re-asking the strategy."""
    intent_id: str              # uuid4 — sticks to the resulting Fill + outcome
    bot: str                    # producing bot name
    strategy: str               # strategy class name
    ts: datetime                # strategy's decision time
    venue: str
    market_id: str
    outcome_name: str           # "Up" / "Yes" / etc. — names not token_ids in the intent layer
    side: str                   # "buy" / "sell"
    order_type: str             # "fok_limit" / "gtc_limit" / "market"
    size_usdc: float
    limit_price: float | None   # required for limit orders
    # Optional venue-level pre-place gates:
    sweet_lo: float | None = None
    sweet_hi: float | None = None
    # Free-form provenance — copied into the fire log:
    debug: dict = field(default_factory=dict)

@dataclass(frozen=True)
class Fill:
    intent_id: str
    ts: datetime                # venue's fill time, or our recv time if not provided
    ok: bool                    # false if rejected or skipped
    order_id: str | None        # venue order id; null for dry-run
    filled_size: float          # in base units (e.g. shares of "Up" token)
    filled_price: float | None  # weighted average if partial
    cost_usdc: float            # actual USDC out (= filled_size × filled_price for buys)
    reason: str | None          # if not ok: "rejected_by_venue" / "sweet_band_fail" /
                                # "max_daily_cap" / "dry_run_no_orderbook" / ...
    raw: dict                   # venue's raw response, for debugging
```

The `intent_id` is the **join key** for everything downstream. A strategy emits intent → executor produces Fill → outcome_tracker eventually produces a `pnl` log entry — all three rows in the JSONL share `intent_id`.

### 6.4 BotContext

The runtime parameter passed to `strategy.on_event(ev, ctx)`. It's a deliberately narrow interface — the strategy gets exactly these affordances and nothing else:

```python
@dataclass
class BotContext:
    bot_name: str
    now: Callable[[], datetime]     # uses the bot's Clock — real time live, replay time in backtest
    config: dict                    # strategy params from YAML (already validated)
    emit_skip: Callable[[str, dict | None], None]  # logs a "skip" event, returns nothing
    state: dict                     # strategy's mutable state — opaque to core
    # No bus, no venues, no httpx, no asyncio. If the strategy thinks it needs more, redesign.
```

`state` is a plain dict that survives across `on_event` calls within a process. It does NOT survive restarts (see §15). If a strategy needs durable state, it logs explicit checkpoint events and rebuilds from them at boot (none do today).

### 6.5 Position (tracked by outcome_tracker)

```python
@dataclass
class OpenPosition:
    intent_id: str
    bot: str
    venue: str
    market_id: str
    outcome_name: str
    filled_size: float
    cost_usdc: float
    opened_ts: datetime
    resolves_by: datetime           # market.window_end — if exceeded by 24h, flag as orphan
```

Persisted to `logs/positions/open.jsonl` so a restart can re-load. Closed positions are appended to `logs/bot=<name>/<date>.jsonl` as `pnl` events; the open file is rewritten on each close (small, bounded).

### 6.6 What lives where (anti-cheat-sheet)

| Concept | Lives in | Does NOT live in |
|---|---|---|
| `PriceEvent` | `core/types.py` | A venue file |
| Binance WS URL constants | `data_sources/binance_ws.py` | `core` |
| Polymarket CLOB auth | `venues/polymarket/clob.py` | A strategy file |
| Sweet-band thresholds | `configs/btc-5m.yaml`, passed via `params` | A constant in the strategy |
| `last_signal_ts` cooldown | `ctx.state["last_signal_ts"]` | A module-level variable |
| Daily-loss cap | `core/kill_switch.py` reading `config.execution.max_daily_usdc` | Strategy code |

---

## 7. Strategy contract

```python
class Strategy(ABC):
    """Pure logic. No I/O. No imports of venues or data_sources.

    The same instance lives across many events. State lives in ctx.state — the
    instance itself should be parameter-only after __init__."""

    NAME: ClassVar[str]                 # set by @register_strategy decorator

    def __init__(self, **params): ...   # validated params from YAML

    @abstractmethod
    def required_streams(self) -> list[StreamSpec]:
        """What events I subscribe to. Called at boot, never again."""

    @abstractmethod
    def required_venues(self) -> list[str]:
        """What venues I will produce OrderIntents for."""

    @abstractmethod
    def on_event(self, ev: Event, ctx: BotContext) -> list[OrderIntent]:
        """Pure function (modulo ctx.state mutation). Return zero or more intents.
        MUST be cheap — runs in the hot path. Heavy work goes in a periodic task
        that emits a synthetic event."""

    # Optional hooks (default = no-op):
    def on_boot(self, ctx: BotContext) -> None: ...
    def on_shutdown(self, ctx: BotContext) -> None: ...
    def on_fill(self, fill: Fill, ctx: BotContext) -> None: ...      # for state updates after a fill
    def on_outcome(self, ev: MarketResolutionEvent, ctx: BotContext) -> None: ...
```

### 7.1 Lifecycle

```
__init__(**params)            # at boot
on_boot(ctx)                  # ctx is now wired; do warmup (e.g. seed state)
loop:
    on_event(ev, ctx)         # 0..many intents returned
    on_fill(fill, ctx)        # called per intent after executor runs
    ...
on_outcome(ev, ctx)           # whenever a relevant resolution arrives (any time)
on_shutdown(ctx)              # SIGTERM / supervisor restart
```

### 7.2 Idempotency and determinism rules

- `on_event` must be **deterministic** given (state, event). No `random`, no `time.time()` — use `ctx.now()`. The replayer relies on this; bugs that violate it cause backtest drift.
- `on_event` must be **side-effect-free except for `ctx.state` and `ctx.emit_skip`**. Logging happens automatically for intents; explicit skip-logging is for cases where you decided not to fire and want the reason recorded.
- A strategy must tolerate `on_event` being called with events out of order ONLY in replay (where the dataset is itself sorted but bot-bus merge ordering may differ slightly across runs). In live, events are monotonic per source but may interleave across sources by up to ~100ms.
- A strategy must tolerate `on_fill` arriving for an intent it has forgotten about (e.g. after a state-erasing restart). The default `on_fill` is a no-op; override only if you need it.

### 7.3 What a strategy is NOT allowed to do

- `import httpx`, `import websockets`, `import asyncio`. Pure sync, pure data.
- Sleep, await, spin.
- Read or write files outside `ctx.emit_skip`.
- Call into a venue or data_source module.
- Maintain module-level state. Two instances of the same strategy class (different configs) coexist in one process; state must be per-instance via `ctx.state`.

### 7.4 What enforces this?

A unit test importing every strategy and asserting:

```python
def test_strategy_has_no_io_imports():
    for cls in all_registered_strategies():
        mod = sys.modules[cls.__module__]
        for name in dir(mod):
            assert name not in FORBIDDEN_IMPORTS  # httpx, websockets, asyncio, ...
```

Plus the replayer's determinism check: run the same dataset twice and assert identical fire ts + intent_ids (after a re-seed of intent_id RNG).

### 7.5 Concrete example: `polymarket_latency_arb`

```python
@register_strategy("polymarket_latency_arb")
class PolymarketLatencyArb(Strategy):
    def __init__(self, *, asset, timeframe_min, threshold_pct, cooldown_s,
                 sweet_lo, sweet_hi, require_confirm, require_window_anchor,
                 snipe_window_s, size_usdc):
        self.p = LatencyArbParams(...)   # frozen dataclass; explicit > **kwargs

    def required_streams(self):
        out = [
            StreamSpec("binance_ws", asset=self.p.asset),
            StreamSpec("polymarket_markets", asset=self.p.asset, timeframe_min=self.p.timeframe_min),
        ]
        if self.p.require_confirm:
            out.append(StreamSpec("coinbase_ws", asset=self.p.asset))
        return out

    def required_venues(self):
        return ["polymarket"]

    def on_boot(self, ctx):
        ctx.state["binance_tracker"] = MoveTracker(60.0)
        ctx.state["coinbase_tracker"] = MoveTracker(60.0)
        ctx.state["history"] = PriceHistory(self.p.timeframe_min * 60 + 60)
        ctx.state["last_signal_ts"] = None
        ctx.state["markets"] = ()

    def on_event(self, ev, ctx) -> list[OrderIntent]:
        if isinstance(ev, PriceEvent) and ev.venue == "binance":
            return self._on_binance(ev, ctx)
        if isinstance(ev, PriceEvent) and ev.venue == "coinbase":
            ctx.state["coinbase_tracker"].add(ev)
            return []
        if isinstance(ev, MarketListEvent):
            ctx.state["markets"] = ev.markets
            return []
        return []

    def _on_binance(self, ev, ctx) -> list[OrderIntent]:
        # ... the pure-logic decision tree from §5.3 ...
```

The whole module is ~150 lines, importable in tests, runnable against any event sequence.

---

## 8. Stream bus & supervisor semantics

### 8.1 Bus design

```python
class StreamBus:
    def publish(self, ev: Event) -> None:
        for q in self._queues_for(ev):
            q.put_nowait(ev)        # bounded queue; drop policy below

    def subscribe(self, spec: StreamSpec) -> AsyncIterator[Event]:
        q = asyncio.Queue(maxsize=1000)
        self._register(spec, q)
        return _drain(q)
```

Key properties:

- **In-process only.** No network hop. v1 is single-host.
- **Bounded queue per subscriber.** Default 1000. If a subscriber falls behind:
  - Default policy: **block the publisher** (back-pressure). A slow bot pulls down the whole fleet — intentional, makes the failure obvious.
  - Override per-stream: `drop_oldest` for price ticks (fresh > complete), `drop_newest` for headlines (first one is most valuable). Configured at the data_source level, not by the subscriber.
- **No persistence.** Recorders persist if they want durability; the bus itself is volatile.
- **Order within a source is guaranteed.** Order across sources is best-effort interleaved by `ts`; the bus does NOT reorder. A strategy that needs strict cross-source ordering uses `merge_sorted` itself.

### 8.2 Subscription model

A `StreamSpec` is a tuple of `(source_name, **params)`. The bus matches publishers to subscribers by `source_name` and pushes only matching events. Params are used for filtering at subscribe time (e.g. `asset=BTC` only delivers BTC tick events).

The supervisor dedups data_sources by `(source_name, params)`. Two bots subscribing to `binance_ws(asset=BTC)` get events from one WS connection.

### 8.3 Supervisor lifecycle

```
boot                    →  see §5.1
runtime                 →  await gather(bot_tasks)
SIGTERM                 →  cancel all bot tasks → on_shutdown → close venues → flush logger → exit 0
unhandled bot exception →  log bot_crashed, exp-backoff restart, NEVER bring down supervisor
unhandled source error  →  log source_crashed, source restarts itself; bots see stream gap → may emit skips
SIGKILL                 →  see §15.5
```

The supervisor's own crash is a different beast: docker / systemd restarts it. On reboot, `outcome_tracker` reads `logs/positions/open.jsonl` and resumes resolution polling — no live decisions are at risk because in-memory strategy state was already volatile.

### 8.4 Why in-process, not Redis?

A bot with 10 strategies and 5 data_sources is ~500 events/sec at peak. That's two orders of magnitude below what Python asyncio handles trivially. Network hops would dominate latency and add a failure surface for no measurable gain. Revisit if/when we go multi-host (well past Phase G).

---

## 9. Configuration model

### 9.1 File layout

```
configs/
├── _shared.yaml            # YAML anchors reusable across configs
├── btc-5m.yaml
├── eth-5m.yaml
├── sol-5m.yaml
├── btc-1h.yaml
├── eth-1h.yaml
└── sol-1h.yaml
```

### 9.2 Full schema

```yaml
# configs/btc-5m.yaml
name: btc-5m                          # unique per bot; becomes the log subdir name
strategy: polymarket_latency_arb      # must be a registered name

# Streams the bot subscribes to. Source names match data_sources/*.py module names.
streams:
  - source: binance_ws
    params: { asset: BTC }
  - source: coinbase_ws
    params: { asset: BTC }
  - source: polymarket_markets
    params: { asset: BTC, timeframe_min: 5 }

# Venues the strategy will trade on. Must be subset of strategy.required_venues().
venues: [polymarket]

# Strategy-specific params. Validated against strategy's pydantic model.
params:
  asset: BTC
  timeframe_min: 5
  threshold_pct: 0.10
  sweet_lo: 0.30
  sweet_hi: 0.40
  cooldown_s: 60
  snipe_window_s: 300
  require_confirm: true
  require_window_anchor: true
  size_usdc: 5

# Execution layer settings — interpreted by core/bot.py and core/kill_switch.py,
# NOT by the strategy.
execution:
  dry_run: true
  max_order_usdc: 5
  max_daily_usdc: 20
  max_open_positions: 1
  halt_on_daily_loss_usdc: -30        # negative = loss limit

logging:
  level: info
  also_legacy_path: logs/live_${UTC_DATE}.jsonl     # back-compat for BTC-5m only
```

### 9.3 Schema validation

`core/config.py`:

```python
class StreamRef(BaseModel):
    source: str
    params: dict = {}

class ExecutionConfig(BaseModel):
    dry_run: bool = True
    max_order_usdc: confloat(gt=0)
    max_daily_usdc: confloat(gt=0)
    max_open_positions: conint(ge=1) = 1
    halt_on_daily_loss_usdc: float | None = None

class BotConfig(BaseModel):
    name: str = Field(..., regex=r"^[a-z0-9-]+$")
    strategy: str
    streams: list[StreamRef]
    venues: list[str]
    params: dict
    execution: ExecutionConfig
    logging: LoggingConfig = LoggingConfig()

    @validator("params")
    def params_match_strategy(cls, v, values):
        strat_cls = get_strategy(values["strategy"])
        strat_cls.Params(**v)   # raises on mismatch
        return v
```

Each strategy ships its own `Params` pydantic model. Bot-level config validation invokes it — bad params fail at boot, never at runtime.

### 9.4 Secrets

Configs are committed; secrets are not. Secrets live in `.env` and are referenced via `${ENV_VAR}` interpolation in `_shared.yaml`. The loader expands these before pydantic validation. The set of acceptable env var names is whitelisted (`POLY_PRIVATE_KEY`, `POLY_FUNDER_ADDRESS`, `KALSHI_API_KEY`, …) to prevent a misconfigured YAML from leaking unrelated env content into a log.

### 9.5 Hot-reload?

**No, by design.** Config changes go through git → rebuild → docker restart. Reasoning: a strategy's behavior is a function of its config; a silent reload would invalidate every line of log history with no clear cutover marker. Restarts are cheap (sub-second) and unambiguous.

---

## 10. Logging & event store

### 10.1 Principle

Every observable thing the system produces is one JSONL row, written exactly once, with a stable schema. No row is rewritten; appends only.

### 10.2 Paths

```
logs/
├── bot=<name>/YYYY-MM-DD.jsonl         # per-bot daily log (fires, skips, pnl, status)
├── recorder=binance_ws/...              # source-side raw event recording (optional, for replay)
├── recorder=ws_polymarket/...           # Polymarket L2 orderbook
├── recorder=news/...                    # future
├── positions/open.jsonl                 # outcome_tracker's working set (small, rewritten on close)
└── _audit/supervisor.jsonl              # boot, crash, restart events
```

### 10.3 Bot log row schema

Every row has these required fields:

| Field | Type | Notes |
|---|---|---|
| `ts` | ISO datetime, UTC, ms precision | Event time. |
| `bot` | string | Matches `BotConfig.name`. |
| `strategy` | string | Strategy class name. |
| `event` | string | One of: `boot`, `shutdown`, `fire`, `skip`, `fill`, `pnl`, `bot_crashed`, `position_orphaned`. |

Event-specific fields:

```jsonc
// event = "fire"
{
  "ts": "...", "bot": "...", "strategy": "...", "event": "fire",
  "intent_id": "uuid",
  "venue": "polymarket",
  "market_id": "0x...",
  "market_title": "Bitcoin Up or Down - June 15, 4:40AM-4:45AM ET",
  "outcome_name": "Down",
  "side": "buy",
  "order_type": "fok_limit",
  "size_usdc": 5.0,
  "limit_price": 0.39,
  "filled_size": 12.82,
  "filled_price": 0.39,
  "cost_usdc": 5.0,
  "order_ok": true,
  "order_id": "0x...",
  "dry_run": false,
  "lat_ms_decide": 0.4,
  "lat_ms_book": 118,
  "lat_ms_order": 174,
  "debug": { "ret_60s_pct": -0.137, "cb_ret_60s_pct": -0.121, "window_ret_pct": -0.18, "underlying_price": 67161.88 }
}

// event = "skip"
{
  "ts": "...", "bot": "...", "strategy": "...", "event": "skip",
  "reason": "cb_confirm_fail",      // controlled vocabulary (see below)
  "debug": { ... }
}

// event = "pnl"
{
  "ts": "...", "bot": "...", "strategy": "...", "event": "pnl",
  "intent_id": "uuid",              // joins back to the fire row
  "market_id": "0x...",
  "outcome_name": "Down",
  "outcome_resolved": "Down",       // what actually happened
  "won": true,
  "cost_usdc": 5.0,
  "payout_usdc": 12.82,
  "pnl_usdc": 7.82,
  "resolved_ts": "...",
  "settle_price": 67159.43
}
```

### 10.4 Skip reasons — controlled vocabulary

The `reason` field on skip events uses a closed set. Adding a new one is intentional — it requires a doc entry here and a backtest-stats update. Current set:

| Reason | Meaning |
|---|---|
| `threshold_not_met` | `\|ret_60s\| < threshold_pct` (often suppressed to avoid log spam — only logged on a debug flag) |
| `cooldown_active` | within `cooldown_s` of last signal |
| `cb_confirm_fail` | Coinbase confirm gate failed |
| `no_market_in_window` | no Polymarket market resolves within `snipe_window_s` |
| `no_window_open_price` | PriceHistory buffer didn't cover window_open (transient at boot) |
| `window_anchor_disagree` | window-open return disagrees with 60s return in sign |
| `no_ask_on_side` | CLOB orderbook empty for our outcome |
| `ask_outside_sweet_band` | ask < sweet_lo or ask > sweet_hi |
| `daily_cap_reached` | max_daily_usdc would be exceeded |
| `open_position_cap_reached` | already have max_open_positions |

### 10.5 Reading logs

```python
# core/logger.py
def read_bot_log(bot: str, since: datetime, until: datetime) -> pd.DataFrame: ...
def read_events(event_type: str, ...) -> pd.DataFrame: ...
def join_fire_pnl(bot: str, since, until) -> pd.DataFrame:
    """Returns fires LEFT JOINed to their pnl rows by intent_id.
    Useful for 'win rate by hour-of-day', 'PnL by sweet-band bucket', etc."""
```

### 10.6 Backward compatibility

The existing `polymarket/logs/live_*.{log,jsonl}` files keep their schema. New rows include both new fields (`intent_id`, `strategy`, `event`) and the legacy aliases (`btc_price`, `btc_ret_60s_pct`). On Phase B cutover, the new logger writes the new path AND keeps appending to the legacy path for the BTC-5m bot for one week of parity testing.

### 10.7 Retention

Storage is not a constraint (operator confirmed). No rotation in v1. When storage becomes a concern, `scripts/rotate_logs.sh` will tar+gzip files older than 90 days into `logs/_archive/YYYY-MM/` without altering on-disk filenames within the archive (so backtests can still find them).

---

## 11. Execution semantics

### 11.1 Order intent → fill, step by step

```
1. Strategy returns OrderIntent.
2. Bot._execute:
   a. kill_switch.check_or_raise()
      - Caps: max_open_positions, max_daily_usdc, halt_on_daily_loss_usdc.
      - If breached: log skip(daily_cap_reached / open_position_cap_reached), do NOT call venue.
   b. venue.place_order(intent):
      - Resolves outcome_name → token_id (Polymarket).
      - If intent.sweet_lo/hi set: fetch orderbook, check ask, log skip(ask_outside_sweet_band / no_ask_on_side) if fail.
      - If dry_run: synthesize Fill(ok=true, filled_at=ask, filled_size=size/ask, order_id="DRY-<uuid>").
      - Else: POST CLOB FOK. Translate response → Fill.
   c. Log fire(intent + fill).
   d. If Fill.ok: outcome_tracker.register(intent, fill).
3. outcome_tracker, later:
   - On MarketResolutionEvent, compute pnl, log pnl, remove from open.jsonl.
```

### 11.2 Dry-run vs live

`dry_run` is per-bot (in execution config). The venue method receives a `dry_run: bool` flag and is responsible for synthesizing a realistic Fill (using the live orderbook ask, not a fabricated price). This keeps PnL accounting honest in dry-run — the same code path that books a live trade books the synthetic one, just with `dry_run=true` in the log row.

### 11.3 Idempotency

If `_execute` crashes between `place_order` returning and `outcome_tracker.register`, the order is real but the tracker doesn't know about it. Mitigation: on boot, the resolution poller scans recent fires in `logs/bot=*/` and reconciles any fire whose `intent_id` is not in `open.jsonl` and not yet resolved. This is the **only** part of the system that does cross-file scanning at boot, and it's bounded to the last 48h.

### 11.4 Cancels and TTLs

Current strategies are FOK only — no order management. When we add the maker-rebate LPer (Phase 2), the venue gains:

```python
async def cancel_order(self, order_id: str) -> bool: ...
async def replace_order(self, order_id: str, new_intent: OrderIntent) -> Fill: ...
async def list_open_orders(self) -> list[OpenOrder]: ...
```

The strategy emits `OrderIntent(order_type="gtc_limit", ttl_s=30)`; the bot's executor sets a timer to cancel after TTL. All cancels and replaces are logged as their own `event=cancel` / `event=replace` rows.

### 11.5 Kill switches

`core/kill_switch.py` reads two things on each `check_or_raise`:

- **Day's pnl** — computed by streaming `pnl` events from today's bot log. Cached, invalidated on each new pnl write.
- **Open position count** — from `logs/positions/open.jsonl`.

Two manual overrides exist:

- File `logs/halt/<bot>` — touched manually halts a single bot until removed.
- File `logs/halt/ALL` — halts everything.

The check is done before every order placement. Removing the file resumes; no process restart needed.

---

## 12. Outcome attribution & PnL

### 12.1 The resolution loop

```python
# venues/polymarket/resolution.py
async def run(bus, poll_s=30.0):
    seen = set()
    while True:
        recently_resolved = await gamma_client.fetch_resolved_since(last_check)
        for m in recently_resolved:
            if m.market_id in seen: continue
            seen.add(m.market_id)
            bus.publish(MarketResolutionEvent(
                ts=now(), venue="polymarket", market_id=m.market_id,
                outcome=m.outcome, settle_price=m.settle_price,
            ))
        await sleep(poll_s)
```

### 12.2 outcome_tracker

```python
# core/outcome_tracker.py
async def run(bus, logger, positions_path):
    open_positions = load(positions_path)              # dict[market_id, list[OpenPosition]]
    async for ev in bus.subscribe(StreamSpec("market_resolution")):
        for pos in open_positions.pop(ev.market_id, []):
            pnl = compute_pnl(pos, ev)
            logger.log(pos.bot, {
                "event": "pnl", "intent_id": pos.intent_id,
                "market_id": ev.market_id, "outcome_name": pos.outcome_name,
                "outcome_resolved": ev.outcome,
                "won": pos.outcome_name == ev.outcome,
                "cost_usdc": pos.cost_usdc,
                "payout_usdc": payout(pos, ev),
                "pnl_usdc": pnl,
                "resolved_ts": ev.ts.isoformat(),
                "settle_price": ev.settle_price,
            })
        save(open_positions, positions_path)
```

### 12.3 Payout formulas (per venue)

| Venue | Position | Outcome | Payout |
|---|---|---|---|
| Polymarket binary | bought `outcome_name` for `cost_usdc`, `filled_size` shares | `outcome_name` wins | `filled_size × $1` |
| Polymarket binary | same | other side wins | `$0` |
| Polymarket binary | same | cancelled | `cost_usdc` returned (logged as `pnl_usdc=0`) |
| Kalshi binary | similar | similar | similar |
| Hyperliquid perp | size × entry | mark price moves | size × (mark − entry) − funding |

Each venue ships its own `compute_pnl(position, resolution_event)` to keep the math localized.

### 12.4 Reconciliation

A nightly script (`scripts/reconcile_pnl.py`, cron 03:00 UTC) compares the venue's own balance/position endpoints against `logs/positions/open.jsonl` + sum of today's `pnl` events. Discrepancies log to `logs/_audit/reconcile.jsonl`. This is the trust anchor — if the books drift, we know within 24h.

---

## 13. Backtest framework — unified

### 13.1 Principle reiterated

Backtest = run the **same** `Strategy.on_event` against a logged event stream, with a deterministic clock and a fill model that consumes the logged orderbook.

### 13.2 Replayer

```python
def replay(bot_config: BotConfig,
           start: datetime, end: datetime,
           dataset: ReplayDataset) -> ReplayResult:
    strategy = get_strategy(bot_config.strategy)(**bot_config.params)
    clock = ReplayClock(start)
    ctx = BotContext(bot_name=bot_config.name, now=clock.now, config=bot_config.params,
                     emit_skip=collector.skip, state={})
    strategy.on_boot(ctx)
    fill_model = FillModel(dataset.orderbook_replay)
    intents_log = []
    for ev in dataset.events(start, end):           # merged & sorted across sources
        clock.advance_to(ev.ts)
        for intent in strategy.on_event(ev, ctx):
            fill = fill_model.simulate(intent, ev.ts)
            collector.fire(intent, fill)
            if fill.ok:
                outcome = dataset.resolution(intent.market_id)
                if outcome is not None:
                    collector.pnl(intent, fill, outcome)
    return collector.result()
```

### 13.3 Fill model

Simulates fills against the **logged** orderbook at `intent.ts`. For Polymarket FOK at the ask:

```python
def simulate(intent, ts):
    ob = orderbook_at(intent.market_id, ts)        # nearest-prior snapshot, ≤500ms
    if ob is None:
        return Fill(ok=False, reason="no_orderbook_snapshot")
    ask = ob.best_ask_on_side(intent.outcome_name)
    if not (intent.sweet_lo <= ask <= intent.sweet_hi):
        return Fill(ok=False, reason="ask_outside_sweet_band")
    # Walk the book to fill size_usdc at the ask:
    filled_size, avg_price, fully = walk_asks(ob, intent.size_usdc)
    if not fully:
        return Fill(ok=False, reason="insufficient_depth")    # FOK semantics
    return Fill(ok=True, filled_size=filled_size, filled_price=avg_price, ...)
```

A second mode (`fill_model="optimistic"`) assumes the top-of-book ask is always fillable for our size — useful for comparing live results against an upper bound.

### 13.4 Datasets

```python
@dataclass
class ReplayDataset:
    price_events: Iterable[PriceEvent]            # from logs/recorder=binance_ws/...
    orderbook_replay: OrderbookReplay             # indexed by (market_id, ts)
    market_lists: Iterable[MarketListEvent]       # from gamma snapshots / live recordings
    resolutions: dict[str, MarketResolutionEvent] # market_id -> resolution
```

A dataset is a thin wrapper. Building one from logs takes ~5 lines once we have recorders running for everything we want to backtest. Backfilling pre-recording history uses REST fetchers in `scripts/fetch_*.py`.

### 13.5 Parity testing

`scripts/parity_test.py`: pick the last 7 days of live BTC-5m logs, build a dataset from the same period's recordings, run the replayer, assert `|replay_pnl − live_pnl| < $1`. Run weekly; any drift means strategy state isn't deterministic.

### 13.6 What about the legacy forex `backtest/`?

Kept. Its bar engine is a different paradigm (deterministic bar iteration with SL/TP exit logic) that fits forex/equity better than tick-by-tick replay. The `Strategy` ABC pattern carries over — eventually forex strategies can also register under the unified `src/strategies/` namespace and get replayed via a bar-event adapter that emits `BarEvent`s into the same bus.

---

## 14. Extension recipes (worked examples)

### 14.1 Add a new market variant (existing strategy, new params)

Drop `configs/sol-1h.yaml`:

```yaml
name: sol-1h
strategy: polymarket_latency_arb
streams:
  - { source: binance_ws, params: { asset: SOL } }
  - { source: polymarket_markets, params: { asset: SOL, timeframe_min: 60 } }
venues: [polymarket]
params:
  asset: SOL
  timeframe_min: 60
  threshold_pct: 0.20
  sweet_lo: 0.30
  sweet_hi: 0.40
  cooldown_s: 60
  snipe_window_s: 300
  require_confirm: false           # hourlies resolve from Binance only
  require_window_anchor: true
  size_usdc: 5
execution:
  dry_run: true
  max_order_usdc: 5
  max_daily_usdc: 20
```

Restart the supervisor. Done.

### 14.2 Add a new strategy: news-headline pre-position (worked walkthrough)

Goal: when Tree of Alpha emits a high-impact BTC headline (Fed, ETF, hack), and current Polymarket BTC 5-min ask on the matching side is in sweet band, fire.

**Step 1 — register the data_source.** Create `src/data_sources/tree_of_alpha_ws.py`. It connects to the WS, normalizes events into `HeadlineEvent(tags=("BTC", "fed_speak", "high_impact"))`, publishes to bus. Adds itself to the registry under `"tree_of_alpha_ws"`.

**Step 2 — write the strategy.** `src/strategies/news_headline_bucket.py`:

```python
@register_strategy("news_headline_bucket")
class NewsHeadlineBucket(Strategy):
    class Params(BaseModel):
        asset: str
        timeframe_min: int = 5
        tags_long: list[str]      # tags that predict "Up" (e.g. "etf_approval")
        tags_short: list[str]     # tags that predict "Down" (e.g. "hack", "regulator_lawsuit")
        sweet_lo: float = 0.30
        sweet_hi: float = 0.50
        cooldown_s: int = 300     # don't double-fire on a news barrage
        size_usdc: float = 5.0

    def required_streams(self):
        return [
            StreamSpec("tree_of_alpha_ws"),
            StreamSpec("polymarket_markets", asset=self.p.asset, timeframe_min=self.p.timeframe_min),
        ]

    def required_venues(self): return ["polymarket"]

    def on_boot(self, ctx):
        ctx.state["last_fire_ts"] = None
        ctx.state["markets"] = ()

    def on_event(self, ev, ctx):
        if isinstance(ev, MarketListEvent):
            ctx.state["markets"] = ev.markets
            return []
        if not isinstance(ev, HeadlineEvent): return []
        if ctx.state["last_fire_ts"] and (ev.ts - ctx.state["last_fire_ts"]).seconds < self.p.cooldown_s:
            ctx.emit_skip("cooldown_active", None)
            return []
        side = self._classify(ev.tags)
        if side is None: return []
        market = pick_nearest_market(ctx.state["markets"], ev.ts, max_lookahead_s=self.p.timeframe_min*60)
        if market is None:
            ctx.emit_skip("no_market_in_window", None); return []
        ctx.state["last_fire_ts"] = ev.ts
        return [OrderIntent(
            intent_id=str(uuid4()), bot=ctx.bot_name, strategy=self.NAME, ts=ev.ts,
            venue="polymarket", market_id=market.market_id, outcome_name=side,
            side="buy", order_type="fok_limit", size_usdc=self.p.size_usdc,
            limit_price=self.p.sweet_hi,    # cap our buy
            sweet_lo=self.p.sweet_lo, sweet_hi=self.p.sweet_hi,
            debug={"headline": ev.text[:120], "tags": list(ev.tags)},
        )]

    def _classify(self, tags):
        if set(tags) & set(self.p.tags_long): return "Up"
        if set(tags) & set(self.p.tags_short): return "Down"
        return None
```

**Step 3 — config.**

```yaml
# configs/news-btc-5m.yaml
name: news-btc-5m
strategy: news_headline_bucket
streams:
  - { source: tree_of_alpha_ws }
  - { source: polymarket_markets, params: { asset: BTC, timeframe_min: 5 } }
venues: [polymarket]
params:
  asset: BTC
  timeframe_min: 5
  tags_long:  [etf_approval, accommodative_fed, large_institutional_buy]
  tags_short: [hack, regulator_lawsuit, sec_enforcement, hawkish_fed]
  sweet_lo: 0.30
  sweet_hi: 0.50
  cooldown_s: 300
  size_usdc: 5
execution: { dry_run: true, max_order_usdc: 5, max_daily_usdc: 20 }
```

**Step 4 — test.** `tests/unit/strategies/test_news_headline_bucket.py`:

```python
def test_fires_on_long_tag_when_market_available():
    strat = NewsHeadlineBucket(asset="BTC", timeframe_min=5,
                                tags_long=["etf_approval"], tags_short=[],
                                sweet_lo=0.3, sweet_hi=0.5)
    ctx = fake_ctx()
    strat.on_boot(ctx)
    strat.on_event(MarketListEvent(ts=t0, venue="polymarket", asset="BTC", timeframe_min=5,
                                   markets=(fake_market(window_end=t0+3min),)), ctx)
    intents = strat.on_event(HeadlineEvent(ts=t0, source="toa", text="ETF approved",
                                            tags=("etf_approval",)), ctx)
    assert len(intents) == 1
    assert intents[0].outcome_name == "Up"
```

**Step 5 — backtest.** `scripts/backtest.py --config configs/news-btc-5m.yaml --from 2026-01-01 --to 2026-06-01`. Requires headline log + Polymarket recordings spanning that range — if not available, narrows to available range and reports.

**Step 6 — deploy.** Drop YAML in `configs/`, redeploy, watch `logs/bot=news-btc-5m/` fill up with skips for a week before flipping `dry_run=false`.

The whole exercise: ~2 days work, no touch to existing strategies, no touch to docker-compose.

### 14.3 Add a new venue: Kalshi

1. Create `src/venues/kalshi/`: `client.py` (REST + WS), `resolution.py` (poll closed markets).
2. Implement `Venue` ABC: `place_order`, `cancel_order`, `fetch_orderbook`, `discover_markets`, `fetch_position`, `fetch_balance`, `compute_pnl`.
3. Add Kalshi credentials to `.env.example` (`KALSHI_API_KEY`, `KALSHI_PRIVATE_KEY_PATH`).
4. Register: `register_venue("kalshi", KalshiVenue)`.
5. A strategy needing it lists `venues: [polymarket, kalshi]`; the bot loader instantiates both and exposes them via `ctx.venues` (added to BotContext for strategies that need cross-venue queries, e.g. arb strategies that compare two orderbooks before firing).

### 14.4 Retire a strategy

1. Move file to `src/strategies/_retired/<name>.py`.
2. Comment out its `@register_strategy` decorator. The file stays readable; the registry doesn't see it.
3. Delete any `configs/*.yaml` that reference it (or rename to `configs/_retired/`).
4. Note in `RESEARCH_2026.md` why and what we learned.

---

## 15. Failure & restart model

### 15.1 Process tree

```
docker (restart: unless-stopped)
└── supervisor.py (PID 1 in container)
    ├── asyncio task: data_source binance_ws (one per dedup'd source)
    ├── asyncio task: data_source coinbase_ws
    ├── asyncio task: data_source polymarket_markets
    ├── asyncio task: venue polymarket resolution poller
    ├── asyncio task: outcome_tracker
    ├── asyncio task: bot btc-5m
    ├── asyncio task: bot eth-5m
    └── ...
```

### 15.2 What's volatile vs persistent

| State | Persistence | Restart behavior |
|---|---|---|
| `ctx.state` (per strategy) | Volatile | Erased. Strategy re-warms (≤60s for current strategies). |
| Stream bus queues | Volatile | Empty on boot. Sources reconnect and start publishing. |
| Open positions | Persistent: `logs/positions/open.jsonl` | Re-loaded by outcome_tracker. |
| Logs | Persistent: `logs/...` | Appended to existing files. |
| Day's PnL counter | Recomputed from logs at boot | First check after boot scans today's `pnl` events. |
| Kill-switch halt files | Persistent: `logs/halt/*` | Honored on boot. |
| Venue auth tokens | Persistent: `polymarket_creds.json` | Re-used. |

### 15.3 Restart sequences

**Bot crash (one strategy exception):** supervisor logs `bot_crashed`, sleeps `min(60, 2^attempts)`, restarts the bot task. Stream bus subs are re-established. Other bots are unaffected.

**Supervisor crash (Python OOM, unhandled exception):** docker restarts container. Boot sequence (§5.1) runs. Outcome tracker reads `open.jsonl` and resumes. Any orders placed but not yet logged would be lost — mitigated by reconciliation (§11.3).

**WS reconnect (Binance hiccup):** data_source's reconnect loop kicks in (already implemented). Bus subscribers see a gap, no events for that period. Strategy watchdog (per-bot, replaces the current `_watchdog`) exits the bot after `stale_s` of no events from a required stream, triggering supervisor restart of that bot.

**SIGTERM (graceful shutdown):** supervisor cancels all bot tasks (which raise CancelledError in `on_event` flow → strategy `on_shutdown` called) → venue clients closed → logger flushed → exit 0. Bounded to 10s total; SIGKILL after.

**SIGKILL (no warning):** crash-only design treats this as identical to a Python crash. Recovery via docker restart.

### 15.4 What if outcome attribution loses an event?

Resolution poller is the source of truth. It polls every 30s and dedups by `market_id`. If a resolution is missed in one poll cycle (gamma 5xx, network glitch), next cycle catches it. If it's missed *permanently* (extremely unlikely — Polymarket retains resolved markets indefinitely), `position_orphaned` fires after 24h and the operator is notified.

### 15.5 What if two supervisor instances boot simultaneously?

Forbidden. docker-compose enforces single instance per service name. If someone manually starts a second one (e.g. ssh'd into a server and ran `python supervisor.py`), they'd hit a file lock on `logs/positions/open.jsonl` (advisory `fcntl.flock`). The lock is the only piece of cross-process coordination in the design.

---

## 16. Migration plan (non-breaking, phased)

The live bot must keep running. Each phase is a non-breaking commit; nothing is removed until the new path is proven.

### Phase A — Document & freeze interfaces ✅ (this commit + the prior architecture commit)
- `ARCHITECTURE.md`. No code change.

### Phase B — `src/core/` skeleton + parallel new logger 🟡 (deployed, parity validation in flight — last status 2026-06-17)
- ✅ Create `src/core/` package (logger landed; types/config/registry/clock to follow as needed).
- ✅ `src/core/logger.py` with controlled-vocab `BotLogger` writing `logs/bot=<name>/YYYY-MM-DD.jsonl`.
- ✅ Wired into `polymarket/live_trader.py` for ALL 6 variants (not just `eth-5m` — single shared entry point made per-variant rollout unnecessary). Legacy `logs/live_*.jsonl` paths untouched.
- ✅ Controlled vocab extended with news_alpha skip reasons (`news_*`); see `src/core/logger.py`.
- 🟡 Parity check in progress. As of 2026-06-17 (~22.5h post-deploy): 1 fire across all 6 variants (eth-5m UP at 0.36 → WIN, both legacy and new-schema rows captured). Need more fires across other variants before parity is statistically meaningful — see task #10 + `reports/STRATEGY_FINDINGS.md`.
- ⏳ Drop legacy writes only after parity holds for one week.

### Phase B.5 — Strategy style guide ✅ (commit `70249f6`)
- `STRATEGY_TEMPLATE.md` + `src/strategies/_template.py` codify the five rules
  (pure `decide`, env→Params at boot, no cross-imports, mutable State,
  side-effects in runner) so new strategies are written close to the Phase C
  target shape and future extraction is mechanical.

### Phase B.6 — Strategy template validated by greenfield build ✅ (commits `7fb42ed`, `bdfc02a`, `9fcc815`)
- Built `news_alpha` (Tree of Alpha news → Polymarket Up/Down) following STRATEGY_TEMPLATE.md from scratch as a proof point:
  - `src/strategies/news_alpha/strategy.py` — pure `decide(state, event, params)`, `Params`/`State`/`Intent` shape per template.
  - `src/strategies/news_alpha/classifier.py` — keyword prefilter (PURE) + Claude Sonnet LLM call (isolated to the runner).
  - `src/strategies/news_alpha/sources/{types,treeofalpha_rest,telegram}.py` — pluggable source layer behind `asyncio.Queue[Headline]`. `NEWS_SOURCE` env switches between sources without touching strategy code. **This source-layer-as-protocol is the pattern Phase D's stream bus will generalize.**
  - `src/bots/news_alpha_runner.py` — the side-effects shell; reads config, wires source → classifier → discover → decide → executor + BotLogger. Tested end-to-end in docker (real Sonnet call classified "SEC approves spot ETF for BTC" → `etf_approval` direction=up conf=0.95).
- The exercise confirmed the template is workable in practice and ironed out concrete issues (e.g., `polymarket/` not being an importable package → runners use bare `sys.path` prepend, matching `live_trader.py`'s existing pattern).
- **Implication for Phase C:** the latency-arb extraction can now follow the same shape mechanically. No template revisions needed.

### Phase B.7 — Operational tooling for ongoing tuning ✅ (commit `a7b074c`)
- `polymarket/backtest/sweet_band_counterfactual.py` — replays `ask_outside_sweet_band` skip rows against Polymarket gamma resolutions to size widened-band PnL. Surfaced the [0.60, 0.75] edge bucket on 2026-06-17 (n=282 resolved signals, 77% direction accuracy overall, +$61 in that band).
- `polymarket/backtest/daily_report.py` — generates `reports/<YYYY-MM-DD>.md` per UTC date with per-bot stats, configs (extracted from boot events), market context (Polymarket UP/DOWN resolution skew, bucketed separately for 5m and 15m markets), news-recorder summary, and auto-derived **direction** + **risks** sections. Tolerates gamma's HTTP 422 at offset ≥ 2100. `--push` flag commits + pushes.
- `reports/STRATEGY_FINDINGS.md` — running curated log of evidence-based findings so we don't relearn the same lessons. Daily reports under `reports/<date>.md` are kept in git.
- Sweet-band v2 applied: all 6 main variants → `[0.60, 0.75]`; `trader-eth-5m-wide` refocused to `[0.75, 0.90]` as a tail-bucket probe.

### Phase C — Strategy extraction 🟡 (local workspace, not deployed — status 2026-06-21)
- ✅ `src/strategies/polymarket_latency_arb.py` now contains the pure latency-arb decision tree:
  signal gate (`threshold` / `cooldown` / Coinbase confirmation), market/window-anchor
  gate, and orderbook/sweet-band gate.
- ✅ `polymarket/live_trader.py` is now a shim around that strategy logic while
  keeping the current live responsibilities: Binance/Coinbase streams, Gamma market
  refresh, orderbook lookup, FOK execution, legacy `logs/live_*.jsonl`, and
  new-schema `BotLogger` writes.
- ✅ Tests cover the pure decision cases and a mocked runner path:
  `tests/test_polymarket_latency_arb.py` and `tests/test_live_trader_shim.py`.
- ✅ Current local verification: `python3 -m unittest discover -s tests` passes
  25 tests.
- ⏳ Still required before calling Phase C production-complete: review the diff,
  run a shadow/dry-run container with the shim, and confirm live log parity against
  the pre-extraction behavior. No deployed bot has been switched because these
  changes are currently local/uncommitted.

### Phase D — Stream bus + supervisor 🟡 (local skeleton, not wired to live — status 2026-06-21)
- ✅ `src/core/stream_bus.py` exists with `StreamSpec`, in-process publish/subscribe,
  bounded subscriber queues, and `block` / `drop_oldest` / `drop_newest` policies.
- ✅ `src/core/bot.py` exists with a minimal `BotRuntime` that consumes one or more
  bus subscriptions and calls a sync or async handler.
- ✅ `src/core/supervisor.py` exists with a conservative supervisor skeleton:
  shared `StreamBus`, deduped data sources by stream specs, bot task startup,
  cancellation cleanup, and error propagation.
- ✅ Tests cover bus filtering/policies, bot runtime consumption/cleanup, and
  supervisor source dedupe/lifecycle:
  `tests/test_stream_bus.py`, `tests/test_bot_runtime.py`, `tests/test_supervisor.py`.
- ⏳ Next Phase D step: add one shadow supervisor entry point/container that runs
  ONE bot (e.g. `eth-5m-via-supervisor`) alongside the existing services without
  removing any current per-variant trader.
- ⏳ After a week of clean shadow operation, migrate remaining bots one at a time.
  Each migration should be a small config/compose change; only remove per-variant
  docker services after all six have proven stable under the supervisor.

### Phase E — Outcome tracker + unified backtester
- Implement `core/outcome_tracker.py` (and `venues/polymarket/resolution.py`).
- Implement `backtest/replayer.py`. Run `parity_test.py` against the last 30 days of live BTC-5m logs; fix discrepancies until within $1.
- `polymarket/backtest/replay_tick.py` → `_retired/`.

### Phase F — New venues
- Add `src/venues/kalshi/` (Phase 3 from `RESEARCH_2026.md`).
- Add `src/venues/hyperliquid/` if/when Phase 4 ships.

### Phase G — UI
- ✅ Early operational sidecar exists in `control-center/` and `docker-compose.yml`:
  authenticated read-only web UI indexing Polymarket/Hyperliquid JSONL into
  SQLite, exposing overview/events/funding/files/container inventory. This is
  useful now but is **not** the final Phase G UI described below.
- ⏳ Final Phase G still waits for post-Phase-D/PnL schema stability and must add
  halt controls plus fire/PnL history from the stable event model.

**Rule:** no phase removes a working code path until the replacement has run live for ≥1 week.

### Acceptance criteria per phase

| Phase | Done when |
|---|---|
| B | Parallel logs match within 1 row over a 24h window. |
| C | Unit tests pass; live behavior of `live_trader.py` shim is bit-identical to pre-extraction. |
| D | Supervisor has run all 6 bots for 7 days, no orphan crashes, WS connections halved. |
| E | Replay PnL matches live PnL within $1 for last 30 days. |
| F | New venue dry-runs 100 orders end-to-end; reconciliation matches venue's balance API. |
| G | Operator can see fire/pnl history and halt a bot from the UI without ssh. |

---

## 17. Future UI dashboard

### Minimum viable UI

- FastAPI service in `src/ui/api/`, static frontend in `src/ui/web/`.
- Endpoints:
  - `GET /bots` — config + live status (last event ts, error counts, day-pnl)
  - `GET /bots/{name}/events?since=...&type=fire,pnl` — tail bot log
  - `GET /bots/{name}/pnl?from=...&to=...` — realized + open PnL summary
  - `POST /halt/{bot}` / `DELETE /halt/{bot}` — manage halt files
  - `POST /backtest` — `{config, from, to}` → result id (async job)
  - `GET /backtest/{id}` — status + summary + trades.csv link
- Frontend: HTMX + small Alpine.js or a single React app — three tabs (Bots / Logs / Backtest).
- Auth: HTTP basic + Tailscale-only network exposure.

### Why "future" not "now"

Building UI atop the current logger schema is throwaway. After Phase D the schema stabilizes; UI becomes a 1-week build.

---

## 18. Open design decisions

| # | Decision | Options | Current lean |
|---|---|---|---|
| 1 | Process model | one container per bot vs one supervisor | **Supervisor** — saves WS connections, isolation via task-level crash-restart. |
| 2 | Config format | YAML / TOML / Python | **YAML + pydantic**. |
| 3 | Log storage | JSONL / SQLite / Postgres | **JSONL** v1 → **SQLite** when UI needs queries → **Postgres** only if multi-host. |
| 4 | Stream bus | in-process / Redis / NATS | **In-process** v1. |
| 5 | UI auth | none (LAN) / basic / OAuth | **Basic + Tailscale** v1. |
| 6 | Backtest dataset format | parquet / JSONL / duckdb | **Parquet candles, JSONL events, duckdb ad-hoc**. |
| 7 | Legacy `backtest/` (forex) | keep / merge / retire | **Keep**; unified replayer adopts its `Strategy` pattern. |
| 8 | `regime-classifier/` integration | wire / standalone / retire | **Wire as `data_source`** emitting `RegimeEvent`. |
| 9 | Cross-venue arb context | ctx exposes venue clients vs pre-aggregated event | **Pre-aggregated event** — strategy stays pure. Arb strategies subscribe to a `CrossVenueQuoteEvent` that the bot composes from two orderbook subscriptions. |
| 10 | Strategy state durability | volatile only / opt-in checkpoint / required checkpoint | **Volatile only** v1; if a strategy needs durability it logs explicit checkpoint events. |
| 11 | Sweet-band gate location | strategy / executor (via intent fields) | **Executor** — see §5.3 reasoning. |
| 12 | Bus backpressure | drop / block | **Block by default**, drop opt-in per source. |
| 13 | Configs in git | yes / no | **Yes** — secrets via `${ENV}` interpolation. |
| 14 | Hot reload of configs | yes / no | **No** — git → rebuild → restart only. |
| 15 | intent_id generation | uuid4 / hash(ts,bot,...) | **uuid4** in live; **seeded hash** in replay for determinism. |

---

## 19. Glossary

- **Bot** — one configured strategy instance, identified by its config file `name` (e.g. `btc-5m`). Multiple bots can share data sources and venues.
- **Strategy** — pure logic that consumes events and emits order intents. Registered via `@register_strategy(name)`.
- **Venue** — anywhere we can place orders (Polymarket, Kalshi, Hyperliquid). Owns auth, order endpoints, and resolution polling.
- **Data source** — read-only feed (price WS, news WS, on-chain indexer). Emits events into the stream bus.
- **Stream bus** — in-process pub/sub so N bots can share 1 WS subscription.
- **Supervisor** — single process that loads N bot configs, instantiates shared resources, runs the bots, restarts crashed ones.
- **Replayer** — backtester that drives the same strategy code with logged events instead of live ones.
- **Variant** — historically "this same strategy with a different asset/timeframe config". After migration this is just "another bot config".
- **Fire** — a bot's decision to place an order (live or dry). One JSONL row.
- **Skip** — a decision *not* to place an order, with a recorded reason from the controlled vocabulary.
- **Fill** — a venue's response to an order intent. Includes synthetic fills in dry-run.
- **Sweet band** — the Polymarket ask price range where the latency-arb edge is +EV (currently `[0.30, 0.40]`).
- **Intent ID** — uuid4 stamped on every OrderIntent. Join key across `fire`, `fill`, `pnl` log rows.
- **Outcome** — the resolved side of a market (e.g. "Up" / "Down" for Polymarket binaries).
- **PnL row** — log entry emitted by `outcome_tracker` once a position resolves.
- **Halt file** — file under `logs/halt/` that pauses a bot or the whole fleet without a restart.
- **Crash-only** — design principle: no graceful-shutdown path that itself can fail. SIGKILL is always acceptable.

---

## 20. Related docs

- `RESEARCH_2026.md` — strategy research + iterative follow-up log. Read this to know *what* to build.
- `polymarket/DEPLOY_NOTES.md` — current deploy reference (env vars, log paths, redeploy steps).
- `README.md` — user-facing setup guide.
- `polymarket/HOSTING.md` — VPS sizing notes.

---

# Part II — In-depth design (escape hatches & edge cases)

> Part I described the generic happy path. Part II covers the parts that **don't** fit cleanly — and the discipline that keeps them uniform anyway.
>
> **Operating principle for everything in Part II:** an escape hatch is acceptable only if it preserves three properties:
> 1. **Log uniformity** — its events still land in JSONL with the schema headers (`ts`, `bot`, `strategy`, `event`).
> 2. **Restart uniformity** — the supervisor still starts and stops it the same way as a generic bot.
> 3. **Backtest accessibility** — it can be exercised in replay, even if the replay is degenerate (e.g. "stub the WS source with a fixture").
>
> A new strategy/venue/feature that breaks any of these three requires explicit doc + an Open Design Decision row in §18 (Part I).

## Part II — TOC

- [A. Escape hatches — when the generic model doesn't fit](#a-escape-hatches--when-the-generic-model-doesnt-fit)
- [B. Strategy kinds beyond pure event-driven](#b-strategy-kinds-beyond-pure-event-driven)
- [C. Order state machine](#c-order-state-machine)
- [D. Multi-leg & atomic execution](#d-multi-leg--atomic-execution)
- [E. Time, clocks, and determinism in depth](#e-time-clocks-and-determinism-in-depth)
- [F. Error taxonomy & retry policy](#f-error-taxonomy--retry-policy)
- [G. Performance, capacity, and operational budgets](#g-performance-capacity-and-operational-budgets)
- [H. Multi-bot coordination — portfolio risk, shared signals, fleet-wide gates](#h-multi-bot-coordination)
- [I. Custom log event types & schema evolution](#i-custom-log-event-types--schema-evolution)
- [J. Venue-specific quirks & their accounting](#j-venue-specific-quirks--their-accounting)
- [K. SQL schema when we graduate from JSONL](#k-sql-schema-when-we-graduate-from-jsonl)
- [L. Metrics, health, observability](#l-metrics-health-observability)
- [M. Worked examples of escape-hatch strategies](#m-worked-examples-of-escape-hatch-strategies)
- [N. Anti-patterns we've seen tempt us](#n-anti-patterns-weve-seen-tempt-us)

---

## A. Escape hatches — when the generic model doesn't fit

The clean model in Part I (`required_streams → on_event → OrderIntent → Fill → Outcome`) covers ~80% of strategies. Below are the dimensions where a real strategy can break the mold, and the **escape hatch** we add for each — designed so the runtime still owns lifecycle, logging, and restart.

### A.1 Dimensions of misfit

| Dimension | Generic model assumes | Real-world break |
|---|---|---|
| **Time model** | React to inbound events | A market-maker re-quotes on a 250ms timer with no input event |
| **State shape** | Per-bot dict, volatile | A whale-tracker holds 10MB of indexed addresses across restarts |
| **Decision atomicity** | One event → zero-or-more independent OrderIntents | A two-legged arb needs both legs filled or neither |
| **Coordination scope** | Bots are independent | A portfolio risk cap spans all bots |
| **Event vocabulary** | Closed set in `core/types.py` | A venue emits funding-rate events nobody else cares about |
| **Order primitives** | FOK limit, GTC limit, market | A perp venue has stop-market, trailing-stop, TP/SL brackets |
| **Outcome model** | Binary win/lose + payout | A perp position has continuous mark-to-market PnL + funding accruals |
| **Auth model** | Static creds from env | An OAuth-refresh flow needs a background refresher |
| **Rate limits** | Best-effort, retry-on-429 | A venue has per-second quotas you must pre-allocate across N strategies |
| **Data source latency** | Push (WS) | A REST poller with a baked-in 60s budget |
| **Multi-instance** | One bot per config | A single config wants to fan out to all currently-live markets at once |

### A.2 The escape hatch catalogue

For each misfit, here's the sanctioned pattern. **None of these requires a new top-level concept** — each is an opt-in flag, capability, or sub-interface within the existing architecture.

| # | Misfit | Escape hatch | What it preserves |
|---|---|---|---|
| 1 | Timer-driven re-quoting | `TickEvent` source w/ per-bot interval; strategy treats it as just another event | All three uniformities. |
| 2 | Heavy state | Strategy implements `checkpoint(ctx) -> bytes` + `restore(ctx, bytes)`; supervisor calls them on shutdown/boot. Persisted to `logs/checkpoints/<bot>.bin` | Log + restart uniformity; backtest replayer skips restore (cold-start always). |
| 3 | Atomic multi-leg | `OrderIntent` becomes `OrderGroup(intents=[...], atomicity="all_or_none")`; venue executes via batch / unwinds on partial | Log uniformity (one `fire` row per leg, joined by `group_id`). |
| 4 | Portfolio coordination | `core/portfolio.py` exposes `read_only_state()` to BotContext; one bot can READ another's open positions but never WRITE | Restart + log uniformity; a portfolio gate is just another kill_switch check. |
| 5 | New event vocabulary | Strategies subclass `Event` via a venue-local type; bus is type-polymorphic; logger has an `event_kind` discriminator + a free-form `payload` blob | All three. |
| 6 | Exotic order types | `OrderIntent.order_type` is a free string; venue knows how to translate. Unsupported types fail at validation, not at runtime | Log + restart uniformity; backtest fill_model may stub unsupported types. |
| 7 | Continuous PnL | Position tracker periodically marks open positions; emits `pnl_mtm` events (vs `pnl` for realized) | Log uniformity; the UI distinguishes by event type. |
| 8 | Auth refresh | Venue exposes `async def ensure_auth()`; called on every API touch with a 5min cache | Restart uniformity (token re-acquired on boot). |
| 9 | Rate-limit allocation | Per-venue token bucket in `venues/base.py`; bots that share a venue share the bucket via the supervisor | Log + restart uniformity. |
| 10 | REST-only data source | Same `data_source` interface, internal loop is `await sleep(); await fetch();` | All three. |
| 11 | Fan-out over N markets | Strategy returns a list of intents per event; alternately, one config can declare `instance_per_market: true` and the supervisor materializes N bots (one per current market) | Log uniformity (each instance is its own `bot=<name>__<market_id>`). |

### A.3 The rule for adding a new hatch

When something doesn't fit any row of §A.2:

1. **First try to bend the misfit into an existing hatch.** Most "I need X" requests are X-disguised-as-Y.
2. If genuinely new, propose the smallest possible interface extension. Add a row to §A.2.
3. Add an Open Design Decision (§18 Part I).
4. Ship behind a flag — the new path runs alongside the old until proven.
5. Never ship a "for now" hack. The repo is small enough that "for now" code metastasizes within weeks.

### A.4 What we explicitly refuse

Things that have come up and we said no to, with reasoning. Future-you (or another agent) should read this before re-proposing them.

| Refused | Reason |
|---|---|
| **Per-strategy thread pool / process pool** | Adds GIL-vs-multiprocess complexity for sub-1% of strategies. If a strategy is CPU-bound enough to need it, it should pre-compute and emit synthetic events. |
| **Generic "plugin" system loading arbitrary Python from outside the repo** | The repo is the unit of audit. Strategies that aren't in git aren't backtested, aren't reviewable, aren't restartable. |
| **Strategy ↔ strategy direct messaging** | A strategy that needs another strategy's signal subscribes to its log events via the bus (see §H). Direct messaging is the seed of god-objects. |
| **YAML inheritance/templating beyond anchors** | Operational comprehension > DRY. A 40-line YAML you can read top-to-bottom beats a 10-line YAML that resolves through 3 indirections. |
| **A general-purpose DAG / workflow engine for strategies** | Strategies are pure event handlers; if you need a DAG, you need a different abstraction layer (and probably a different repo). |
| **Hot-reload of strategy code** | See §9.5. Git → rebuild → restart is unambiguous. |
| **Per-bot Python virtualenv** | One image, one venv. Strategies that need exotic deps justify the dep at the image level or don't ship. |

---

## B. Strategy kinds beyond pure event-driven

The Strategy ABC in §7 (Part I) is the **event-driven** kind. Three more kinds exist, all of which subclass the same `Strategy` ABC and share the same lifecycle hooks (`on_boot`, `on_shutdown`, `on_fill`, `on_outcome`) — they differ only in *how the runtime drives them*.

### B.1 `kind = "event_driven"` (default)

What §7 describes. The runtime calls `on_event(ev, ctx)` for every event matching `required_streams()`. No `await` in strategy code. Examples: `polymarket_latency_arb`, `news_headline_bucket`.

### B.2 `kind = "ticked"` — wall-clock cadence

Strategy declares an interval and the runtime injects `TickEvent`s at that cadence in addition to its other streams. Example: a quoter that re-prices every 250ms even if the underlying hasn't traded.

```python
@register_strategy("polymarket_maker_rebate")
class PolymarketMakerRebate(Strategy):
    KIND = "ticked"
    TICK_INTERVAL_MS = 250

    def required_streams(self):
        return [
            StreamSpec("polymarket_orderbook", market_filter="...rebated..."),
            StreamSpec("ticker", interval_ms=self.TICK_INTERVAL_MS),    # ← synthetic
        ]
    ...
```

The synthetic tick is published by `core/clock.py`'s ticker task. In replay, ticks are deterministically interleaved at exact UTC multiples of the interval — so the replayer doesn't drift from live.

### B.3 `kind = "async_loop"` — strategy owns its coroutine

When the strategy genuinely needs to `await` (e.g. a quoting bot that places an order, waits for ack, then immediately decides the next quote based on the ack), it implements:

```python
class PolymarketMakerRebate(Strategy):
    KIND = "async_loop"

    async def run(self, ctx: AsyncBotContext) -> None:
        """Owns its lifetime. Must check ctx.cancelled periodically.
        ctx.emit_intent(intent) replaces returning intents from on_event."""
        while not ctx.cancelled():
            quotes = self._compute_quotes(ctx.state)
            for q in quotes:
                fill = await ctx.place(q)
                self._learn(fill, ctx.state)
            await asyncio.sleep(0.25)
```

`AsyncBotContext` extends `BotContext` with:
- `place(intent) -> Fill` — awaits, returns the fill. (Equivalent to: emit intent → wait for the executor's fill row.)
- `cancel(order_id) -> bool`
- `cancelled() -> bool` — true once supervisor signals shutdown
- `wait_event(spec, timeout) -> Event | None`

**Why offer this kind?** Because some strategies are fundamentally request/response with the venue (place → ack → decide → place), not stream-driven. Forcing them into `on_event` produces a state machine spaghetti.

**Cost:** async strategies break the "no I/O in strategies" rule. We accept this for the explicit `async_loop` kind. They are subject to the same logging discipline (intents flow through the executor, all calls logged) but their unit-testability is lower (need an async test harness with a fake venue). The unit-test ban on importing `httpx`/`asyncio` lifts for `async_loop` strategies — they must import asyncio. The replayer drives them by stubbing `ctx.place` and `ctx.wait_event` against the dataset.

**Discipline:** an `async_loop` strategy still **never** instantiates a venue client directly. It goes through `ctx.place` / `ctx.cancel`. That keeps the executor as the only path that talks to the venue.

### B.4 `kind = "scheduled"` — runs at cron-like times

Some strategies (regime classifier consumers, daily-rebalancers, on-chain indexers) run once per hour/day, not continuously.

```python
class HourlyRebalance(Strategy):
    KIND = "scheduled"
    SCHEDULE = "0 * * * *"      # cron syntax — supervisor parses, schedules
    async def run_once(self, ctx) -> list[OrderIntent]: ...
```

The supervisor schedules these via a single in-process cron loop (no system cron). Misses (process down at scheduled time) are logged but **not** caught up — running yesterday's rebalance at noon today is worse than skipping it. A `scheduled_missed` event marks the gap.

### B.5 Multiplexing kinds

A strategy can declare multiple kinds — e.g. `kind=["event_driven", "ticked"]` to get both. The runtime drives both paths concurrently; the strategy's `on_event` and `on_tick` must be re-entrant w.r.t. `ctx.state` (which is single-threaded asyncio, so this is "don't yield in the middle of a state-modifying critical section" — easy in pure event_driven, harder in async_loop).

---

## C. Order state machine

§11 (Part I) covered the simple FOK case where `place_order` returns a terminal Fill. Real venues have more states.

### C.1 Canonical state diagram

```
                           ┌──────────────────────────────┐
                           ▼                              │
        place_order   ┌─────────┐  ack    ┌──────────┐    │
INTENT ─────────────► │ PENDING ├────────►│ OPEN     │    │ (cancel/replace)
                      └─────────┘         └────┬─────┘    │
                           │ reject              │ partial fill
                           ▼                     ▼
                      ┌─────────┐         ┌──────────┐
                      │ FAILED  │         │ PARTIAL  │
                      └─────────┘         └────┬─────┘
                                               │ fill complete
                                               ▼
                                          ┌──────────┐
                                          │ FILLED   │
                                          └──────────┘
                                          │ cancel
                                          ▼
                                          ┌──────────┐
                                          │ CANCELLED│
                                          └──────────┘
                                          │ expire (TTL)
                                          ▼
                                          ┌──────────┐
                                          │ EXPIRED  │
                                          └──────────┘
```

For FOK orders only `PENDING → FAILED` and `PENDING → FILLED` exist (no `OPEN` state).

For GTC: every state transition emits an event:

```jsonc
{"event": "order_ack",     "intent_id": "...", "order_id": "0x...", "venue_ts": "..."}
{"event": "order_partial", "intent_id": "...", "filled_size": 2.0, "remaining": 8.0, ...}
{"event": "order_filled",  "intent_id": "...", "total_filled": 10.0, "vwap": 0.34, ...}
{"event": "order_cancelled","intent_id": "...", "filled_at_cancel": 2.0, ...}
{"event": "order_expired", "intent_id": "...", "filled_at_expire": 0.0, ...}
```

These are the *granular* events. `fire` (the existing umbrella event) is emitted at intent submission and carries the final terminal state if the call is synchronous (FOK). For async (GTC) orders, `fire` records only the intent + ack; the lifecycle events follow.

### C.2 Order state in the strategy

Strategies should **not** be the source of truth for order state. The venue is. Strategies that need to know whether an order is still open ask `ctx.order_status(order_id)` which reads from the executor's local order-state cache (which is refreshed by lifecycle events).

`on_fill` is called for `order_filled` and `order_partial` (with `fill.terminal: bool` indicating which). It is NOT called for `order_cancelled` or `order_expired` unless the strategy opts in via `wants_terminal_callbacks = True`.

### C.3 Cancel races

A common bug: strategy cancels at T, but the venue fills at T+5ms. We log both events (`cancel_requested` at T, `order_filled` at T+5ms), and the executor reconciles — net position is the fill, the cancel is informational. The strategy must tolerate `on_fill` arriving *after* it asked to cancel.

### C.4 Order ID stability across restarts

If the supervisor restarts while a GTC order is open, the executor on boot calls `venue.list_open_orders()`, reconciles against `logs/positions/open.jsonl` (matched by `client_order_id` = our `intent_id`), and resumes monitoring. Orders not in our log but on the venue are flagged `unknown_open_order` and surfaced to the operator — never auto-cancelled (could be a different bot, manual order, etc.).

---

## D. Multi-leg & atomic execution

Some strategies need two-or-more orders to be all-filled-or-none-filled. Examples: Polymarket × Kalshi arb (buy Yes on one, No on the other simultaneously); funding-rate arb (open perp short + spot long together).

### D.1 The OrderGroup primitive

```python
@dataclass(frozen=True)
class OrderGroup:
    group_id: str                       # uuid4 — joins all legs in logs
    intents: tuple[OrderIntent, ...]
    atomicity: str                      # "all_or_none" | "best_effort" | "sequential"
    timeout_s: float = 5.0              # wall-clock budget for the whole group
```

A strategy returns an `OrderGroup` instead of a list of intents when atomicity matters. The executor's behavior:

| atomicity | Behavior |
|---|---|
| `all_or_none` | Place all legs concurrently. If any rejects or doesn't fill within `timeout_s`: cancel all open legs, **immediately unwind any partial fills via opposite orders**, log `group_failed` with per-leg detail. PnL of the unwind is the cost of the failure. |
| `best_effort` | Place all legs concurrently. Log whatever happens — partial groups are valid outcomes. |
| `sequential` | Place leg 1; only place leg 2 if leg 1 fills; etc. Useful when leg 1 confirms a market exists (e.g. quote alive) before committing. |

### D.2 Unwind logic

`all_or_none` unwinding is the hard part. The executor must:

1. Track which legs filled, which are still open, which rejected.
2. Cancel all open legs (best-effort — some venues may take time to ack cancel).
3. For each leg that filled (or partially filled), submit an opposite-side market order to flatten.
4. Log every step: `group_partial_fill`, `cancel_leg`, `unwind_fill`, `group_failed`.

The unwind itself can fail (no liquidity, venue down). In that case we log `unwind_failed` and **halt the bot** (touch `logs/halt/<bot>`). An operator must intervene — we never silently leave a directional position from a failed atomic group.

### D.3 Multi-venue atomicity is impossible — we approximate

There is no cross-venue 2PC. "Atomic" cross-venue execution is racy by definition. The `all_or_none` semantic is *unwind-on-failure*, not true atomic commit. We document the worst-case slippage (depth × spread × 2) and only run atomic-cross-venue strategies with size where worst-case is tolerable.

### D.4 Backtest of multi-leg

The replayer's fill model must simulate per-leg latency between venues. A simple model: each venue gets a fixed `simulated_latency_ms`; the leg "places" at its `intent.ts`, "acks" at `intent.ts + latency`. The unwind path is exercised by injecting failures (e.g. mark every Nth leg as `rejected`) and asserting bookkeeping is correct.

---

## E. Time, clocks, and determinism in depth

### E.1 The two clock contexts

| Context | Clock | `ctx.now()` returns | Used by |
|---|---|---|---|
| Live | `RealClock` | wall-clock UTC, ms precision | Production supervisor |
| Replay | `ReplayClock(start_ts)` | last event's ts (or last explicit advance) | Backtest replayer |

Strategy code **must** use `ctx.now()`, never `datetime.now()` or `time.time()`. A linter rule + a unit test (importing every strategy and inspecting its AST) enforces this.

### E.2 Event ordering invariants

| Invariant | Holds in live? | Holds in replay? |
|---|---|---|
| Events from one source arrive in `ts` order | Yes | Yes |
| Events across sources are merged by `ts` ± bus jitter | Best-effort (~50ms tolerance) | Exact (sorted dataset) |
| `event.ts <= ctx.now()` at delivery | Live: ~always, with small jitter | Replay: always exactly equal |
| Two runs of the replayer produce identical fire sequences | n/a | Required — drift = bug |

### E.3 Sources of non-determinism we must control

- **`uuid4()` for intent_id.** Replay uses a seeded `random.Random(replay_seed)` to generate intent_ids. The seed is derived from `(strategy_name, dataset_id)` so the same replay always produces the same ids.
- **`asyncio.gather` ordering.** Order of completion is not deterministic across runs. We avoid relying on it in any decision-relevant path.
- **Dict iteration order.** Pin to Python ≥3.7 (insertion order). We do.
- **External API responses.** Venues' fills in live are non-deterministic. The replayer uses fill_model output instead of recorded fills, so this is a non-issue for the replay's own determinism. The live-vs-replay PnL parity check (§13.5) accepts up to $1 drift to tolerate this.

### E.4 Replay clock advance policy

The replay clock advances when:

1. The next event is dequeued — clock jumps to `event.ts`.
2. The strategy calls `await ctx.sleep(s)` (only in `async_loop` strategies) — clock advances by `s`.
3. The executor "places" an order — clock advances by `venue.simulated_latency_ms` before producing the Fill.

Between these, the clock is frozen. A strategy that depends on "wall-clock just passed" between events would behave differently in replay; that's a bug in the strategy and the replayer's parity test catches it.

### E.5 Daylight-saving / TZ traps

All `ts` fields are UTC, tz-aware. There is no local time anywhere in the system. Polymarket's market titles use "ET" — we parse those into UTC at the gamma adapter, never propagate ET past the boundary. Crypto markets don't have a session concept anyway.

### E.6 Latency budgets in live

For the latency-arb strategy specifically, we care about end-to-end latency from "Binance trade happened" to "FOK placed":

| Hop | Budget | Observed (median) |
|---|---|---|
| Binance trade → our WS recv | <50ms | ~30ms |
| Bus publish → strategy on_event | <2ms | <1ms |
| Strategy decision | <1ms | <0.5ms |
| Executor: gates + CLOB orderbook fetch | <200ms | ~120ms |
| Place FOK → ack | <300ms | ~180ms |
| **Total** | **<550ms** | **~330ms** |

Every fire row carries `lat_ms_decide`, `lat_ms_book`, and `lat_ms_order` measuring wall-clock from Binance tick → orderbook resolve → CLOB POST ack, so each hop is observable. Drift > 50% over a week triggers an investigation. The strategy itself has no latency budget (pure data); the budget is on the framework.

Two executor-side optimizations keep the hot path tight: `polymarket/clob.py` uses a module-level `httpx.Client` with keep-alive (max 4 connections) so each `get_orderbook` skips the TCP+TLS handshake, and `place_buy_fok` is dispatched via `run_in_executor` so the sign+POST cycle does not stall the Binance price stream. When `POLY_BOOK_WS_CACHE=true`, `polymarket/orderbook_ws_cache.py` subscribes to the active-window markets' token_ids on the Polymarket CLOB WS and serves top-of-book from memory, falling back to the HTTPS path on cache miss or a snapshot older than 5 s.

---

## F. Error taxonomy & retry policy

### F.1 Error categories

| Category | Examples | Retry policy |
|---|---|---|
| **Transient network** | DNS timeout, 5xx, WS disconnect | Exponential backoff (250ms → 30s cap), unlimited |
| **Rate limit (429)** | Venue saying slow down | Honor `Retry-After` header; if absent, 1s baseline with jitter |
| **Auth** | 401/403 | Re-fetch creds once; if still failing, halt the bot, alert operator |
| **Validation** | 400, "min size", "invalid market_id" | Do NOT retry. Log + log skip(reason). Strategy must be fixed. |
| **Insufficient balance** | "insufficient USDC" | Halt the bot (touch halt file). Operator must top up. |
| **Venue degraded** | Repeated 5xx > N in window | Halt the venue (touch `logs/halt/venue=<name>`) — all bots using it pause. |
| **Programmer error** | `KeyError`, `AssertionError` | Bot task crashes, supervisor restarts; if it crashes 5× in 5min, the bot is auto-halted. |

### F.2 The retry budget

Every retry-able call carries a budget: `RetryConfig(max_attempts, max_total_s, backoff_base_s, backoff_cap_s, jitter)`. Defaults are per-venue (a slow venue gets longer budgets). Strategies do not configure this — it's framework concern.

### F.3 Error rows in the log

A controlled vocabulary, just like skip reasons:

```jsonc
{"event": "error", "category": "transient_network", "where": "venue=polymarket.place_order",
 "msg": "ConnectError: ...", "retry_attempt": 3, "will_retry": true}
{"event": "error", "category": "auth", "where": "venue=polymarket.ensure_auth",
 "msg": "401", "retry_attempt": 1, "will_retry": false, "action": "halt_bot"}
```

The `category` field powers UI alerts and dashboards.

### F.4 Circuit breaker

After 10 consecutive `transient_network` errors against a single venue, we open a 5-minute circuit breaker: no calls to that venue (all return `Fill(ok=false, reason="circuit_open")`), one probe attempt at the 5-min mark, close on success. Logged as `circuit_opened` / `circuit_closed`.

---

## G. Performance, capacity, and operational budgets

### G.1 Compute budgets (single 4-vCPU VPS)

| Component | Max events/sec | Memory cap | CPU cap |
|---|---|---|---|
| Binance WS (1 asset) | ~50 | 50 MB | 5% |
| Coinbase WS (1 asset) | ~30 | 50 MB | 5% |
| Polymarket WS (10 markets) | ~200 | 100 MB | 10% |
| One latency-arb bot | n/a (drives off above) | 50 MB | <1% |
| Supervisor + bus | n/a | 100 MB | 5% |
| Outcome tracker | <1 ev/s | 50 MB | <1% |
| Resolution poller | n/a | 30 MB | <1% |

Headroom for 20 bots before the VPS budget is concerning. Above 20 bots we re-evaluate (in-process bus may need a per-CPU shard, or we move to a small Redis pub/sub).

### G.2 Disk budgets

| Log type | Size/day (typical) |
|---|---|
| `bot=<name>` log (per bot) | <1 MB |
| `recorder=binance_ws` per asset | ~50 MB |
| `recorder=ws_polymarket` per market-day | ~10 MB |
| `recorder=news` | <1 MB |

A 500 GB VPS holds years of all-asset recordings. Storage is not a constraint.

### G.3 Network budgets

| Connection | Type | Reconnect frequency |
|---|---|---|
| Binance WS | 1 per asset | ~hourly (clean) |
| Coinbase WS | 1 per asset | ~hourly |
| Polymarket WS | 1 per market group | ~daily |
| Polymarket REST | sporadic | n/a |
| Gamma REST | 1 every 60s per asset-timeframe pair | n/a |

If we reach 100+ bots, WS deduplication via the supervisor already saves us. REST polling does not deduplicate (different bots may want different parameters) but is rate-bucketed per venue.

### G.4 The "running 100 bots" thought experiment

Suppose RESEARCH_2026 throws us another 90 strategies. What breaks first?

1. **Bus throughput** — fine (event rate scales with sources, not bots).
2. **Logger contention** — JSONL writes are per-bot, so 100 bots = 100 file writers. fsync overhead at ~10/sec/bot = 1k fsyncs/sec, which is fine on SSDs. We don't fsync per write anyway (buffered, fsync every 1s).
3. **Memory** — 100 bots × 50MB = 5GB. Within VPS budget but the supervisor process is suddenly very fat. Consider per-pod supervisors (one supervisor per N bots) before this becomes an issue.
4. **Cognitive load** — 100 YAML files. Need a `configs/_by_strategy/` index, an `ls --bots` CLI, and tooling.

We're nowhere near this; documented for when we get there.

---

## H. Multi-bot coordination

Most bots are independent. Three patterns where they aren't:

### H.1 Portfolio-wide risk caps

Hard cap on total open USDC across all bots, or daily loss cap fleet-wide. Implemented as a kill-switch that reads `logs/positions/open.jsonl` (already aggregated) and today's `pnl` rows across all bot logs. Lives in `core/portfolio.py`; called from every bot's pre-place check.

Config lives in `configs/_portfolio.yaml`:

```yaml
max_total_open_usdc: 200
max_daily_loss_usdc: -100
max_per_venue_open_usdc:
  polymarket: 100
  kalshi: 100
halt_fleet_on_breach: true
```

### H.2 Shared signal consumption (one bot produces, others consume)

Example: a regime-classifier bot emits `RegimeEvent`s; several trading bots subscribe to filter their fires.

The producer bot is just a normal bot whose only "action" is publishing — it returns an empty intent list, but `on_event` (or `run` in async_loop form) calls `ctx.publish(ev)`. The bus delivers to subscribers.

Wait — that allows a bot to push events into the bus, breaking the read-only-strategy rule. Resolution: producer bots use `kind = "async_loop"` and explicitly the runtime gives them a `ctx.publish` method (event-driven strategies do NOT have it). This way the rule "event-driven strategies are pure" is preserved; "producer" is a recognized escape-hatch kind.

### H.3 Cross-bot lockouts (only one bot trades a market at a time)

Sometimes we don't want two bots to both fire on the same market_id. We implement a per-market mutex in `core/portfolio.py`:

```python
class MarketLocks:
    def try_acquire(self, market_id: str, bot: str) -> bool: ...
    def release(self, market_id: str, bot: str) -> None: ...
```

A bot's executor calls `try_acquire` before placing. On failure, logs `skip(reason="market_locked_by_other_bot", debug={"holder": "..."})`. Released on `pnl` (position resolved).

This is opt-in per bot (`execution.respect_market_lock: true`). Default is off (independent bots).

---

## I. Custom log event types & schema evolution

### I.1 Adding a new event type

A strategy or venue may need to log something the controlled vocabulary doesn't cover.

1. Add the event name to `core/logger.py:KNOWN_EVENTS`.
2. Define its required fields in a docstring + a pydantic model (for runtime validation).
3. Add a row to §10 (Part I).
4. The logger validates rows against the known schema at write time in debug mode; in prod it logs unknown events with `event_unknown_kind` tag (never silently drops).

### I.2 Schema evolution rules

| Change | Allowed? | How |
|---|---|---|
| Add an optional field | Yes | Just write it. Readers ignore unknown fields. |
| Add a required field | No directly — add optional, backfill if needed, then enforce | Two-phase. |
| Rename a field | No | Add new, write both for one release, deprecate old, remove. |
| Change a field type | No | Add a new field with new type. |
| Add a new `event` value | Yes | Per §I.1. |
| Remove an `event` value | No | Stop emitting; keep parsers tolerant forever. |

The principle: **logs are durable**. Code that reads month-old logs must still work.

### I.3 Schema version

Every row carries `schema_v: int` (currently `1`). Bumped on breaking changes. Readers dispatch by version. We expect ~1 bump every couple of years.

---

## J. Venue-specific quirks & their accounting

### J.1 Polymarket (binary, CLOB)

- **Outcome model:** binary, payout 0 or 1 USDC per share.
- **PnL:** realized at resolution. `payout = filled_size if won else 0`.
- **Quirks:** order size denominated in shares OR USDC depending on endpoint; we always send USDC and let the venue back-compute. FOK orders skip the rebate eligibility check entirely.
- **Resolution lag:** 1-5 min after `window_end`. Poller catches it.
- **Settle source:** Chainlink aggregate for 5-min, Binance for hourly. We log this in the market record.

### J.2 Kalshi (binary, request-based)

- **Outcome model:** binary, $1 contract.
- **PnL:** realized at resolution. Similar to Polymarket.
- **Quirks:** orders are batched in 100ms windows server-side. Latency-arb strategies that fire on sub-100ms signals will see this; we backoff the snipe-window to >150ms when targeting Kalshi.
- **KYC:** required for US persons. Operator handles.

### J.3 Hyperliquid (perp, on-chain)

- **Outcome model:** continuous mark-to-market.
- **PnL:** open position has unrealized PnL = size × (mark − entry) − funding accrued.
- **Quirks:** funding paid every hour; tracker emits `funding_paid` events. Liquidation possible — `kill_switch` checks margin ratio and force-closes at thresholds before the venue does.
- **Position model:** `OpenPosition` becomes `OpenPerpPosition` with extra fields (mark, margin, funding_accrued). The base type's `cost_usdc` is the initial margin.

### J.4 The accounting interface

Each venue ships:

```python
class Venue(ABC):
    def compute_pnl(self, position: OpenPosition, ev: MarketResolutionEvent | None) -> float: ...
    def compute_mtm(self, position: OpenPosition, mark: MarkEvent) -> float: ...  # perps only
    def position_class(self) -> type[OpenPosition]: ...
```

The outcome tracker calls `venue.compute_pnl()`; the framework doesn't know binary vs perp. New venue with weird accounting? Override `compute_pnl` and ship a `MarkEvent` if you need MtM.

---

## K. SQL schema when we graduate from JSONL

When the UI starts needing queries like "win rate by hour-of-day for bot X over last 30 days", we'll add a SQLite mirror. JSONL stays the durable source; SQLite is a derived index.

### K.1 Schema sketch

```sql
CREATE TABLE bots (
    name TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    config_json TEXT NOT NULL,
    first_seen_ts TEXT NOT NULL,
    last_seen_ts TEXT NOT NULL
);

CREATE TABLE fires (
    intent_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    bot TEXT NOT NULL REFERENCES bots(name),
    strategy TEXT NOT NULL,
    venue TEXT NOT NULL,
    market_id TEXT,
    outcome_name TEXT,
    side TEXT,
    order_type TEXT,
    size_usdc REAL,
    filled_size REAL,
    filled_price REAL,
    cost_usdc REAL,
    order_ok INTEGER,         -- 0/1
    dry_run INTEGER,
    debug_json TEXT
);
CREATE INDEX idx_fires_bot_ts ON fires(bot, ts);
CREATE INDEX idx_fires_market ON fires(market_id);

CREATE TABLE pnl (
    intent_id TEXT PRIMARY KEY REFERENCES fires(intent_id),
    ts TEXT NOT NULL,
    outcome_resolved TEXT,
    won INTEGER,
    payout_usdc REAL,
    pnl_usdc REAL,
    resolved_ts TEXT
);

CREATE TABLE skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    bot TEXT NOT NULL,
    reason TEXT NOT NULL,
    debug_json TEXT
);
CREATE INDEX idx_skips_bot_ts ON skips(bot, ts);
CREATE INDEX idx_skips_reason ON skips(reason);

CREATE TABLE errors (...);
CREATE TABLE positions_open (...);   -- mirror of open.jsonl, derived on demand
```

### K.2 Ingestion

A `scripts/sync_jsonl_to_sqlite.py` job runs every 5min, scanning JSONL files newer than the DB's last `max(ts)` and inserting rows. Idempotent on `intent_id` (insert-or-ignore).

### K.3 What stays in JSONL

The raw rows, forever. We can always rebuild the DB. We never query JSONL ad-hoc in production code paths; that's the DB's job.

### K.4 Why not Postgres v1?

Single host, single writer (the sync job). SQLite handles tens of GB and concurrent reads from the UI fine. Postgres only if we go multi-host.

---

## L. Metrics, health, observability

### L.1 Health endpoints (supervisor-level)

When the UI lands, supervisor exposes:

- `GET /healthz` — `200 OK` if all bots are running AND all data_sources have published an event within their stale window. Else `503` with a JSON body listing degraded components.
- `GET /readyz` — `200` once boot is complete (all bots subscribed, all venues authed).
- `GET /metrics` — Prometheus exposition format (counters, gauges).

### L.2 Prometheus metrics emitted

```
bot_events_total{bot, event}            # counter
bot_fires_total{bot, ok}                # counter
bot_skips_total{bot, reason}            # counter
bot_pnl_usdc{bot}                       # gauge (today's running total)
bot_open_positions{bot}                 # gauge
bot_last_event_age_seconds{bot}         # gauge — watchdog signal
source_events_total{source}             # counter
source_last_event_age_seconds{source}   # gauge
venue_orders_total{venue, ok}           # counter
venue_latency_ms{venue, op}             # histogram
venue_errors_total{venue, category}     # counter
supervisor_boot_ts                       # gauge (UNIX ts)
supervisor_bot_crashes_total{bot}        # counter
```

### L.3 Alerting

The metrics above feed a Prometheus + Alertmanager later. Initial alerts:

- `bot_last_event_age_seconds > 300` for any required source → "data source stale"
- `bot_pnl_usdc < halt_threshold` → "kill switch armed"
- `supervisor_bot_crashes_total > 5/hour` for any bot → "bot unstable"
- `venue_errors_total{category=auth} > 0/hour` → "auth broken"
- `bot_open_positions > expected` (config caps) → "kill switch missed"

### L.4 Pre-UI observability

Until metrics land, the operator's observability stack is:

- `docker compose ps`
- `tail -f logs/bot=*/$(date -u +%F).jsonl | jq`
- `scripts/today_pnl.sh` — one-liner that sums today's `pnl_usdc` per bot

This is sufficient for ≤10 bots. Past that, build the metrics layer (phase G').

---

## M. Worked examples of escape-hatch strategies

To make Part II concrete, three quick walkthroughs of strategies that use the escape hatches.

### M.1 Maker-rebate LPer (uses `async_loop`, `OrderGroup`, GTC orders, cancel/replace)

Goal: quote inside Polymarket's reward-eligible spread on hourly markets. Earn rebate × time-on-book.

```python
@register_strategy("polymarket_maker_rebate")
class PolymarketMakerRebate(Strategy):
    KIND = "async_loop"

    async def run(self, ctx):
        while not ctx.cancelled():
            ob = await ctx.fetch_orderbook(market_id=...)
            mid = (ob.best_bid + ob.best_ask) / 2
            edge = self._compute_edge(mid, ctx.state)
            quotes = OrderGroup(
                group_id=str(ctx.uuid4()),
                atomicity="best_effort",
                intents=(
                    OrderIntent(side="buy",  limit_price=mid-edge, order_type="gtc_limit", ttl_s=5, ...),
                    OrderIntent(side="sell", limit_price=mid+edge, order_type="gtc_limit", ttl_s=5, ...),
                ),
            )
            await ctx.place_group(quotes)
            await ctx.sleep(0.25)
```

Hatches used: `async_loop`, `OrderGroup(best_effort)`, GTC + TTL, cancel/replace cycle, per-tick re-quoting.

### M.2 Whale-copy (uses checkpoint state, REST data source, custom event type)

Goal: index Polymarket whale wallets, detect new positions, mirror them with slippage tolerance.

```python
@register_strategy("polymarket_whale_copy")
class PolymarketWhaleCopy(Strategy):
    KIND = "event_driven"

    def required_streams(self):
        return [StreamSpec("polygon_polymarket_indexer", whale_list_path="data/whales.txt")]

    def checkpoint(self, ctx) -> bytes:
        # 50KB of indexed wallet → last-position-seen mapping
        return pickle.dumps(ctx.state["whale_index"])

    def restore(self, ctx, blob: bytes):
        ctx.state["whale_index"] = pickle.loads(blob)

    def on_event(self, ev, ctx) -> list[OrderIntent]:
        # ev: WhalePositionEvent (custom type, registered by the indexer data_source)
        if not self._is_new_position(ev, ctx.state):
            return []
        return [OrderIntent(market_id=ev.market_id, side="buy", outcome_name=ev.outcome, ...)]
```

Hatches: `checkpoint/restore`, custom event type `WhalePositionEvent`, REST-based data source under `data_sources/polygon_polymarket_indexer.py`.

### M.3 BTC × ETH funding-rate divergence (uses scheduled, multi-venue, OrderGroup all_or_none)

Goal: every hour, check Hyperliquid funding rates for BTC and ETH; if BTC funding is much higher than ETH, short BTC perp + long ETH perp (size-matched).

```python
@register_strategy("perp_funding_divergence")
class PerpFundingDivergence(Strategy):
    KIND = "scheduled"
    SCHEDULE = "5 * * * *"     # 5 minutes after each hour (funding has settled)

    async def run_once(self, ctx) -> list[OrderGroup]:
        funding = await ctx.fetch("hyperliquid", "funding_rates")
        diff = funding["BTC"] - funding["ETH"]
        if abs(diff) < self.p.threshold_bps: return []
        notional = self.p.size_usdc
        side_btc, side_eth = ("sell", "buy") if diff > 0 else ("buy", "sell")
        return [OrderGroup(
            group_id=str(ctx.uuid4()),
            atomicity="all_or_none",
            timeout_s=10,
            intents=(
                OrderIntent(venue="hyperliquid", asset="BTC", side=side_btc,
                            order_type="market", size_usdc=notional, ...),
                OrderIntent(venue="hyperliquid", asset="ETH", side=side_eth,
                            order_type="market", size_usdc=notional, ...),
            ),
        )]
```

Hatches: `scheduled` kind, `OrderGroup(all_or_none)` with unwind, perp venue with MtM accounting.

---

## N. Anti-patterns we've seen tempt us

Patterns that look right but produce problems we've already paid for once.

| Anti-pattern | What it looks like | Why it bites | Right answer |
|---|---|---|---|
| **Strategy peeking at venue state mid-decision** | `if venue.balance > X: place(...)` inside `on_event` | Couples strategy to live state, breaks replay determinism, makes unit tests need a mock venue | Strategy works off `ctx.state`; balance limits enforced by `kill_switch`. |
| **Stuffing config into env vars** | Adding `THRESHOLD_PCT_ETH=...` to `.env` | `.env` becomes an unreadable god-config; configs lose audit trail | YAML in `configs/`. |
| **"Just one quick global" for cross-bot state** | `core/_shared_state.py` with module-level dicts | First request looks innocent; second one needs locking; third needs versioning. You've reinvented a DB in 100 lines. | Use `core/portfolio.py` with a defined interface (§H). |
| **One log line per orderbook snapshot in the bot log** | `event="orderbook_seen", bids=..., asks=...` per tick | 50 MB/day per bot of unreadable noise; jq queries crawl | Orderbook lives in `recorder=ws_*/`; bot log records only decisions. |
| **Sync HTTP from inside `on_event`** | `httpx.get(...)` in an event_driven strategy | Blocks the whole bot's event loop; one slow venue freezes 10 bots | Move I/O to the executor (`OrderIntent` flag) or use `async_loop` kind. |
| **A strategy that "needs" to know it's in dry-run** | `if ctx.dry_run: place(big_size) else: place(small)` | Live and dry-run paths diverge; backtest becomes unreliable | Strategy doesn't know. `dry_run` is purely an executor concern. |
| **Backtest-only branches** | `if ctx.replay: skip_check()` | Live and replay drift, parity test fails for "good reason" | Make the check work in both modes, or move it to the executor/fill_model. |
| **Catching exceptions in strategy code** | `try: ... except: pass` around state updates | Hides bugs; broken state survives invisibly | Let it crash. The supervisor restarts the bot. The crash row is the audit trail. |
| **One bot doing two strategies** | A switch in `on_event` based on event type | The two strategies have different optimal params, different risk caps, different teams of future-maintainers | Two bots, two configs, one stream subscription. |

---

## Quick context for AI agents resuming work

1. **Read `README.md` first** — what we ship today.
2. **Then Part I of this file** — what we're building toward + why decisions were made.
3. **Then Part II** when adding a strategy/venue that feels like a misfit — find the matching escape hatch in §A.2 before inventing one.
4. **Then `RESEARCH_2026.md`** — what to build next.
5. **Then `polymarket/DEPLOY_NOTES.md`** — how the live bot is wired today.
6. Check the **Migration plan (§16, Part I)** — find the current phase. Each phase ends in a stable shippable state; never push half-migrated code.
7. The live bot **must keep running** during any refactor. Use Phase B's parallel-logger pattern for any cross-cutting change.
8. When in doubt: **make the smallest change that's testable**. The repo is small; grand rewrites waste a week.
9. **Before adding a generic abstraction**, find two concrete strategies that need it. One isn't enough.
10. **If you find yourself bypassing the executor** or talking to a venue from a strategy, stop — you're in Part II §A or §N territory; pick the right hatch.
