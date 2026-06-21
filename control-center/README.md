# Trading Bot Control Center

Authenticated, read-only sidecar for fleet observability.

## What it does

- Indexes `polymarket/logs/bot=*/YYYY-MM-DD.jsonl` into SQLite.
- Indexes `hyperliquid/logs/funding_*.jsonl` into SQLite.
- Tracks all log files, including large orderbook recorder files, without
  ingesting the multi-GB orderbook payloads.
- Serves a small web UI for overview, bot events, funding monitor events,
  file freshness, raw tails, and manual resync.

It does not mount Polymarket credentials and does not place or cancel orders.

## Run

`docker-compose.yml` includes the service:

```bash
docker compose up -d --build control-center
```

The service binds to localhost by default:

```bash
ssh -L 8080:127.0.0.1:8080 root@192.168.1.57
```

Then open:

```text
http://127.0.0.1:8080
```

Credentials come from the ignored root `.env` file:

```dotenv
CONTROL_CENTER_USER=admin
CONTROL_CENTER_PASSWORD=...
CONTROL_CENTER_BIND=127.0.0.1
CONTROL_CENTER_SYNC_INTERVAL_S=30
```

## Storage

Derived SQLite state lives in:

```text
control-center/data/control_center.sqlite3
```

It can be deleted at any time; the sidecar will rebuild it from JSONL logs.

## API Smoke Checks

```bash
curl -u "$CONTROL_CENTER_USER:$CONTROL_CENTER_PASSWORD" \
  http://127.0.0.1:8080/health

curl -u "$CONTROL_CENTER_USER:$CONTROL_CENTER_PASSWORD" \
  http://127.0.0.1:8080/api/summary
```

Unauthenticated requests should return `401`.
