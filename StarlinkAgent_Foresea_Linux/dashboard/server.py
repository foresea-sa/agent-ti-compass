from __future__ import annotations

import json
import logging
import mimetypes
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from analytics.trends import apply_historical_analytics
from database.db import get_history_by_units, init_db

BASE = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
DB_PATH = BASE / "database" / "starlink.db"
CONFIG_PATH = BASE / "config.json"
FALLBACK_CONFIG_PATH = BASE / "config.example.json"
ASSETS = BASE / "assets"

logger = logging.getLogger("starlink-dashboard")


def _config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else FALLBACK_CONFIG_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date().isoformat()
    except Exception:
        return str(value)[:10]


def _display_row(row: dict) -> dict:
    keys = {
        "unit", "collected_at", "period", "period_start", "period_end", "period_days", "source_name", "source_file",
        "terminal", "kit_name", "service_line", "plan_name",
        "quota_gb", "priority_gb", "booster_gb", "standard_gb", "overage_gb", "total_gb", "remaining_gb",
        "usage_pct", "portal_usage_pct", "status", "rate_gb_day", "trend", "history_points", "days_to_limit",
        "forecast_limit_date", "cycle_start_date", "cycle_end_date", "projected_cycle_end_gb", "projected_overage_gb",
        "forecast_risk", "projection_method", "data_age_days", "data_freshness", "forecast_confidence",
    }
    data = {k: row.get(k) for k in keys}
    for key in [
        "quota_gb", "priority_gb", "booster_gb", "standard_gb", "overage_gb", "total_gb", "remaining_gb",
        "usage_pct", "portal_usage_pct", "projected_overage_gb", "data_age_days",
    ]:
        data[key] = _to_float(data.get(key))
    for key in ["rate_gb_day", "days_to_limit", "projected_cycle_end_gb"]:
        value = data.get(key)
        data[key] = None if value is None else _to_float(value)
    return data


def _latest_rows(conn: sqlite3.Connection) -> list[dict]:
    query = """
        SELECT * FROM usage_history
        WHERE id IN (
            SELECT MAX(id) FROM usage_history GROUP BY unit
        )
        ORDER BY usage_pct DESC, unit ASC
    """
    return [dict(r) for r in conn.execute(query).fetchall()]


def _history_rows(conn: sqlite3.Connection, days: int) -> dict[str, list[dict]]:
    cutoff = (datetime.now() - timedelta(days=max(days - 1, 0))).date().isoformat()
    query = """
        SELECT id, collected_at, period_end, unit, quota_gb, total_gb, usage_pct, status
        FROM usage_history
        WHERE COALESCE(period_end, substr(collected_at,1,10)) >= ?
        ORDER BY unit, COALESCE(period_end, substr(collected_at,1,10)), collected_at, id
    """
    rows = [dict(r) for r in conn.execute(query, [cutoff]).fetchall()]
    daily: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        day = _iso_date(row.get("period_end") or row.get("collected_at"))
        if not day:
            continue
        prev = daily[row["unit"]].get(day)
        if prev is None or int(row.get("id") or 0) > int(prev.get("id") or 0):
            daily[row["unit"]][day] = row
    result = {}
    for unit, by_day in daily.items():
        result[unit] = [
            {
                "date": day,
                "total_gb": _to_float(item.get("total_gb")),
                "quota_gb": _to_float(item.get("quota_gb")),
                "usage_pct": _to_float(item.get("usage_pct")),
                "status": item.get("status") or "NORMAL",
            }
            for day, item in sorted(by_day.items())
        ]
    return result


