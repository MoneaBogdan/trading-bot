# Polymarket BTC Up/Down latency-arb bot

A containerized trading bot for Polymarket's 5-minute BTC Up/Down prediction
markets. Streams Binance BTC trades, requires a Coinbase cross-confirmation,
and lifts the marketable ask on the matching token when the 60-second return
crosses a threshold and the entry sits in the validated sweet-spot price band.

> **Status:** dry-run by default. The bot will not place real orders until
> `POLY_DRY_RUN=false` in `polymarket/.env`. See **Going live** below.

## What's in the box

| Service | What it does |
|---|---|
| `trader` | Live decision loop: Binance WS → 60s rolling return → Coinbase confirm → Polymarket fill |
| `ws-recorder` | Logs full Polymarket L2 orderbook events for post-hoc fill validation and backtest alignment |

Both run from the same image, restart unless stopped, and share `polymarket/logs/` on the host.

## Prerequisites

- A Linux host with Docker Engine 24+ and the `docker compose` plugin (Proxmox LXC works — see _Proxmox notes_ below).
- A Polygon wallet holding USDC.e (≈$20-50) and a small amount of MATIC for gas.
- The wallet's private key (hex, with or without `0x`).
- One-time onboarding on https://polymarket.com with that wallet, so USDC + CTF allowances are set.

## Setup

```bash
git clone <your-repo-url> trading-bot
cd trading-bot
cp polymarket/.env.example polymarket/.env
$EDITOR polymarket/.env       # paste POLY_PRIVATE_KEY; leave POLY_DRY_RUN=true for now
```

Build the image (one-time, ~3 minutes on a small VPS):

```bash
docker compose build
```

Derive Polymarket API creds (one-time, writes `polymarket/polymarket_creds.json`):

```bash
docker compose run --rm trader python setup_wallet.py
```

The address it prints must match the wallet you funded. If it doesn't, re-check
`POLY_PRIVATE_KEY` and `POLY_FUNDER_ADDRESS` in `.env`.

## Run

```bash
docker compose up -d                          # start both services
docker compose logs -f trader                 # tail the trader
docker compose logs -f ws-recorder            # tail the orderbook recorder
docker compose down                           # stop both
```

JSONL trade records and stdout logs land in `polymarket/logs/` on the host
(persistent across container restarts).

## Going live

The bot stays in dry-run mode until you do two things:

1. Edit `polymarket/.env`: set `POLY_DRY_RUN=false`.
2. Restart: `docker compose up -d --force-recreate trader`.

Risk caps (`POLY_MAX_ORDER_USDC`, `POLY_MAX_DAILY_USDC`) are enforced in
`trader.py` and apply both in dry-run and live.

## Local development (without Docker)

For editing code, running the backtest, or invoking one-off scripts on your laptop.

**Requirements:** Python 3.12, `pip`. (macOS: `brew install python@3.12`; Debian/Ubuntu: `apt install python3.12 python3.12-venv`.)

```bash
# One-time: create a shared venv used by polymarket/ and backtest/ tools.
# (The shell scripts default to ../backtest/.venv as the interpreter path —
#  match that exactly or override $PYTHON in the env.)
cd trading-bot/backtest
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r ../polymarket/requirements.txt
.venv/bin/pip install -r requirements.txt   # backtest-only deps
```

After that, run any script with `../backtest/.venv/bin/python …` from inside `polymarket/`, or activate it with `source ../backtest/.venv/bin/activate`.

Quick smoke tests:

```bash
cd polymarket
../backtest/.venv/bin/python -c "import live_trader, trader, coinbase_stream; print('imports ok')"
../backtest/.venv/bin/python -c "from gamma import discover_btc_markets; print(len(discover_btc_markets()), 'upcoming 5-min markets')"
```

## Scripts reference

All Python scripts are runnable directly with `python <name>.py`. The shell scripts default to `../backtest/.venv/bin/python` but accept `PYTHON=...` to override (used by Docker).

