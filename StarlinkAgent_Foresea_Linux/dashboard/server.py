from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from analytics.cycle_view import build_cycle_view, load_records
from database.db import init_db
from reports.executive_report import generate_pdf

BASE = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
DB_PATH = BASE / "database" / "starlink.db"
CONFIG_PATH = BASE / "config.json"
FALLBACK_CONFIG_PATH = BASE / "config.example.json"
ASSETS = BASE / "assets"
PDF_DIR = BASE / "output" / "dashboard_pdf"
logger = logging.getLogger("starlink-dashboard")


def _config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else FALLBACK_CONFIG_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _consolidated_path() -> str:
    value = str(_config().get("dashboard", {}).get("consolidated_path", "/analise-consolidada")).strip()
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/") or "/analise-consolidada"


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _display_row(row: dict) -> dict:
    keys = {
        "unit", "collected_at", "period", "period_start", "period_end", "period_days", "source_name", "source_file",
        "terminal", "kit_name", "service_line", "plan_name", "recommended_action",
        "quota_gb", "priority_gb", "booster_gb", "standard_gb", "overage_gb", "total_gb", "remaining_gb",
        "usage_pct", "portal_usage_pct", "status", "rate_gb_day", "trend", "history_points", "days_to_limit",
        "forecast_limit_date", "cycle_start_date", "cycle_end_date", "projected_cycle_end_gb", "projected_overage_gb",
        "forecast_risk", "projection_method", "data_age_days", "data_freshness", "forecast_confidence", "daily_coverage_pct", "daily_points",
    }
    data = {k: row.get(k) for k in keys}
    for key in [
        "quota_gb", "priority_gb", "booster_gb", "standard_gb", "overage_gb", "total_gb", "remaining_gb",
        "usage_pct", "portal_usage_pct", "projected_overage_gb", "data_age_days", "daily_coverage_pct", "daily_points",
    ]:
        data[key] = _to_float(data.get(key))
    for key in ["rate_gb_day", "days_to_limit", "projected_cycle_end_gb"]:
        value = data.get(key)
        data[key] = None if value is None else _to_float(value)
    return data


def _cycle_data(days: int = 7):
    days = 30 if int(days) == 30 else 7
    cfg = _config()
    refresh = int(cfg.get("dashboard", {}).get("refresh_seconds", 60))
    if not DB_PATH.exists():
        return cfg, refresh, [], {}
    lookback = int(cfg.get("history", {}).get("lookback_days", 120))
    records = load_records(DB_PATH, lookback_days=lookback)
    rows, histories = build_cycle_view(records, cfg)
    cutoff = (datetime.now().date() - timedelta(days=max(days - 1, 0))).isoformat()
    filtered = {u: [p for p in series if str(p.get("date") or "") >= cutoff] for u, series in histories.items()}
    return cfg, refresh, rows, filtered


def dashboard_data(days: int = 7) -> dict:
    cfg, refresh_seconds, rows, history = _cycle_data(days)
    latest = [_display_row(r) for r in rows]
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
        "generated_at": datetime.now().isoformat(timespec="seconds"), "days": 30 if int(days) == 30 else 7, "refresh_seconds": refresh_seconds,
        "summary": {"units": len(latest), "total_usage_gb": total_usage, "total_quota_gb": total_quota,
                    "total_overage_gb": total_overage, "projected_overage_gb": projected_overage, "at_risk": at_risk,
                    "last_collection": last_collection, "period_start": period_start, "period_end": period_end,
                    "max_data_age_days": max_age, "stale_units": stale_units},
        "units": latest, "history": history,
    }


def unit_data(unit: str, days: int = 30) -> dict | None:
    _, refresh, rows, history = _cycle_data(days)
    match = next((r for r in rows if str(r.get("unit") or "").upper() == str(unit).upper()), None)
    if match is None:
        return None
    return {"generated_at": datetime.now().isoformat(timespec="seconds"), "refresh_seconds": refresh,
            "unit": _display_row(match), "history": history.get(str(match.get("unit")), [])}