def dashboard_data(days: int = 7) -> dict:
    days = 30 if days == 30 else 7
    cfg = _config()
    refresh_seconds = int(cfg.get("dashboard", {}).get("refresh_seconds", 60))
    if not DB_PATH.exists():
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "days": days,
            "refresh_seconds": refresh_seconds,
            "summary": {"units": 0, "total_usage_gb": 0, "total_quota_gb": 0, "total_overage_gb": 0, "projected_overage_gb": 0, "at_risk": 0, "last_collection": None},
            "units": [],
            "history": {},
        }

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        latest_raw = [dict(r) for r in _latest_rows(conn)]
        history = _history_rows(conn, days)

    # v0.9.1: recalcula ritmo/projecao ao servir o dashboard. Isso corrige
    # snapshots antigos que tenham sido gravados com uma formula anterior.
    if latest_raw:
        lookback = int(cfg.get("history", {}).get("lookback_days", 90))
        hist_by_unit = get_history_by_units([r.get("unit") for r in latest_raw], lookback_days=lookback)
        latest_raw = apply_historical_analytics(latest_raw, cfg, hist_by_unit)
    latest = [_display_row(r) for r in latest_raw]

    total_usage = sum(r["total_gb"] for r in latest)
    total_quota = sum(r["quota_gb"] for r in latest)
    total_overage = sum(r["overage_gb"] for r in latest)
    projected_overage = sum(r["projected_overage_gb"] for r in latest)
    at_risk = sum(1 for r in latest if str(r.get("forecast_risk") or "").upper() in {"ESTOURADO", "ESTOURO PREVISTO", "RISCO ALTO"})
    last_collection = max((str(r.get("collected_at") or "") for r in latest), default=None) or None
    period_start = min((str(r.get("period_start")) for r in latest if r.get("period_start")), default=None)
    period_end = max((str(r.get("period_end")) for r in latest if r.get("period_end")), default=None)
    max_age = max((int(r.get("data_age_days") or 0) for r in latest), default=0)
    stale_units = sum(1 for r in latest if str(r.get("data_freshness") or "").upper() == "DESATUALIZADO")

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "refresh_seconds": refresh_seconds,
        "summary": {
            "units": len(latest),
            "total_usage_gb": total_usage,
            "total_quota_gb": total_quota,
            "total_overage_gb": total_overage,
            "projected_overage_gb": projected_overage,
            "at_risk": at_risk,
            "last_collection": last_collection,
            "period_start": period_start,
            "period_end": period_end,
            "max_data_age_days": max_age,
            "stale_units": stale_units,
        },
        "units": latest,
        "history": history,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "StarlinkDashboard/0.9.1"

    def _common_headers(self, content_type: str, content_length: int | None = None, cache: str = "no-store"):
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))

    def _send_bytes(self, payload: bytes, content_type: str, status: int = 200, cache: str = "no-store"):
        self.send_response(status)
        self._common_headers(content_type, len(payload), cache)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, obj: dict, status: int = 200):
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status=status)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            path = STATIC / "index.html"
            self._send_bytes(path.read_bytes(), "text/html; charset=utf-8", cache="no-cache")
            return
        if parsed.path == "/api/dashboard":
            params = parse_qs(parsed.query)
            try:
                days = int((params.get("days") or ["7"])[0])
            except Exception:
                days = 7
            self._send_json(dashboard_data(days))
            return
        if parsed.path == "/health":
            self._send_json({"status": "ok", "version": "0.9.1", "db_exists": DB_PATH.exists()})
            return
        if parsed.path == "/logo.png":
            logo = ASSETS / "logo.png"
            if logo.exists():
                self._send_bytes(logo.read_bytes(), "image/png", cache="public, max-age=300")
            else:
                self._send_bytes(b"", "image/png", status=404)
            return
        self._send_json({"error": "not found"}, status=404)

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)


def run():
    init_db()
    cfg = _config()
    dash = cfg.get("dashboard", {})
    host = str(dash.get("host", "127.0.0.1"))
    port = int(dash.get("port", 8787))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    httpd = ThreadingHTTPServer((host, port), Handler)
    logger.info("Dashboard Starlink v0.9.1 em http://%s:%s", host, port)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning("Dashboard exposto fora do localhost. Restrinja o acesso por firewall/VLAN; esta versao nao implementa autenticacao web.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
