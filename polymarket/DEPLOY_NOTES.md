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

Two long-lived processes — both supervised by docker-compose (`restart: unless-stopped`):

1. `polymarket/run_live.sh` → `live_trader.py` (auto-restart wrapper, 10s backoff).
2. `polymarket/run_ws_recorder.sh` → `orderbook_recorder_ws.py` (same wrapper pattern). Polymarket WS recorder, used to retroactively validate fills and feed backtests.

## Logs — where everything lands

All log paths are inside the container at `/app/polymarket/logs/`, mounted to the host at `<repo>/polymarket/logs/` via `docker-compose.yml`. After deploy, read them directly from the host — no `docker cp` needed.

| File | Producer | Content |
|---|---|---|
| `logs/live_YYYYMMDD.log` | `run_live.sh` (tee) | Trader stdout/stderr: wrapper messages, startup banner, strategy prints, errors |
| `logs/live_YYYYMMDD.jsonl` | `live_trader.py --log` | Structured record: every signal evaluated, every fire, every fill |
| `logs/orderbook_ws_YYYYMMDD.log` | `run_ws_recorder.sh` (tee) | WS recorder stdout/stderr: reconnects, throughput heartbeats, errors |
| `logs/orderbook_ws_YYYYMMDD.jsonl` | `orderbook_recorder_ws.py` | Polymarket L2 orderbook snapshots (one event per line) |

All four rotate by UTC date — the wrappers re-evaluate `DATE` on each restart loop. Docker's own journal also captures stdout (`docker logs polymarket-trader`, `docker logs polymarket-ws-recorder`) as a redundant copy.

Quick checks after deploy:

```bash
ls -lah polymarket/logs/                                    # files growing?
tail -f polymarket/logs/live_$(date -u +%Y%m%d).log         # trader live
tail -f polymarket/logs/orderbook_ws_$(date -u +%Y%m%d).log # ws-recorder live
wc -l polymarket/logs/live_$(date -u +%Y%m%d).jsonl         # fires today
docker compose ps                                           # both services Up
```

## Deploy checklist

1. Provision VPS, install Docker + docker-compose.
2. `git clone https://github.com/MoneaBogdan/trading-bot && cd trading-bot`
3. Copy `.env.example` → `.env`, fill `POLY_PRIVATE_KEY` + `POLY_FUNDER_ADDRESS`.
4. `docker-compose up -d` — auto-pull cron handles updates after that.
5. Tail `polymarket/logs/live_*.log` to confirm startup; `pgrep -fl live_trader.py` should show one PID.
6. Flip `POLY_DRY_RUN=false` in `.env` only after a dry-run day looks clean.
