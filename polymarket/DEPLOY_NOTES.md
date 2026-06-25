# Deploy Notes

Snapshot of where the bot was left and the exact config used in the last live (dry-run) session, captured 2026-06-14 from prior conversation.

## Current state

- Repo: public at `MoneaBogdan/trading-bot`, master at `fc1733a`.
- All processes stopped locally — paused until moved to a server.
- Docker + auto-deploy is wired (`deploy.sh`, `docker-compose.yml`, cron polls git and rebuilds on new commits — see commits `19d0214`, `39a4aa2`).

## Last run command (dry-run on laptop, Jun 9–10)

```bash
cd polymarket
POLY_DRY_RUN=true REQUIRE_CONFIRM=1 REQUIRE_WINDOW_ANCHOR=1 ./run_live.sh
```

Behavior observed: tighter sweet band `[0.30, 0.40]` plus anchor gate correctly rejected most signals (market prices clustered 0.81–0.98, already priced in). Anchor-only config (no snipe-window) was chosen as EV-max per 2026-06-09 backtest: n=63, 78% win, +$26.85 — beats `snipe=90` (n=14, 86% win, +$7).

## Effective parameters

Defaults live in `run_live.sh`; override via env vars. Values below are the ones the last session ran with:

| Var | Value | Meaning |
|---|---|---|
| `POLY_DRY_RUN` | `true` | No real orders. Set `false` only when live. |
| `REQUIRE_CONFIRM` | `1` | Require Coinbase 60s return to agree in sign with Binance — filters single-exchange noise. |
| `REQUIRE_WINDOW_ANCHOR` | `1` | Require BTC-now vs window-open return to agree with 60s return. |
| `THRESHOLD` | `0.10` | Min % move (60s) to consider a signal. |
| `COOLDOWN` | `60` | Seconds between fires. |
| `SWEET_LO` | `0.60` | Lower edge of Polymarket price band. |
| `SWEET_HI` | `0.75` | Upper edge. Widened from `[0.30, 0.40]` on 2026-06-17 after the 22.5h sweet-band counterfactual backtest showed the [0.60, 0.75] bucket carries the strongest per-fire edge (see `reports/STRATEGY_FINDINGS.md`). |
| `SNIPE_WINDOW_S` | `300` | Max secs-to-close at fire time. 300 disables the tight snipe — anchor alone is the EV-max. |

## Required secrets (NOT in repo)

In `polymarket/.env`:

- `POLY_PRIVATE_KEY` — wallet private key
- `POLY_FUNDER_ADDRESS` — funder/proxy address

In the root `.env` (next to `docker-compose.yml`) — required by the control-center sidecar:

- `CONTROL_CENTER_USER` — Basic-auth username (default `admin`)
- `CONTROL_CENTER_PASSWORD` — **must set**; the default `change-me` is publicly known
- `CONTROL_CENTER_BIND` — interface to bind (default `127.0.0.1`; leave on localhost and tunnel via SSH)
- `CONTROL_CENTER_SYNC_INTERVAL_S` — log→SQLite resync cadence (default `30`)

See `.env.example` for the full list.

## What runs together

Twelve long-lived processes — all supervised by docker-compose (`restart: unless-stopped`):

**Ten trader variants** (one per asset × timeframe — all share one image, differ only in env vars):

| Service | Asset | Window | Threshold | Confirm | Notes |
|---|---|---|---|---|---|
| `trader-btc-5m` | BTC | 5 min | 0.10% | yes | The original — Chainlink-aggregate resolution |
| `trader-eth-5m` | ETH | 5 min | 0.13% | yes | Higher 60s vol → wider threshold |
| `trader-sol-5m` | SOL | 5 min | 0.20% | yes | Highest 60s vol |
| `trader-eth-5m-wide` | ETH | 5 min | 0.13% | yes | Tail-bucket probe — sweet band `[0.75, 0.90]` (A/B vs main bots on `[0.60, 0.75]`) |
| `trader-btc-15m` | BTC | 15 min | 0.10% | yes | Mirrors wallet 0x8dxd's 15-min universe |
| `trader-eth-15m` | ETH | 15 min | 0.13% | yes | 15-min variant |
| `trader-sol-15m` | SOL | 15 min | 0.20% | yes | 15-min variant |
| `trader-btc-1h` | BTC | 60 min | 0.10% | **no** | Binance-only resolution — Coinbase confirm dropped |
| `trader-eth-1h` | ETH | 60 min | 0.13% | **no** | Binance-only resolution |
| `trader-sol-1h` | SOL | 60 min | 0.20% | **no** | Binance-only resolution |