def _unit_pdf(unit: str) -> tuple[Path, str] | None:
    cfg, _, rows, _ = _cycle_data(30)
    match = next((r for r in rows if str(r.get("unit") or "").upper() == str(unit).upper()), None)
    if match is None:
        return None
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(match.get("unit") or "unidade"))
    filename = f"Relatorio_Starlink_{safe}_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    path = PDF_DIR / filename
    generate_pdf([match], cfg, output_path=path)
    return path, filename


class Handler(BaseHTTPRequestHandler):
    server_version = "StarlinkDashboard/0.9.3"

    def _common_headers(self, content_type: str, content_length: int | None = None, cache: str = "no-store"):
        self.send_header("Content-Type", content_type); self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "SAMEORIGIN"); self.send_header("Referrer-Policy", "no-referrer")
        if content_length is not None: self.send_header("Content-Length", str(content_length))

    def _send_bytes(self, payload: bytes, content_type: str, status: int = 200, cache: str = "no-store", disposition: str | None = None):
        self.send_response(status); self._common_headers(content_type, len(payload), cache)
        if disposition: self.send_header("Content-Disposition", disposition)
        self.end_headers(); self.wfile.write(payload)

    def _send_json(self, obj: dict, status: int = 200):
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status=status)

    def do_GET(self):
        parsed = urlparse(self.path); path = parsed.path.rstrip("/") or "/"
        if path in {"/", "/index.html"}:
            self._send_bytes((STATIC / "cover.html").read_bytes(), "text/html; charset=utf-8", cache="no-cache"); return
        if path.startswith("/unidade/"):
            self._send_bytes((STATIC / "unit.html").read_bytes(), "text/html; charset=utf-8", cache="no-cache"); return
        if path == _consolidated_path():
            self._send_bytes((STATIC / "consolidated.html").read_bytes(), "text/html; charset=utf-8", cache="no-cache"); return
        if path == "/api/dashboard":
            params = parse_qs(parsed.query)
            try: days = int((params.get("days") or ["7"])[0])
            except Exception: days = 7
            self._send_json(dashboard_data(days)); return
        if path == "/api/unit":
            params = parse_qs(parsed.query); unit = (params.get("unit") or [""])[0]
            try: days = int((params.get("days") or ["30"])[0])
            except Exception: days = 30
            data = unit_data(unit, days)
            if data is None: self._send_json({"error": "unit not found"}, status=404)
            else: self._send_json(data)
            return
        if path == "/api/unit-pdf":
            params = parse_qs(parsed.query); unit = unquote((params.get("unit") or [""])[0])
            try: result = _unit_pdf(unit)
            except Exception as exc:
                logger.exception("Falha ao gerar PDF da unidade %s", unit); self._send_json({"error": str(exc)}, status=500); return
            if result is None: self._send_json({"error": "unit not found"}, status=404); return
            pdf, filename = result
            self._send_bytes(pdf.read_bytes(), "application/pdf", cache="no-store", disposition=f'attachment; filename="{filename}"'); return
        if path == "/health":
            self._send_json({"status": "ok", "version": "0.9.3", "db_exists": DB_PATH.exists(), "consolidated_path": _consolidated_path()}); return
        if path == "/logo.png":
            logo = ASSETS / "logo.png"
            if logo.exists(): self._send_bytes(logo.read_bytes(), "image/png", cache="public, max-age=300")
            else: self._send_bytes(b"", "image/png", status=404)
            return
        self._send_json({"error": "not found"}, status=404)

    def log_message(self, fmt, *args): logger.info("%s - %s", self.address_string(), fmt % args)


def run():
    init_db(); cfg = _config(); dash = cfg.get("dashboard", {}); host = str(dash.get("host", "127.0.0.1")); port = int(dash.get("port", 8787))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    httpd = ThreadingHTTPServer((host, port), Handler)
    logger.info("Dashboard Starlink v0.9.3 em http://%s:%s", host, port)
    logger.info("Analise consolidada (nao exibida na navegacao): %s", _consolidated_path())
    if host not in {"127.0.0.1", "localhost", "::1"}: logger.warning("Dashboard exposto fora do localhost. Restrinja o acesso por firewall/VLAN; ocultar a rota consolidada nao substitui autenticacao.")
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally: httpd.server_close()


if __name__ == "__main__": run()
