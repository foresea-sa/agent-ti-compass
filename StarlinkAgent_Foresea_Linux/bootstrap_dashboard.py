from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from agent import _recommended_action, enrich
from analytics.trends import apply_historical_analytics
from collectors.compass import CompassCollector
from collectors.starlink_api import StarlinkAPICollector
from database.db import DB_PATH, get_history_by_units, init_db, insert_rows

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
RAW_DIR = BASE / "data" / "raw"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "dashboard-bootstrap.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("starlink-dashboard-bootstrap")


def load_config() -> dict:
    path = CONFIG if CONFIG.exists() else BASE / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def snapshot_count() -> int:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT COUNT(*) FROM usage_history").fetchone()
        return int(row[0] if row else 0)


def raw_csv_candidates() -> list[Path]:
    if not RAW_DIR.exists():
        return []
    candidates = [p for p in RAW_DIR.glob("*.csv") if p.is_file()]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def process_rows(rows: list[dict], cfg: dict) -> int:
    rows = enrich(rows, cfg)
    lookback = int(cfg.get("history", {}).get("lookback_days", 90))
    history = get_history_by_units([r.get("unit") for r in rows], lookback_days=lookback)
    rows = apply_historical_analytics(rows, cfg, history)
    for row in rows:
        row["recommended_action"] = _recommended_action(row)
    return insert_rows(rows)


def seed_from_raw(cfg: dict) -> int:
    candidates = raw_csv_candidates()
    if not candidates:
        return 0
    collector = CompassCollector(cfg, logger)
    for csv_path in candidates:
        logger.info("Banco vazio. Tentando carga inicial pelo CSV local: %s", csv_path)
        try:
            rows = collector._parse_csv(csv_path, "")
            if not rows:
                logger.warning("CSV nao produziu unidades validas: %s", csv_path)
                continue
            inserted = process_rows(rows, cfg)
            logger.info("Carga inicial via CSV concluida: unidades=%s snapshots_novos=%s", len(rows), inserted)
            if inserted > 0 or snapshot_count() > 0:
                return inserted
        except Exception as exc:
            logger.warning("CSV local ignorado por erro (%s): %s", csv_path, exc)
    return 0


def seed_live(cfg: dict) -> int:
    mode = cfg.get("collection", {}).get("mode", "compass")
    logger.info("Nenhum snapshot utilizavel em data/raw. Iniciando coleta ao vivo (%s).", mode)
    collector = CompassCollector(cfg, logger) if mode == "compass" else StarlinkAPICollector(cfg, logger)
    rows = collector.collect()
    if not rows:
        logger.warning("Coleta ao vivo retornou zero unidades.")
        return 0
    inserted = process_rows(rows, cfg)
    logger.info("Carga inicial ao vivo concluida: unidades=%s snapshots_novos=%s", len(rows), inserted)
    return inserted


def main() -> int:
    cfg = load_config()
    dash = cfg.get("dashboard", {})
    if not bool(dash.get("bootstrap_on_start", True)):
        logger.info("Carga inicial do dashboard desabilitada em config.json.")
        return 0

    existing = snapshot_count()
    if existing > 0:
        logger.info("SQLite ja possui %s snapshot(s). Carga inicial nao e necessaria.", existing)
        return 0

    prefer_raw = bool(dash.get("bootstrap_prefer_latest_raw_csv", True))
    live_if_empty = bool(dash.get("bootstrap_live_collect_if_empty", True))

    try:
        if prefer_raw:
            inserted = seed_from_raw(cfg)
            if inserted > 0 or snapshot_count() > 0:
                return 0
        if live_if_empty:
            seed_live(cfg)
    except Exception:
        logger.exception("Falha na carga inicial do dashboard. O painel sera iniciado mesmo assim.")
        return 2

    if snapshot_count() == 0:
        logger.warning("Carga inicial terminou sem snapshots. Dashboard sera iniciado vazio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
