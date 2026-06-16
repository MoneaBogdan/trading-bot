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
| `SWEET_LO` | `0.30` | Lower edge of Polymarket price band. |
| `SWEET_HI` | `0.40` | Upper edge — tightened from 0.45 on 2026-06-08 (live win-rate split). |
| `SNIPE_WINDOW_S` | `300` | Max secs-to-close at fire time. 300 disables the tight snipe — anchor alone is the EV-max. |

## Required secrets (NOT in repo)

Set in `.env` next to `docker-compose.yml`:

- `POLY_PRIVATE_KEY` — wallet private key
- `POLY_FUNDER_ADDRESS` — funder/proxy address

See `.env.example` for the full list.

## What runs together

Seven long-lived processes — all supervised by docker-compose (`restart: unless-stopped`):

**Six trader variants** (one per asset × timeframe — all share one image, differ only in env vars):

| Service | Asset | Window | Threshold | Confirm | Notes |
|---|---|---|---|---|---|
| `trader-btc-5m` | BTC | 5 min | 0.10% | yes | The original — Chainlink-aggregate resolution |
| `trader-eth-5m` | ETH | 5 min | 0.13% | yes | Higher 60s vol → wider threshold |
| `trader-sol-5m` | SOL | 5 min | 0.20% | yes | Highest 60s vol |
| `trader-btc-1h` | BTC | 60 min | 0.10% | **no** | Binance-only resolution — Coinbase confirm dropped |
| `trader-eth-1h` | ETH | 60 min | 0.13% | **no** | Binance-only resolution |
| `trader-sol-1h` | SOL | 60 min | 0.20% | **no** | Binance-only resolution |

Each writes its own log files (see below). All run dry-run by default.

**One shared WS recorder**: `polymarket-ws-recorder` runs `run_ws_recorder.sh` → `orderbook_recorder_ws.py`. Captures Polymarket L2 orderbook for retroactive fill validation and backtests. One recorder covers all variants — no need to duplicate.

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
- `logs/live_btc-1h_20260616.jsonl` — new BTC hourly variant
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
docker compose ps                                                    # all 7 services Up?
for v in '' eth-5m_ sol-5m_ btc-1h_ eth-1h_ sol-1h_; do
  f="polymarket/logs/live_${v}$(date -u +%Y%m%d).jsonl"
  [ -f "$f" ] && echo "$f: $(wc -l < "$f") fires"
done
tail -f polymarket/logs/live_eth-5m_$(date -u +%Y%m%d).log          # watch any variant live
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
docker compose up -d           # starts all 7 containers
docker compose ps              # verify 7 services Up
```

Existing `polymarket/logs/live_<date>.{log,jsonl}` files are on the bind mount — they survive the rebuild untouched. The new BTC-5m container will continue appending to the same `live_<date>.{log,jsonl}` filenames (legacy name preserved). New variants will get their own tagged files.