### Trading runtime (`polymarket/`)

| Script | Purpose | Typical invocation |
|---|---|---|
| `live_trader.py` | Main trading loop. Binance + Coinbase confirm + sweet-spot gate → FOK BUY. | `python live_trader.py --threshold 0.10 --sweet-lo 0.30 --sweet-hi 0.40 --require-confirm` |
| `run_live.sh` | Auto-restart wrapper around `live_trader.py` with daily log rotation. Used by the `trader` Docker service. | `./run_live.sh` (env: `THRESHOLD`, `COOLDOWN`, `SWEET_LO`, `SWEET_HI`, `REQUIRE_CONFIRM`) |
| `monitor.py` | Observation-only harness (no trading). Logs hypothetical signals + outcomes. Predecessor to `live_trader.py`. | `python monitor.py --threshold 0.30 --log signals.jsonl` |
| `run_local.sh` | Auto-restart wrapper around `monitor.py`. | `./run_local.sh` |
| `status.sh` | Quick stdout health snapshot of `monitor.py`. | `./status.sh` |
| `orderbook_recorder_ws.py` | WS-based full L2 orderbook recorder. Used by the `ws-recorder` Docker service. | `python orderbook_recorder_ws.py` |
| `orderbook_recorder.py` | HTTP-polling recorder (5s interval). Older; prefer the WS one. | `python orderbook_recorder.py` |
| `rotate_logs.sh` | Gzip yesterday's WS log, prune > `RETAIN_DAYS`. | Host cron — see _Log retention_. |

### One-time setup (`polymarket/`)

Run these once per wallet/machine before live trading.

| Script | Purpose | When to run |
|---|---|---|
| `setup_wallet.py` | Derives Polymarket API creds from your `POLY_PRIVATE_KEY` and writes `polymarket_creds.json`. | First-time, or after rotating keys. |
| `setup_allowances.py` | Sets USDC.e + CTF on-chain allowances against Polymarket's exchange contracts. | First-time, if `setup_wallet.py` flagged missing allowances. UI onboarding usually does this for you. |

### Diagnostics / debugging (`polymarket/`)

Safe to run; they post non-marketable orders or just probe state.

| Script | Use when… |
|---|---|
| `probe_sig_types.py` | Polymarket rejects your orders and you want to isolate which `signature_type` it accepts. |
| `test_signature.py` / `test_signature_v2.py` | Validate the signature flow end-to-end with the old (`py-clob-client`) and new (`polymarket-client`) SDKs. |
| `verify_live_fills.py` | Cross-validate logged trader signals against the WS recorder's contemporaneous book — confirms the fill price you _would_ have got matches what you logged. |
| `sdk_patch.py` | Not a script — module import that monkey-patches `py-clob-client` to accept `sig_type=3`. Imported automatically where needed. |

### Backtest workflow (`polymarket/backtest/`)

End-to-end re-validation against the last N days of data:

```bash
cd polymarket/backtest

# 1. Discover historical markets (writes cache/markets_*.json)
../../backtest/.venv/bin/python historical_markets.py --days 30

# 2. Fetch trade tapes per market (writes cache/trades/<conditionId>.json)
../../backtest/.venv/bin/python historical_trades.py --markets-file cache/markets_30d.json

# 3. Fetch Binance BTCUSDT 1-second candles for the window
../../backtest/.venv/bin/python historical_btc_1s.py --markets-file cache/markets_30d.json

# 4. Tick-aligned replay (mirrors live MoveTracker exactly)
../../backtest/.venv/bin/python replay_tick.py \
  --markets-file cache/markets_30d.json \
  --btc-file cache/btc_1s_*.parquet \
  --sweet-lo 0.30 --sweet-hi 0.40

# 5. Analyze
../../backtest/.venv/bin/python analyze_tick.py --trades cache/tick_replay.jsonl
```

Other backtest tools:

