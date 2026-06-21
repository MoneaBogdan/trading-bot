from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
DB_PATH = Path(os.environ.get("CONTROL_CENTER_DB", "/app/control-center/data/control_center.sqlite3"))
POLY_LOG_DIR = Path(os.environ.get("POLYMARKET_LOG_DIR", "/app/polymarket/logs"))
HL_LOG_DIR = Path(os.environ.get("HYPERLIQUID_LOG_DIR", "/app/hyperliquid/logs"))
DOCKER_SOCKET = Path(os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"))
SYNC_INTERVAL_S = int(os.environ.get("CONTROL_CENTER_SYNC_INTERVAL_S", "30"))
HTTP_HOST = os.environ.get("CONTROL_CENTER_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("CONTROL_CENTER_PORT", "8080"))
AUTH_USER = os.environ.get("CONTROL_CENTER_USER", "admin")
AUTH_PASSWORD = os.environ.get("CONTROL_CENTER_PASSWORD", "change-me")

DB_LOCK = threading.Lock()
LAST_SYNC: dict[str, Any] = {"running": False, "ok": None, "error": None, "ts": None}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    with DB_LOCK, db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingested_files (
                path TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                rows INTEGER NOT NULL,
                last_ingested_ts TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                ts TEXT,
                bot TEXT,
                strategy TEXT,
                event TEXT,
                reason TEXT,
                venue TEXT,
                market_id TEXT,
                market_title TEXT,
                outcome_name TEXT,
                side TEXT,
                limit_price REAL,
                size_usdc REAL,
                filled_size REAL,
                filled_price REAL,
                cost_usdc REAL,
                order_ok INTEGER,
                dry_run INTEGER,
                raw_json TEXT NOT NULL,
                UNIQUE(source_path, line_no)
            );
            CREATE INDEX IF NOT EXISTS idx_bot_events_ts ON bot_events(ts);
            CREATE INDEX IF NOT EXISTS idx_bot_events_bot_ts ON bot_events(bot, ts);
            CREATE INDEX IF NOT EXISTS idx_bot_events_event_ts ON bot_events(event, ts);
            CREATE INDEX IF NOT EXISTS idx_bot_events_reason ON bot_events(reason);

            CREATE TABLE IF NOT EXISTS funding_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                ts TEXT,
                event TEXT,
                asset TEXT,
                best_cex TEXT,
                best_spread_bps_8h REAL,
                long_perp TEXT,
                short_perp TEXT,
                spread_bps_8h REAL,
                gross_pnl_8h_usdc REAL,
                est_costs_8h_usdc REAL,
                net_pnl_8h_usdc REAL,
                annualized_apr_pct_net REAL,
                raw_json TEXT NOT NULL,
                UNIQUE(source_path, line_no)
            );
            CREATE INDEX IF NOT EXISTS idx_funding_ts ON funding_events(ts);
            CREATE INDEX IF NOT EXISTS idx_funding_event_asset_ts ON funding_events(event, asset, ts);

            CREATE TABLE IF NOT EXISTS log_files (
                path TEXT PRIMARY KEY,
                family TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                updated_ts TEXT NOT NULL
            );
            """
        )


def parse_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append((line_no, json.loads(line)))
            except json.JSONDecodeError:
                continue
    return rows


def maybe_skip(conn: sqlite3.Connection, path: Path, kind: str) -> bool:
    st = path.stat()
    row = conn.execute("SELECT size, mtime FROM ingested_files WHERE path=?", (str(path),)).fetchone()
    if row and row["size"] == st.st_size and abs(float(row["mtime"]) - st.st_mtime) < 0.0001:
        return True
    conn.execute("DELETE FROM bot_events WHERE source_path=?", (str(path),))
    conn.execute("DELETE FROM funding_events WHERE source_path=?", (str(path),))
    conn.execute("DELETE FROM ingested_files WHERE path=?", (str(path),))
    return False


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError:
                continue


def ingest_bot_file(conn: sqlite3.Connection, path: Path) -> int:
    if maybe_skip(conn, path, "bot"):
        return 0
    inserted = 0
    for line_no, row in iter_jsonl(path):
        conn.execute(
            """
            INSERT OR IGNORE INTO bot_events (
                source_path, line_no, ts, bot, strategy, event, reason, venue,
                market_id, market_title, outcome_name, side, limit_price,
                size_usdc, filled_size, filled_price, cost_usdc, order_ok,
                dry_run, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(path), line_no, row.get("ts"), row.get("bot"), row.get("strategy"),
                row.get("event"), row.get("reason"), row.get("venue"), row.get("market_id"),
                row.get("market_title"), row.get("outcome_name"), row.get("side"),
                _float(row.get("limit_price")), _float(row.get("size_usdc")),
                _float(row.get("filled_size")), _float(row.get("filled_price")),
                _float(row.get("cost_usdc")), _bool_int(row.get("order_ok")),
                _bool_int(row.get("dry_run")), json.dumps(row, separators=(",", ":")),
            ),
        )
        inserted += 1
    record_ingested(conn, path, "bot", inserted)
    return inserted


