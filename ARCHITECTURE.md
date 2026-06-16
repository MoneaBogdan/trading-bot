# Architecture & Roadmap

> **Audience:** future contributors, AI agents resuming work on this repo, and the operator.
> **Goal of this doc:** make it cheap to (a) understand what exists, (b) add new bots / strategies / markets, and (c) hand context off without re-deriving it.
>
> Read top-to-bottom on first visit. Later, jump via the TOC.

## Table of Contents

1. [North star](#1-north-star)
2. [What exists today](#2-what-exists-today)
3. [Pain points blocking expansion](#3-pain-points-blocking-expansion)
4. [Target architecture](#4-target-architecture)
5. [Logging & data architecture](#5-logging--data-architecture)
6. [Backtest framework — unified](#6-backtest-framework--unified)
7. [Extension recipes](#7-extension-recipes-how-to-add-x)
8. [Migration plan (non-breaking, phased)](#8-migration-plan-non-breaking-phased)
9. [Future UI dashboard](#9-future-ui-dashboard)
10. [Open design decisions](#10-open-design-decisions)
11. [Glossary](#11-glossary)
12. [Related docs](#12-related-docs)

---

## 1. North star

A **multi-venue, multi-strategy crypto trading platform** the operator can:

- run on a single VPS via docker-compose
- extend by dropping in a new strategy / market / venue with minimal scaffolding
- backtest any strategy × market combination against the same logged data
- monitor and tune from a web UI (future)

We are **not** building HFT or a market-making giant. The target operator is one technically literate person running 5–50 USD–10k positions across edge sources where bigger funds are absent or asleep.

Optimize for:
- **Clarity** > cleverness. Plain code beats meta-frameworks.
- **Plug-in extension** > inheritance hierarchies.
- **One log per event, ever.** No re-deriving truth from heterogeneous sources.
- **Backtest parity.** Same code paths replay against logged data — no separate "backtest engine" that drifts from live.

---

## 2. What exists today

### Top-level layout

```
trading-bot/
├── Dockerfile                  # single image, used by all containers
├── docker-compose.yml          # 6 trader variants + 1 ws-recorder (after Phase 1)
├── deploy.sh                   # cron-friendly auto-pull-and-rebuild script
├── README.md                   # user-facing setup & ops guide
├── RESEARCH_2026.md            # cited research + iterative follow-up log
├── ARCHITECTURE.md             # this file
├── polymarket/                 # live bot + Polymarket-specific replay backtest
├── backtest/                   # GENERIC bar-event backtest engine (forex/equity)
└── regime-classifier/          # standalone Claude-based regime classifier (not integrated)
```

### `polymarket/` — live bot + market-specific replay

```
polymarket/
├── binance_stream.py           # WS stream — Binance trades (asset-parameterized)
├── coinbase_stream.py          # WS stream — Coinbase matches (asset-parameterized)
├── gamma.py                    # Polymarket Gamma API client — market discovery
├── clob.py                     # Polymarket CLOB orderbook fetch
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
│   ├── replay_tick.py          # Replay live decisions over cached Polymarket + Binance data
│   ├── replay_trades.py        # Same idea, trade-level granularity
│   ├── replay.py               # Earlier minute-bar variant
│   ├── analyze_tick.py         # Stats on replay output
│   ├── historical_*.py         # Data fetchers for BTC 1s/1m candles + Polymarket markets
│   ├── stability.py            # Permutation tests
│   └── cache/                  # Cached historical data (not in git)
└── logs/                       # All runtime logs (not in git)
```

**Live decision pipeline (`live_trader.py`)**:

```
Binance WS ──┐
             ├──> MoveTracker (60s return) ──┐
Coinbase WS ─┘                               │
                                             ▼
                              gates: threshold → CB confirm → window-anchor
                                             │
Gamma poll (60s) ──> upcoming markets ───────┤
                                             ▼
                                _pick_market (snipe window)
                                             ▼
                                     CLOB orderbook fetch
                                             ▼
                                 sweet-band ask check
                                             ▼
                            trader.place_buy_fok ─> logs/live_*.jsonl
```

### `backtest/` — generic engine (forex/equity legacy)

```
backtest/
├── engine.py                   # Event-driven bar engine (entries on next-bar-open)
├── strategy.py                 # Strategy ABC + register() decorator + registry
├── btypes.py                   # Bar / Signal / Position / Trade / Side / StrategyState
├── data.py                     # On-disk candle cache; load_candles(pair, granularity, src)
├── binance.py / oanda.py / yahoo.py / dukascopy.py / dukascopy_src.py
│                               # Data source adapters
├── indicators.py               # ATR, ADX, BB, MACD, RSI, Donchian helpers
├── gate.py                     # StaticGate (regime filter)
├── risk.py                     # Position sizing + RiskConfig
├── metrics.py                  # Sharpe, profit factor, win rate, etc.
├── walk_forward.py             # OOS parameter tuning loop
├── monte_carlo.py              # Trade-shuffle CI
├── permutation.py              # / `significance.py` — null-hypothesis tests
├── pairs.py                    # Cointegration pair-trade helpers
├── markov.py                   # Markov state classifier (helper, not standalone)
├── run.py                      # CLI: pick strategy, pair, granularity, date range → report
├── strategies/                 # Plug-in strategies
│   ├── donchian.py             # ENABLED
│   ├── macd_trend.py           # ENABLED
│   ├── bb_volume_breakout.py   # ENABLED
│   ├── chronos_signal.py       # ENABLED
│   ├── coint_mean_rev.py       # ENABLED
│   ├── london_orb.py           # RETIRED (kept on disk)
│   └── rsi_pullback.py         # RETIRED (kept on disk)
└── data_cache/ + *.csv         # Cached parquet candles + exported trade tables
```

Designed for: bar-event-driven, single-asset, long/short with SL+TP, walk-forward tunable.
**Not used** by the live Polymarket bot — they evolved independently.

### `regime-classifier/` — standalone Claude classifier

```
regime-classifier/
├── main.py                     # Run one classification cycle → cache
├── classifier.py               # Calls Anthropic API with a structured prompt
├── preprocessor.py             # Build input (headlines, calendar, CB speeches)
├── schema.py                   # Output schema (regime tags + risk multipliers)
├── briefing.py                 # Human-readable briefing
├── cache.py                    # Persist results to disk
└── strategy_example.py         # Demo wiring into the generic engine
```

Standalone Python project. **Not currently wired** into either `backtest/` or `polymarket/`. Intended use: hourly cron, output consumed by strategies' `StaticGate`.

---

## 3. Pain points blocking expansion

| # | Pain | Symptom | Cost |
|---|---|---|---|
| 1 | **Two backtest engines, neither portable.** | `backtest/` is bar-event (forex). `polymarket/backtest/` is binary-payoff (replay). New venue → invent a third. | Adding Kalshi arb requires writing a new backtest from scratch. |
| 2 | **`polymarket/` is the namespace AND the venue.** | Adding Kalshi or Hyperliquid means either a sibling folder (`kalshi/`, `hyperliquid/`) or polluting `polymarket/`. | No clear pattern for venue-adapter placement. |
| 3 | **Streams + execution + strategy intermixed in `live_trader.py`.** | The trading rule, the market discovery, and the order placement are one async loop. | Can't unit-test the strategy; can't run two strategies sharing one stream. |
| 4 | **No config layer.** | Variants are hard-coded as env vars in `run_live.sh` + `docker-compose.yml`. Each new variant = new compose service. | Adding a 7th variant means editing two files in two places. |
| 5 | **Log schema is implicit.** | Each component writes its own JSONL with different fields. No central reader, no schema doc. | A UI cannot query "all fires from variant X in window Y" without bespoke parsing. |
| 6 | **No process-level orchestration.** | Each container runs one bot. Sharing a Binance stream across 3 strategies = 3 redundant subscriptions. | At 10 strategies the WS connection count alone becomes a problem. |
| 7 | **`regime-classifier/` orphan.** | Built but not wired. | Existing investment wasted; gate-style strategies in `backtest/` reference it implicitly. |
| 8 | **No registry of live bots.** | "What's running?" answered only by `docker ps`. No metadata, no health, no PnL summary. | A UI has no source of truth. |

---

## 4. Target architecture

### Top-level layout (proposed)

```
trading-bot/
├── Dockerfile
├── docker-compose.yml           # supervisor service + per-bot services (or one supervisor per pod)
├── deploy.sh
├── README.md
├── ARCHITECTURE.md
├── RESEARCH_2026.md
│
├── configs/                     # ← NEW: declarative bot configs (YAML)
│   ├── btc-5m.yaml
│   ├── eth-1h.yaml
│   ├── polymarket-mm.yaml
│   └── _shared.yaml
│
├── src/
│   ├── core/                    # framework-level code; no venue/strategy specifics
│   │   ├── bot.py               # Bot runtime: load config → wire streams → run strategy → log
│   │   ├── stream_bus.py        # In-process pub/sub so N strategies share 1 WS subscription
│   │   ├── logger.py            # Single structured-log entry point (JSONL + schema)
│   │   ├── config.py            # YAML loader + validation (pydantic)
│   │   ├── registry.py          # Registries for strategies / venues / data-sources
│   │   ├── timeframe.py         # Bar/window/snipe primitives
│   │   └── types.py             # Trade, Signal, Position, Market, Order — venue-agnostic
│   │
│   ├── data_sources/            # Read-only feeds (no order execution)
│   │   ├── binance_ws.py        # Price stream, asset-parameterized
│   │   ├── coinbase_ws.py
│   │   ├── tree_of_alpha_ws.py  # ← future: news feed for headline-based strategies
│   │   └── defillama_rest.py    # ← future: derivatives funding/OI poller
│   │
│   ├── venues/                  # Read+write venue adapters (orders, positions, fees)
│   │   ├── polymarket/
│   │   │   ├── gamma.py         # market discovery
│   │   │   ├── clob.py          # orderbook + order placement
│   │   │   └── ws_orderbook.py  # L2 recorder (still useful for fill verification)
│   │   ├── kalshi/              # ← future
│   │   └── hyperliquid/         # ← future
│   │
│   ├── strategies/              # Pure logic: input = events, output = orders
│   │   ├── polymarket_latency_arb.py     # the current BTC/ETH/SOL × 5m/1h bot
│   │   ├── polymarket_maker_rebate.py    # ← Phase 2
│   │   ├── polymarket_kalshi_arb.py      # ← Phase 3
│   │   ├── news_headline_bucket.py       # ← Tree of Alpha play
│   │   ├── funding_divergence.py         # ← DefiLlama play
│   │   └── whale_copy.py                 # ← self-indexed Polymarket whale tracker
│   │
│   ├── backtest/                # Unified backtester — replays any strategy against logs
│   │   ├── replayer.py          # Drives a strategy with logged events
│   │   ├── fill_model.py        # Simulates fills against logged orderbook depth
│   │   ├── outcomes.py          # Resolves wins/losses from logged market closes
│   │   └── reports.py           # Stats + plots
│   │
│   └── ui/                      # ← future: FastAPI + simple React/HTMX dashboard
│       ├── api/                 # /bots, /logs, /pnl, /backtest endpoints
│       └── web/                 # static frontend
│
├── scripts/                     # one-off tooling (deploy, log rotation, key setup)
│   ├── deploy.sh                # (moved from root)
│   ├── setup_wallet.py
│   ├── setup_allowances.py
│   └── rotate_logs.sh
│
├── data/                        # ← single root for cached historical data
│   ├── binance_1s/              # parquet by asset
│   ├── polymarket_markets/      # JSON by date range
│   └── orderbook_replays/       # subsampled WS recordings used for backtests
│
├── logs/                        # ← single root for all runtime logs
│   ├── bot=<name>/              # per-bot subfolder
│   │   └── YYYY-MM-DD.jsonl     # one JSONL per UTC day
│   └── recorder=ws/             # WS recorder logs
│
└── tests/
    ├── unit/                    # strategies, core, fill_model
    └── integration/             # full-loop replay
```

### Core interfaces (proposed)

```python
# src/core/types.py
@dataclass
class PriceEvent: ts: datetime; asset: str; price: float; venue: str
@dataclass
class HeadlineEvent: ts: datetime; source: str; text: str; tags: list[str]
@dataclass
class OrderbookEvent: ts: datetime; market_id: str; bids: list; asks: list
# ... etc — all events carry ts + a discriminator field

# src/core/registry.py — every venue/strategy/source registers a factory
@register_strategy("polymarket_latency_arb")
class PolymarketLatencyArb(Strategy):
    def required_streams(self) -> list[StreamSpec]: ...   # what events I subscribe to
    def required_venues(self) -> list[str]: ...           # what venues I need order access to
    def on_event(self, ev: Event, ctx: BotContext) -> list[OrderIntent]: ...

# src/core/bot.py
class Bot:
    """One configured strategy. Loaded from a YAML config.
    The supervisor process can run N bots in one Python process via stream_bus."""
    def __init__(self, cfg: BotConfig): ...
    async def run(self): ...
```

### Example config: `configs/btc-5m.yaml`

```yaml
name: btc-5m
strategy: polymarket_latency_arb
asset: BTC
timeframe_min: 5
streams:
  - source: binance_ws
    asset: BTC
  - source: coinbase_ws
    asset: BTC
venues:
  - polymarket
params:
  threshold_pct: 0.10
  sweet_lo: 0.30
  sweet_hi: 0.40
  cooldown_s: 60
  snipe_window_s: 300
  require_confirm: true
  require_window_anchor: true
execution:
  dry_run: true
  max_order_usdc: 5
  max_daily_usdc: 20
logging:
  level: info
```

**Adding a new variant** = drop a new YAML in `configs/`. **Adding a new strategy** = drop a new file in `src/strategies/` with `@register_strategy(...)`. Compose just runs `supervisor --config configs/*.yaml`.

---

## 5. Logging & data architecture

**Principle:** every event the system observes or emits lands in exactly one structured JSONL file, with a stable schema.

### Log schema (proposed; v1)

```json
{
  "ts": "2026-06-15T16:43:50.375Z",
  "bot": "btc-5m",
  "event": "fire",            // or "skip" / "fill" / "stream_status" / "market_resolution"
  "reason": null,             // populated for skips: "ask_outside_band" / "cb_confirm_fail" / ...
  "asset": "BTC",
  "venue": "polymarket",
  "market_id": "0x5649...",
  "direction": "Down",
  "size_usdc": 5.0,
  "ask": 0.39,
  "price_ref": 67161.88,      // underlying price at decision time
  "extra": { ... }            // strategy-specific debug payload
}
```

- One file per bot per UTC day: `logs/bot=btc-5m/2026-06-15.jsonl`
- Recorders use their own discriminator path: `logs/recorder=ws_polymarket/2026-06-15.jsonl`
- A `LogReader` library (in `src/core/logger.py`) parses these into pandas DataFrames for backtesting and the future UI.

**Backward compatibility:** the existing `polymarket/logs/live_*.jsonl` files are *not* re-formatted. The new logger writes side-by-side at the new paths from cutover day. A small migration script can ingest legacy logs into the new schema if the UI needs historical depth.

### Data architecture

| Data | Source | Cache | Producer | Consumer |
|---|---|---|---|---|
| Live price ticks | Binance WS / Coinbase WS | none (transient) | `data_sources/*.py` | live strategies via `stream_bus` |
| Polymarket orderbook | Polymarket WS | `logs/recorder=ws/*.jsonl` (durable) | `venues/polymarket/ws_orderbook.py` | backtest fill model |
| Historical candles | REST | `data/binance_1s/` (parquet) | `scripts/fetch_*.py` | backtest replayer |
| Historical Polymarket markets | Gamma REST | `data/polymarket_markets/` (json) | `scripts/fetch_polymarket_history.py` | backtest replayer |
| Newsflow | Tree of Alpha WS | `logs/recorder=news/*.jsonl` (durable) | future `data_sources/tree_of_alpha_ws.py` | news-headline strategies |
| Whale fills | Polygon node | `logs/recorder=whale/*.jsonl` (durable) | future `recorders/whale_indexer.py` | whale-copy strategy |

---

## 6. Backtest framework — unified

### Principle: backtest replays *the same strategy code* against logged events.

```python
# src/backtest/replayer.py (sketch)
def replay(strategy_name: str, cfg: BotConfig,
           start: datetime, end: datetime,
           data: ReplayDataset) -> ReplayResult:
    strategy = get_strategy(strategy_name)(**cfg.params)
    fill_model = make_fill_model(data.orderbook)
    pnl = 0.0
    for ev in data.events(start, end):                  # iterates logged events
        intents = strategy.on_event(ev, ctx)
        for intent in intents:
            fill = fill_model.simulate(intent, ev.ts)   # uses logged orderbook
            outcome = data.resolve_market(fill.market_id)  # uses logged close
            pnl += pnl_from(fill, outcome)
    return ReplayResult(pnl=pnl, trades=..., ...)
```

The same `strategy.on_event` is called in live mode by `core/bot.py` and in backtest mode by `replayer.py`. Drift between the two is impossible by construction.

### Datasets

A `ReplayDataset` is a thin wrapper over:
- Logged price events (from recorders, or REST-fetched candles if recordings are short)
- Logged orderbook snapshots (from `recorder=ws`)
- Logged market resolutions (from Polymarket history fetch + actual outcomes)

**Reuse of existing assets:**
- `polymarket/backtest/historical_*.py` → moves to `scripts/fetch_*.py`, populates `data/`
- `backtest/engine.py` → its bar-iteration logic informs `replayer.py` for time-series strategies that don't use orderbook snapshots
- `backtest/strategies/` → migrate to `src/strategies/`. The `Strategy.on_bar(bar)` interface generalizes to `Strategy.on_event(ev)` where `ev` happens to be a bar.

---

## 7. Extension recipes (how to add X)

### Add a new market variant (e.g. SOL hourly already done)
1. Drop `configs/sol-1h.yaml` — same shape as `configs/btc-5m.yaml`.
2. Set `asset: SOL`, `timeframe_min: 60`, threshold per the per-asset table in `RESEARCH_2026.md`.
3. Restart the supervisor — picks up new configs automatically.

### Add a new strategy
1. Create `src/strategies/<my_strategy>.py`.
2. Subclass `Strategy`, declare `required_streams()` and `required_venues()`.
3. Decorate with `@register_strategy("<my_strategy>")`.
4. Write a config under `configs/`.
5. Add a unit test in `tests/unit/strategies/test_<my_strategy>.py` driving it with a fake event sequence.
6. (Optional but recommended) Run a backtest via `scripts/backtest.py --config configs/<my_strategy>.yaml --from 2026-01-01 --to 2026-06-01`.

### Add a new venue (e.g. Kalshi for cross-venue arb)
1. Create `src/venues/kalshi/`.
2. Implement the small `Venue` ABC: `place_order`, `fetch_orderbook`, `discover_markets`, `fetch_balance`, `fetch_position`.
3. Add API creds to `.env.example`.
4. A strategy needing Kalshi declares `required_venues=["polymarket", "kalshi"]`; the bot loader instantiates both clients and passes them in `BotContext`.

### Add a new data source (e.g. Tree of Alpha news)
1. Create `src/data_sources/<source>_ws.py` (or `_rest.py`).
2. Emit normalized events through `stream_bus`.
3. Strategies subscribe via `required_streams()`.

### Retire a strategy
1. Move file to `src/strategies/_retired/`.
2. Comment in `src/strategies/__init__.py` about why (see `backtest/strategies/__init__.py` for the existing pattern — `london_orb`, `rsi_pullback` retired with notes).

---

## 8. Migration plan (non-breaking, phased)

The live bot must keep running. Each phase is a non-breaking commit; nothing is removed until the new path is proven.

### Phase A — Document & freeze interfaces (this commit)
- `ARCHITECTURE.md` (this file). No code change.
- No risk; pure documentation.

### Phase B — `src/core/` skeleton + parallel new logger
- Create `src/core/` with `types.py`, `logger.py`, `config.py`, `registry.py`.
- Wire `logger.py` into a single new bot (e.g. `eth-5m`) as a parallel logger — keeps writing the legacy `live_*.jsonl` AND the new `logs/bot=eth-5m/*.jsonl`.
- After 1 week, validate parity → switch eth-5m to log-new-only.

### Phase C — Strategy extraction
- Extract the Polymarket latency-arb decision tree from `polymarket/live_trader.py` into `src/strategies/polymarket_latency_arb.py`, with the on_event interface.
- `polymarket/live_trader.py` becomes a thin shim that imports it (for backward compat with `run_live.sh`).
- All variants now share one strategy implementation; configs drive differences.

### Phase D — Stream bus + supervisor
- Implement `src/core/stream_bus.py` (in-process pub/sub).
- Implement `src/core/bot.py` and a `supervisor.py` that loads N configs and runs them in one process.
- Update `docker-compose.yml` from 6 trader services → 1 supervisor service.
- Existing per-variant containers can stay until supervisor proves stable, then be removed.

### Phase E — Unified backtester
- Implement `src/backtest/replayer.py` using extracted strategies.
- Verify it reproduces last month's live PnL within 1% of actual.
- `polymarket/backtest/replay_tick.py` becomes deprecated (kept in `_retired/`).

### Phase F — New venues
- Add `src/venues/kalshi/` (Phase 3 from `RESEARCH_2026.md`).
- Add `src/venues/hyperliquid/` if/when Phase 4 ships.

### Phase G — UI (see §9)

**Rule:** no phase removes a working code path until the replacement has run live for ≥1 week.

---

## 9. Future UI dashboard

### Minimum viable UI
- Single FastAPI service in `src/ui/api/`, static frontend in `src/ui/web/`.
- Endpoints:
  - `GET /bots` — list of configured bots + live status (PID, last event ts, error)
  - `GET /bots/{name}/log?since=...` — tail the bot's JSONL
  - `GET /bots/{name}/pnl?from=...&to=...` — realized + unrealized PnL summary
  - `POST /backtest` — body: `{strategy, config, from, to}` → returns a result id
  - `GET /backtest/{id}` — status + summary
  - `GET /backtest/{id}/trades.csv` — raw trades for plotting
- Frontend: a single-page React or HTMX app — three tabs (Bots / Logs / Backtest).
- Auth: HTTP basic + Tailscale-only network exposure. No public auth surface in v1.

### Why "future" not "now"
- The architecture above is the gating dependency. Building UI atop the current logger schema and `docker ps` is throwaway work.
- After Phase D the data shape stabilizes; UI becomes a 1-week build.

---

## 10. Open design decisions

| # | Decision | Options | Current lean |
|---|---|---|---|
| 1 | Process model | one container per bot vs one supervisor process running N bots | **Supervisor** — saves WS connections; isolation via crash-restart of the supervisor |
| 2 | Config format | YAML vs TOML vs Python | **YAML** — best ergonomics for ops; pydantic validation |
| 3 | Log storage | JSONL files vs SQLite vs Postgres | **JSONL files** v1; **SQLite** when UI needs queries; **Postgres** only if multi-host |
| 4 | Stream bus | in-process pub/sub vs Redis vs NATS | **In-process** v1 (single host); revisit when scaling out |
| 5 | UI auth | none (LAN only) vs basic vs OAuth | **Basic + Tailscale** v1 |
| 6 | Backtest dataset format | parquet vs JSONL vs duckdb | **Parquet for candles, JSONL for events, duckdb for ad-hoc** |
| 7 | Legacy `backtest/` (forex engine) | keep / merge / retire | **Keep** — it's working code; the unified replayer will *consume* its `Strategy` ABC pattern rather than replace it. The forex strategies can later port to `src/strategies/` if cryptos call for similar patterns. |
| 8 | `regime-classifier/` integration | wire into supervisor as a data_source / leave standalone / retire | **Wire as a `data_source`** — emit a regime event hourly; strategies subscribe via `required_streams` |

---

## 11. Glossary

- **Bot** — one configured strategy instance, identified by its config file name (e.g. `btc-5m`). Multiple bots can share data sources.
- **Strategy** — pure logic that consumes events and emits order intents. No I/O. Registered via `@register_strategy`.
- **Venue** — anywhere we can place orders (Polymarket, Kalshi, Hyperliquid). Owns auth + order endpoints.
- **Data source** — read-only feed (price WS, news WS, on-chain indexer). Emits events into the stream bus.
- **Stream bus** — in-process pub/sub so N bots can share 1 WS subscription.
- **Supervisor** — single process that loads N bot configs, instantiates shared data sources / venues, runs the bots.
- **Replayer** — backtester that drives the *same* strategy code with logged events instead of live ones.
- **Variant** — historically used for "this same strategy with a different asset/timeframe config". After migration this is just "another bot config".
- **Fire** — a bot's decision to place an order (live or dry).
- **Skip** — a decision *not* to place an order, with a recorded reason.
- **Sweet band** — the Polymarket ask price range where the latency-arb edge is +EV (currently `[0.30, 0.40]`).

---

## 12. Related docs

- `RESEARCH_2026.md` — strategy research + iterative follow-up log. Read this to know *what* to build.
- `polymarket/DEPLOY_NOTES.md` — current deploy reference (env vars, log paths, redeploy steps).
- `README.md` — user-facing setup guide.
- `polymarket/HOSTING.md` — VPS sizing notes.

---

## Quick context for AI agents resuming work

If you (an agent) are picking up this repo cold:

1. **Read `README.md` first** — what we ship today.
2. **Then this file** — what we're building toward + why decisions were made.
3. **Then `RESEARCH_2026.md`** — what to build next (Tree of Alpha integration, maker rebates, Kalshi arb).
4. **Then `polymarket/DEPLOY_NOTES.md`** — how the live bot is wired today.
5. Check the **Migration plan (§8)** — find the current phase. Each phase ends in a stable shippable state; never push half-migrated code.
6. The live bot **must keep running** during any refactor. Use Phase B's parallel-logger pattern for any cross-cutting change.

When in doubt: **make the smallest change that's testable**. The repo is small enough that grand rewrites usually waste a week.
