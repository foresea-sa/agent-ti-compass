from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from agent import _recommended_action, enrich
from collectors.compass import CompassCollector
from collectors.starlink_api import StarlinkAPICollector
from database.db import DB_PATH, init_db, insert_rows

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
    # Mais antigo primeiro: se houver dois arquivos para o mesmo period_end,
    # o mais novo sera inserido depois e vencera a deduplicacao diaria.
    return sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name))


def process_rows(rows: list[dict], cfg: dict) -> int:
    rows = enrich(rows, cfg)
    # Store interval records as-is. Cycle analytics are built at read time.
    for row in rows:
        row["recommended_action"] = _recommended_action(row)
    return insert_rows(rows)


def import_raw_history(cfg: dict) -> int:
    candidates = raw_csv_candidates()
    if not candidates:
        return 0
    collector = CompassCollector(cfg, logger)
    inserted_total = 0
    logger.info("Sincronizando historico local: %s CSV(s) encontrado(s) em %s", len(candidates), RAW_DIR)
    for csv_path in candidates:
        try:
            rows = collector._parse_csv(csv_path, "")
            if not rows:
                logger.warning("CSV nao produziu unidades validas: %s", csv_path)
                continue
            inserted = process_rows(rows, cfg)
            inserted_total += inserted
            logger.info("CSV historico processado: %s | unidades=%s | snapshots_novos=%s", csv_path.name, len(rows), inserted)
        except Exception as exc:
            logger.warning("CSV local ignorado por erro (%s): %s", csv_path, exc)
    logger.info("Sincronizacao de data/raw concluida: snapshots_novos=%s | total_db=%s", inserted_total, snapshot_count())
    return inserted_total


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

    existing_before = snapshot_count()
    import_all_raw = bool(dash.get("bootstrap_import_all_raw_csvs", True))
    prefer_raw = bool(dash.get("bootstrap_prefer_latest_raw_csv", True))
    live_if_empty = bool(dash.get("bootstrap_live_collect_if_empty", True))

    try:
        # Sincroniza TODOS os CSVs de data/raw mesmo quando o DB ja possui
        # registros. A deduplicacao por snapshot_key torna esta operacao idempotente.
        if import_all_raw or (existing_before == 0 and prefer_raw):
            import_raw_history(cfg)

        if snapshot_count() == 0 and live_if_empty:
            seed_live(cfg)
    except Exception:
        logger.exception("Falha na carga inicial do dashboard. O painel sera iniciado mesmo assim.")
        return 2

    if snapshot_count() == 0:
        logger.warning("Carga inicial terminou sem snapshots. Dashboard sera iniciado vazio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