Each writes its own log files (see below). All run dry-run by default.

**One shared WS recorder**: `polymarket-ws-recorder` runs `run_ws_recorder.sh` → `orderbook_recorder_ws.py`. Captures Polymarket L2 orderbook for retroactive fill validation and backtests. One recorder covers all variants — no need to duplicate.

**One funding monitor**: `hyperliquid-monitor` polls HL/Binance/Bybit/Drift/Paradex and logs cross-venue funding-rate opportunities to `hyperliquid/logs/`. Public REST only — no secrets, no execution.

**One observability sidecar**: `control-center` exposes an auth'd read-only web UI on `127.0.0.1:8080` that indexes all bot + funding JSONL into SQLite. Logs are mounted read-only; no trading credentials. SSH-tunnel for remote access: `ssh -L 8080:127.0.0.1:8080 root@<host>`.

## Logs — where everything lands

All log paths are inside the container at `/app/polymarket/logs/`, mounted to the host at `<repo>/polymarket/logs/` via `docker-compose.yml`. After deploy, read them directly from the host — no `docker cp` needed.

**Variant-tagged naming** so multiple traders don't collide:

| Variant | Log files |
|---|---|
| BTC 5-min (legacy — keeps original name) | `live_YYYYMMDD.{log,jsonl}` |
| Any other variant | `live_<asset>-<tf>m_YYYYMMDD.{log,jsonl}` |

Examples:
- `logs/live_20260615.log` / `.jsonl` — the original BTC-5m bot (preserves historical logs)
- `logs/live_eth-5m_20260616.jsonl` — new ETH 5-min variant
- `logs/live_eth-5m-wide_20260616.jsonl` — Stage A wide-band ETH 5-min variant (VARIANT_SUFFIX=wide)
- `logs/live_btc-60m_20260616.jsonl` — new BTC hourly variant (variant tag uses minutes, not "1h")
- `logs/bot=btc-60m/2026-06-16.jsonl` — new-schema per-bot log (Phase B parallel logger)
- `logs/bot=eth-5m-wide/2026-06-16.jsonl` — Stage A new-schema log
- `logs/orderbook_ws_20260616.{log,jsonl}` — shared WS recorder

| File | Producer | Content |
|---|---|---|
| `live_*.log` | `run_live.sh` (tee) | Trader stdout: wrapper messages, startup banner, decisions, errors |
| `live_*.jsonl` | `live_trader.py --log` | Structured record: every fire + fill. Includes `asset` and `timeframe_min` fields. |
| `orderbook_ws_*.log` | `run_ws_recorder.sh` (tee) | WS stdout: reconnects, throughput heartbeats |
| `orderbook_ws_*.jsonl` | `orderbook_recorder_ws.py` | Polymarket L2 orderbook snapshots (one event per line) |

The `.jsonl` files rotate by UTC date (Python computes the date per write). The `.log` files capture date at wrapper-restart time — known cosmetic limitation, content is correct. Docker's journal also captures stdout (`docker logs polymarket-trader-<variant>`) as a redundant copy.

**Old logs are preserved.** Historical `live_<date>.{log,jsonl}` files for BTC-5m remain on the server's bind mount across rebuilds. New variants get new tagged filenames; nothing is overwritten or renamed.

Quick checks after deploy:

```bash
ls -lah polymarket/logs/                                            # all variants growing?
docker compose ps                                                    # all 12 services Up?
for v in '' eth-5m_ eth-5m-wide_ sol-5m_ btc-60m_ eth-60m_ sol-60m_; do
  f="polymarket/logs/live_${v}$(date -u +%Y%m%d).jsonl"
  [ -f "$f" ] && echo "$f: $(wc -l < "$f") fires"
done
tail -f polymarket/logs/live_eth-5m_$(date -u +%Y%m%d).log          # watch any variant live

# Phase B new-schema check: every variant should have a boot event today
for v in btc-5m eth-5m eth-5m-wide sol-5m btc-60m eth-60m sol-60m; do
  f="polymarket/logs/bot=$v/$(date -u +%F).jsonl"
  echo -n "$v: "
  [ -f "$f" ] && head -1 "$f" | python3 -c "import json,sys;print(json.load(sys.stdin)['event'])" || echo "NO FILE"
done
```

## Deploy checklist (fresh server)

1. Provision VPS, install Docker + docker-compose.
2. `git clone https://github.com/MoneaBogdan/trading-bot && cd trading-bot`
3. Copy `polymarket/.env.example` → `polymarket/.env`, fill `POLY_PRIVATE_KEY` + `POLY_FUNDER_ADDRESS`.
4. `docker compose up -d` — auto-pull cron handles updates after that. **Builds 7 containers**: 6 traders + 1 WS recorder.
5. `docker compose ps` to confirm all seven Up. Tail any variant's log.
6. Flip `POLY_DRY_RUN=false` in `.env` only after each variant's dry-run looks clean — recommend doing this one variant at a time, not all six at once.

## Redeploying over the existing deployment (preserves all logs)

If you already have the legacy single-trader deployment running on `/mnt/data/trading-bot`:

```bash
cd /mnt/data/trading-bot
git pull                       # pulls multi-variant code
docker compose down            # stops the old single trader + recorder
docker compose build           # rebuild the shared image
docker compose up -d           # starts all 12 containers
docker compose ps              # verify 12 services Up
```

Existing `polymarket/logs/live_<date>.{log,jsonl}` files are on the bind mount — they survive the rebuild untouched. The new BTC-5m container will continue appending to the same `live_<date>.{log,jsonl}` filenames (legacy name preserved). New variants will get their own tagged files.

## 2026-06-18 — 15m variants + WS cache

### New 15-min trader services

Three new services mirror `0x8dxd`'s 15-min universe: `trader-btc-15m`, `trader-eth-15m`, `trader-sol-15m`. Same image, same `env_file`, same thresholds as the 5m variants. Sweet band `[0.60, 0.75]`. Deploy:

```bash
cd ~/trading-bot
git pull
docker compose build
docker compose up -d trader-btc-15m trader-eth-15m trader-sol-15m
docker compose ps
```

Logs land at `polymarket/logs/live_btc-15m_YYYYMMDD.jsonl` (and `eth-15m`, `sol-15m`). Phase B new-schema: `polymarket/logs/bot=btc-15m/YYYY-MM-DD.jsonl`.

### WS orderbook cache feature flag

All trader services in `docker-compose.yml` now read `POLY_BOOK_WS_CACHE` from the shell (default `false`). When enabled the bot subscribes to the Polymarket CLOB WS for active-window markets and serves top-of-book from cache; on cache miss or staleness >5s it falls back to HTTPS.

Enable on one variant only for shadow comparison:

```bash
POLY_BOOK_WS_CACHE=true docker compose up -d trader-eth-5m
```

Compare the `book_source` field in the JSONL (`ws_cache` vs `https`) for a few hours before flipping production-wide.

### Latency telemetry

Every fire event now carries `lat_ms_decide`, `lat_ms_book`, `lat_ms_order`. Use `lat_ms_book` to verify the WS cache is reducing book-fetch latency — expect sub-ms on cache hits vs 50–150ms on HTTPS.

### `place_buy_fok` non-blocking

The order POST now runs in a thread executor so the WS price stream doesn't stall on order submission. No deploy action required — automatically active on rebuild.