| Script | Purpose |
|---|---|
| `replay.py` / `replay_trades.py` | Older lower-fidelity replays (1m candles). Kept for comparison. |
| `permutation.py` | Permutation significance test on a `tick_replay.jsonl` — beats random direction with p<? |
| `stability.py` | Walk-forward stability check on rolling windows. |
| `probe_api.py` | Sanity-check Polymarket Gamma/CLOB endpoints from your IP. |

## Strategy parameters

Defaults are baked into `polymarket/run_live.sh` (override via env vars in compose):

| Var | Default | Meaning |
|---|---|---|
| `THRESHOLD` | `0.10` | Binance 60s return %% needed to fire |
| `COOLDOWN` | `60` | Seconds between signals |
| `SWEET_LO` | `0.30` | Lower entry-price band |
| `SWEET_HI` | `0.40` | Upper entry-price band (tightened from 0.45 — see `memory/` notes) |
| `REQUIRE_CONFIRM` | `1` | Require Coinbase 60s return to agree with Binance |

Validation history:
- 30-day tick-aligned backtest: 62% win rate overall, best bucket [0.30, 0.40] at 67%.
- Live (small N, dry-run): 1/4 wins under the previous [0.30, 0.45] band; tightened to [0.30, 0.40] on 2026-06-08.

## Auto-deploy on git push

`deploy.sh` (at the repo root on the server) pulls the latest commit from the
tracked branch and, if HEAD moved, runs `docker compose up -d --build
--force-recreate`. It's idempotent — exits in < 1 second when there's nothing
new. Schedule it to poll every few minutes.

### Option A: cron (simplest)

```cron
# /etc/cron.d/polymarket-deploy
*/5 * * * * root /opt/trading-bot/deploy.sh >> /var/log/polymarket-deploy.log 2>&1
```

### Option B: systemd timer (nicer logs via `journalctl`)

```ini
# /etc/systemd/system/polymarket-deploy.service
[Unit]
Description=Polymarket bot auto-deploy
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/trading-bot
ExecStart=/opt/trading-bot/deploy.sh
```