def ingest_funding_file(conn: sqlite3.Connection, path: Path) -> int:
    if maybe_skip(conn, path, "funding"):
        return 0
    inserted = 0
    for line_no, row in iter_jsonl(path):
        conn.execute(
            """
            INSERT OR IGNORE INTO funding_events (
                source_path, line_no, ts, event, asset, best_cex,
                best_spread_bps_8h, long_perp, short_perp, spread_bps_8h,
                gross_pnl_8h_usdc, est_costs_8h_usdc, net_pnl_8h_usdc,
                annualized_apr_pct_net, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(path), line_no, row.get("ts"), row.get("event"), row.get("asset"),
                row.get("best_cex"), _float(row.get("best_spread_bps_8h")),
                row.get("long_perp"), row.get("short_perp"),
                _float(row.get("spread_bps_8h")), _float(row.get("gross_pnl_8h_usdc")),
                _float(row.get("est_costs_8h_usdc")), _float(row.get("net_pnl_8h_usdc")),
                _float(row.get("annualized_apr_pct_net")),
                json.dumps(row, separators=(",", ":")),
            ),
        )
        inserted += 1
        if inserted % 500 == 0:
            conn.commit()
    record_ingested(conn, path, "funding", inserted)
    return inserted


def record_ingested(conn: sqlite3.Connection, path: Path, kind: str, rows: int) -> None:
    st = path.stat()
    conn.execute(
        """
        INSERT OR REPLACE INTO ingested_files(path, kind, size, mtime, rows, last_ingested_ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (str(path), kind, st.st_size, st.st_mtime, rows, utc_now()),
    )


def refresh_file_inventory(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM log_files")
    roots = [
        (POLY_LOG_DIR, "polymarket"),
        (HL_LOG_DIR, "hyperliquid"),
    ]
    for root, family in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                st = path.stat()
                conn.execute(
                    "INSERT OR REPLACE INTO log_files(path, family, size, mtime, updated_ts) VALUES (?, ?, ?, ?, ?)",
                    (str(path), family, st.st_size, st.st_mtime, utc_now()),
                )


def sync_once() -> dict[str, Any]:
    started = time.time()
    counts = {"bot_files": 0, "funding_files": 0, "rows_changed": 0}
    LAST_SYNC.update({"running": True, "ts": utc_now(), "error": None, "ok": {**counts, "phase": "starting"}})
    try:
        with DB_LOCK, db_connect() as conn:
            refresh_file_inventory(conn)
            conn.commit()
            LAST_SYNC["ok"] = {**counts, "phase": "inventory"}
            if POLY_LOG_DIR.exists():
                for path in sorted(POLY_LOG_DIR.glob("bot=*/20*.jsonl")):
                    counts["rows_changed"] += ingest_bot_file(conn, path)
                    counts["bot_files"] += 1
                    conn.commit()
                    LAST_SYNC["ok"] = {**counts, "phase": f"bot:{path.parent.name}/{path.name}"}
            if HL_LOG_DIR.exists():
                for path in sorted(HL_LOG_DIR.glob("funding_*.jsonl")):
                    counts["rows_changed"] += ingest_funding_file(conn, path)
                    counts["funding_files"] += 1
                    conn.commit()
                    LAST_SYNC["ok"] = {**counts, "phase": f"funding:{path.name}"}
            conn.commit()
        result = {**counts, "duration_s": round(time.time() - started, 3), "ts": utc_now()}
        LAST_SYNC.update({"running": False, "ok": result, "error": None, "ts": result["ts"]})
        print(f"[control-center] sync complete {result}", flush=True)
        return result
    except Exception as exc:
        LAST_SYNC.update({"running": False, "ok": None, "error": f"{type(exc).__name__}: {exc}", "ts": utc_now()})
        raise


def sync_loop() -> None:
    while True:
        try:
            sync_once()
        except Exception as exc:
            print(f"[control-center] sync failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(SYNC_INTERVAL_S)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingControlCenter/0.1"

    def do_GET(self) -> None:
        if not self.authorized():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/":
                self.serve_static("index.html", "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                rel = path.removeprefix("/static/")
                self.serve_static(rel, content_type_for(rel))
            elif path == "/api/summary":
                self.send_json(api_summary())
            elif path == "/api/events":
                self.send_json(api_events(query))
            elif path == "/api/inventory":
                self.send_json(api_inventory())
            elif path == "/api/containers":
                self.send_json(api_containers())
            elif path == "/api/funding":
                self.send_json(api_funding(query))
            elif path == "/api/files":
                self.send_json(api_files(query))
            elif path == "/api/tail":
                self.send_json(api_tail(query))
            elif path == "/health":
                self.send_json({"ok": True, "last_sync": LAST_SYNC, "time": utc_now()})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_POST(self) -> None:
        if not self.authorized():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/resync":
                self.send_json(sync_once())
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(f"{AUTH_USER}:{AUTH_PASSWORD}".encode()).decode()
        if secrets.compare_digest(header, expected):
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Trading Bot Control Center"')
        self.end_headers()
        return False

    def serve_static(self, rel: str, content_type: str) -> None:
        target = (STATIC_ROOT / rel).resolve()
        if not str(target).startswith(str(STATIC_ROOT.resolve())) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[control-center] {self.address_string()} {fmt % args}", flush=True)


def api_summary() -> dict[str, Any]:
    with db_connect() as conn:
        event_counts = rows_to_dicts(conn.execute(
            "SELECT event, COUNT(*) AS n FROM bot_events GROUP BY event ORDER BY n DESC"
        ).fetchall())
        bot_counts = rows_to_dicts(conn.execute(
            """
            SELECT bot,
                   SUM(CASE WHEN event='fire' THEN 1 ELSE 0 END) AS fires,
                   SUM(CASE WHEN event='skip' THEN 1 ELSE 0 END) AS skips,
                   SUM(CASE WHEN event='bot_crashed' THEN 1 ELSE 0 END) AS crashes,
                   MAX(ts) AS last_ts
            FROM bot_events
            GROUP BY bot
            ORDER BY bot
            """
        ).fetchall())
        funding_counts = rows_to_dicts(conn.execute(
            "SELECT event, asset, COUNT(*) AS n, MAX(ts) AS last_ts FROM funding_events GROUP BY event, asset ORDER BY event, asset"
        ).fetchall())
        recent_fires = rows_to_dicts(conn.execute(
            """
            SELECT ts, bot, market_title, outcome_name, limit_price, size_usdc, order_ok, dry_run
            FROM bot_events
            WHERE event='fire'
            ORDER BY ts DESC
            LIMIT 12
            """
        ).fetchall())
        recent_opps = rows_to_dicts(conn.execute(
            """
            SELECT ts, asset, long_perp, short_perp, spread_bps_8h, net_pnl_8h_usdc, annualized_apr_pct_net
            FROM funding_events
            WHERE event='opportunity'
            ORDER BY ts DESC
            LIMIT 12
            """
        ).fetchall())
        file_stats = dict(conn.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(size), 0) AS bytes FROM log_files"
        ).fetchone())
        return {
            "time": utc_now(),
            "last_sync": LAST_SYNC,
            "event_counts": event_counts,
            "bot_counts": bot_counts,
            "funding_counts": funding_counts,
            "recent_fires": recent_fires,
            "recent_opportunities": recent_opps,
            "file_stats": file_stats,
            "db_path": str(DB_PATH),
            "roots": {"polymarket": str(POLY_LOG_DIR), "hyperliquid": str(HL_LOG_DIR)},
        }


def api_events(query: dict[str, list[str]]) -> dict[str, Any]:
    limit = bounded_int(query.get("limit", ["100"])[0], 1, 500)
    where = []
    params: list[Any] = []
    for key in ("bot", "event", "reason"):
        value = query.get(key, [""])[0]
        if value:
            where.append(f"{key}=?")
            params.append(value)
    sql = "SELECT * FROM bot_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with db_connect() as conn:
        return {"items": rows_to_dicts(conn.execute(sql, params).fetchall())}


def api_inventory() -> dict[str, Any]:
    with db_connect() as conn:
        bot_rows = rows_to_dicts(conn.execute(
            """
            SELECT bot,
                   COUNT(*) AS total_events,
                   SUM(CASE WHEN event='fire' THEN 1 ELSE 0 END) AS fires,
                   SUM(CASE WHEN event='skip' THEN 1 ELSE 0 END) AS skips,
                   SUM(CASE WHEN event='boot' THEN 1 ELSE 0 END) AS boots,
                   SUM(CASE WHEN event='bot_crashed' THEN 1 ELSE 0 END) AS crashes,
                   MIN(ts) AS first_ts,
                   MAX(ts) AS last_ts,
                   COUNT(DISTINCT source_path) AS files
            FROM bot_events
            WHERE bot IS NOT NULL
            GROUP BY bot
            ORDER BY bot
            """
        ).fetchall())

        items: list[dict[str, Any]] = []
        for bot in bot_rows:
            name = bot["bot"]
            skip_reasons = rows_to_dicts(conn.execute(
                """
                SELECT reason, COUNT(*) AS n
                FROM bot_events
                WHERE bot=? AND event='skip' AND reason IS NOT NULL
                GROUP BY reason
                ORDER BY n DESC
                LIMIT 5
                """,
                (name,),
            ).fetchall())
            latest_fire = conn.execute(
                """
                SELECT ts, market_title, outcome_name, limit_price, order_ok, dry_run
                FROM bot_events
                WHERE bot=? AND event='fire'
                ORDER BY ts DESC
                LIMIT 1
                """,
                (name,),
            ).fetchone()
            latest_boot = conn.execute(
                """
                SELECT raw_json
                FROM bot_events
                WHERE bot=? AND event='boot'
                ORDER BY ts DESC
                LIMIT 1
                """,
                (name,),
            ).fetchone()
            config = None
            if latest_boot:
                try:
                    raw = json.loads(latest_boot["raw_json"])
                    config = raw.get("config") or raw.get("debug") or raw
                except json.JSONDecodeError:
                    config = None
            item = dict(bot)
            item["skip_reasons"] = skip_reasons
            item["latest_fire"] = dict(latest_fire) if latest_fire else None
            item["config"] = config
            items.append(item)
        return {"items": items, "time": utc_now(), "last_sync": LAST_SYNC}


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path):
        super().__init__("localhost")
        self.socket_path = str(socket_path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        self.sock = sock


def docker_api(path: str) -> Any:
    if not DOCKER_SOCKET.exists():
        raise FileNotFoundError(f"Docker socket not mounted at {DOCKER_SOCKET}")
    conn = UnixHTTPConnection(DOCKER_SOCKET)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"Docker API {path} returned {resp.status}: {body[:300]!r}")
        return json.loads(body.decode() or "null")
    finally:
        conn.close()


def api_containers() -> dict[str, Any]:
    try:
        containers = docker_api("/containers/json?all=1")
        items = []
        for row in containers:
            names = [name.lstrip("/") for name in row.get("Names", [])]
            name = names[0] if names else row.get("Id", "")[:12]
            if not is_trading_container(name):
                continue
            detail = docker_api(f"/containers/{row['Id']}/json")
            env = env_dict(detail.get("Config", {}).get("Env", []))
            labels = detail.get("Config", {}).get("Labels", {}) or {}
            state = detail.get("State", {}) or {}
            strategy = infer_container_strategy(name, env, labels)
            items.append({
                "id": row.get("Id", "")[:12],
                "name": name,
                "service": labels.get("com.docker.compose.service"),
                "status": row.get("Status"),
                "state": row.get("State"),
                "image": row.get("Image"),
                "created": row.get("Created"),
                "started_at": state.get("StartedAt"),
                "finished_at": state.get("FinishedAt"),
                "restart_count": detail.get("RestartCount"),
                "exit_code": state.get("ExitCode"),
                "oom_killed": state.get("OOMKilled"),
                "strategy": strategy["strategy"],
                "bot": strategy["bot"],
                "asset": env.get("ASSET"),
                "timeframe_min": env.get("TIMEFRAME_MIN"),
                "variant_suffix": env.get("VARIANT_SUFFIX"),
                "threshold": env.get("THRESHOLD"),
                "sweet_lo": env.get("SWEET_LO"),
                "sweet_hi": env.get("SWEET_HI"),
                "require_confirm": env.get("REQUIRE_CONFIRM"),
                "dry_run": env.get("POLY_DRY_RUN"),
            })
        items.sort(key=lambda item: item["name"])
        return {"items": items, "time": utc_now(), "socket": str(DOCKER_SOCKET), "error": None}
    except Exception as exc:
        return {"items": [], "time": utc_now(), "socket": str(DOCKER_SOCKET), "error": f"{type(exc).__name__}: {exc}"}


def is_trading_container(name: str) -> bool:
    return (
        name.startswith("polymarket-trader-")
        or name in {
            "polymarket-ws-recorder",
            "hyperliquid-monitor",
            "polymarket-news-alpha",
            "trading-control-center",
        }
    )


def env_dict(items: list[str]) -> dict[str, str]:
    result = {}
    for item in items:
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def infer_container_strategy(name: str, env: dict[str, str], labels: dict[str, str]) -> dict[str, str | None]:
    if name.startswith("polymarket-trader-"):
        asset = env.get("ASSET", "")
        tf = env.get("TIMEFRAME_MIN", "")
        suffix = env.get("VARIANT_SUFFIX")
        bot = f"{asset.lower()}-{tf}m" if asset and tf else name.removeprefix("polymarket-trader-")
        if suffix:
            bot = f"{bot}-{suffix}"
        return {"strategy": "PolymarketLatencyArb", "bot": bot}
    if name == "hyperliquid-monitor":
        return {"strategy": "HyperliquidFundingMonitor", "bot": "hyperliquid-monitor"}
    if name == "polymarket-ws-recorder":
        return {"strategy": "PolymarketOrderbookRecorder", "bot": "ws-recorder"}
    if name == "polymarket-news-alpha":
        return {"strategy": "NewsAlpha", "bot": "news-alpha"}
    if name == "trading-control-center":
        return {"strategy": "ControlCenter", "bot": "control-center"}
    return {"strategy": labels.get("com.docker.compose.service") or "unknown", "bot": name}


def api_funding(query: dict[str, list[str]]) -> dict[str, Any]:
    limit = bounded_int(query.get("limit", ["100"])[0], 1, 500)
    where = []
    params: list[Any] = []
    for key in ("event", "asset"):
        value = query.get(key, [""])[0]
        if value:
            where.append(f"{key}=?")
            params.append(value)
    sql = "SELECT * FROM funding_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with db_connect() as conn:
        return {"items": rows_to_dicts(conn.execute(sql, params).fetchall())}


def api_files(query: dict[str, list[str]]) -> dict[str, Any]:
    family = query.get("family", [""])[0]
    limit = bounded_int(query.get("limit", ["50"])[0], 10, 500)
    params: list[Any] = []
    sql = "SELECT path, family, size, mtime, datetime(mtime, 'unixepoch') AS mtime_utc FROM log_files"
    if family:
        sql += " WHERE family=?"
        params.append(family)
    sql += " ORDER BY mtime DESC LIMIT ?"
    params.append(limit)
    with db_connect() as conn:
        return {"items": rows_to_dicts(conn.execute(sql, params).fetchall()), "limit": limit}


def api_tail(query: dict[str, list[str]]) -> dict[str, Any]:
    raw_path = query.get("path", [""])[0]
    lines = bounded_int(query.get("lines", ["80"])[0], 1, 500)
    if not raw_path:
        raise ValueError("path is required")
    path = Path(unquote(raw_path)).resolve()
    allowed = [POLY_LOG_DIR.resolve(), HL_LOG_DIR.resolve()]
    if not any(str(path).startswith(str(root)) for root in allowed):
        raise ValueError("path outside allowed log roots")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return {"path": str(path), "lines": tail_file(path, lines)}


def bounded_int(value: str, lo: int, hi: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        parsed = lo
    return max(lo, min(hi, parsed))


def tail_file(path: Path, lines: int) -> list[str]:
    # Read from the end without loading multi-GB orderbook files.
    chunk_size = 8192
    data = b""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0 and data.count(b"\n") <= lines:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            data = f.read(read_size) + data
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


def content_type_for(path: str) -> str:
    if path.endswith(".css"):
        return "text/css; charset=utf-8"
    if path.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if path.endswith(".html"):
        return "text/html; charset=utf-8"
    return "application/octet-stream"


def main() -> None:
    init_db()
    threading.Thread(target=sync_loop, daemon=True).start()
    print(
        f"[control-center] listening on {HTTP_HOST}:{HTTP_PORT} "
        f"user={AUTH_USER!r} db={DB_PATH}",
        flush=True,
    )
    ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
