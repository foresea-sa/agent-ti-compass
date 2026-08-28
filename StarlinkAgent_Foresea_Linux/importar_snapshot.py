from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from agent import _recommended_action, enrich
from analytics.trends import apply_historical_analytics
from collectors.compass import CompassCollector
from database.db import get_history_by_units, init_db, insert_rows

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Importa um CSV Compass como snapshot historico auditavel no SQLite v0.9.1.")
    parser.add_argument("csv", help="CSV exportado de Starlink Fleet Usage")
    parser.add_argument("--period", default="", help="Periodo opcional. Normalmente e detectado pelo nome do arquivo.")
    args = parser.parse_args()

    cfg_path = BASE / "config.json"
    if not cfg_path.exists():
        cfg_path = BASE / "config.example.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logger = logging.getLogger("snapshot-import")

    init_db()
    rows = CompassCollector(cfg, logger)._parse_csv(Path(args.csv), args.period)
    rows = enrich(rows, cfg)
    history = get_history_by_units([r.get("unit") for r in rows], lookback_days=int(cfg.get("history", {}).get("lookback_days", 90)))
    rows = apply_historical_analytics(rows, cfg, history)
    for r in rows:
        r["recommended_action"] = _recommended_action(r)
    inserted = insert_rows(rows)
    period = next((r.get("period") for r in rows if r.get("period")), "-")
    print(f"Periodo Compass: {period}")
    print(f"Unidades lidas: {len(rows)}")
    print(f"Snapshots novos gravados: {inserted}")
    if inserted == 0:
        print("Nenhum registro novo: o snapshot ja existia no banco (deduplicacao v0.9.1).")


if __name__ == "__main__":
    main()