```ini
# /etc/systemd/system/polymarket-deploy.timer
[Unit]
Description=Run polymarket-deploy every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:
```bash
systemctl daemon-reload
systemctl enable --now polymarket-deploy.timer
journalctl -u polymarket-deploy.service -f
```

**Trade-off:** up to 5 min latency between `git push` and deploy. Lower the
`OnUnitActiveSec` (or the cron interval) if you want it tighter — anything
below 1 min just adds load without value, since the rebuild itself takes ~10 s.

**Manual deploy** any time: `/opt/trading-bot/deploy.sh`.

## Resilience: network drops, power outages, autostart

The bot is designed to recover from network blips, host reboots, and partial
failures without intervention. Layered defenses, outermost first:

1. **Host autostart on boot.** Ensures the Docker daemon (and your container) come back after a power cut.
   - Proxmox LXC: set `onboot: 1` in the container config (web UI: _Options → Start at boot_).
   - Inside the LXC: `systemctl enable --now docker` so the Docker daemon starts automatically.
2. **Container restart-on-crash.** `restart: unless-stopped` in `docker-compose.yml` brings the trader and recorder back if either crashes. `docker compose down` is the only thing that stops them permanently.
3. **Process restart-on-crash.** `run_live.sh` wraps `live_trader.py` in `while true; ... ; sleep 10` — any Python exception → 10s backoff → restart.
4. **Stream watchdog (added 2026-06-08).** `live_trader.py` exits with code 2 if no Binance trade arrives in 90s (or, with `REQUIRE_CONFIRM=1`, no Coinbase trade). Catches half-dead TCP that the WS ping/pong didn't catch.
5. **WS auto-reconnect with 2s backoff.** Both `binance_stream.py` and `coinbase_stream.py` retry forever on disconnect. The `websockets` library's 20s ping interval will tear down a stalled connection.

**End-to-end recovery on a 30-minute power outage:** host boots → LXC starts (onboot=1) → Docker daemon starts (systemd enable) → compose containers restart (unless-stopped) → trader runs `run_live.sh` → live_trader reconnects to Binance/Coinbase → resumes streaming. No manual steps.

**Known gap (not yet fixed):** if the trader crashes between order placement and the market's resolution time, the local outcome log won't be written — the order itself is on-chain on Polymarket, but our `logs/live_*.jsonl` will lack the win/loss record. Audit-only impact; live capital is safe.

## Log retention

Two log shapes with very different sizes:

| Log | Size | What to do |
|---|---|---|
| `live_*.jsonl` + `live_*.log` (trader) | ~1 MB/day | Keep indefinitely — tiny |
| `orderbook_ws_*.jsonl` (WS recorder) | ~10 GB/day | Compress yesterday's (~10× reduction), prune > 14 days |

`polymarket/rotate_logs.sh` handles both. Schedule on the host:

```cron
# /etc/cron.d/polymarket-rotate
0 3 * * * root cd /path/to/trading-bot && docker compose exec -T trader bash rotate_logs.sh
```

Or run on the host directly against the mounted dir:

```cron
0 3 * * * root LOG_DIR=/path/to/trading-bot/polymarket/logs /path/to/trading-bot/polymarket/rotate_logs.sh
```

Adjust `RETAIN_DAYS` (env var, default 14) to taste. Trader signal JSONLs are
never pruned by this script — they're the audit trail.

When you need to analyze on another machine, copy the dir manually (rsync, scp, USB, etc.).

## Proxmox notes

- An LXC container running Docker is the simplest deploy. Privileged isn't required for outbound-only traffic.
- Tick **_Options → Start at boot_** (or `pct set <id> -onboot 1` from the host).
- Set the container's timezone to UTC: `timedatectl set-timezone UTC`. The strategy assumes UTC throughout (Polymarket window endpoints use UTC).
- Open outbound HTTPS/WSS only — no inbound ports needed.
- 1 vCPU / 1 GB RAM is sufficient. Disk: size for at least 14 days of logs (~150 GB) with the rotation cron above, or 30 days (~300 GB) without it.

## Repo layout

```
polymarket/
  trader.py                  # Polymarket order placement (v2 SDK)
  live_trader.py             # main loop: signal → confirm → fill
  binance_stream.py          # Binance BTC trades WS
  coinbase_stream.py         # Coinbase BTC-USD WS (confirmation gate)
  gamma.py                   # Polymarket market discovery
  clob.py                    # Polymarket REST orderbook
  monitor.py                 # MoveTracker (60s rolling return) + observation harness
  orderbook_recorder_ws.py   # full L2 orderbook recorder
  setup_wallet.py            # derives API creds from your private key
  run_live.sh                # auto-restart wrapper used by `trader` service
  backtest/                  # tick-aligned replay tools (replay_tick.py, analyze_tick.py)
  logs/                      # *.jsonl + *.log written by both services
backtest/                    # generic backtest framework (other strategies)
regime-classifier/           # unrelated sub-project
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `address banned` from Polymarket API | The EOA is API-blocked. Use a Polymarket deposit-wallet proxy: set `POLY_FUNDER_ADDRESS` to your proxy and keep `POLY_PRIVATE_KEY` as the EOA's. |
| `nodename nor servname provided` in trader log | DNS hiccup. The wrapper auto-restarts; if persistent, check container DNS (`docker compose exec trader getent hosts gamma-api.polymarket.com`). |
| `WebSocket heartbeat stale` from ws-recorder | One-off, the SDK reconnects internally. Persistent staleness = restart the recorder. |
| Image build fails on `pandas` | Likely an old base image. Force rebuild: `docker compose build --no-cache`. |

## Security

- `polymarket/.env` and `polymarket/polymarket_creds.json` are gitignored. Never commit them.
- The image does not bake in your private key — it's mounted at runtime via `env_file`.
- Run the container as a non-root user only if your host supports it; the image runs as root by default for simplicity.
