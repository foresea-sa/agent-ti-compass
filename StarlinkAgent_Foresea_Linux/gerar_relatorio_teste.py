from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from agent import _recommended_action, enrich
from analytics.cycle_view import build_cycle_view, load_records
from collectors.compass import CompassCollector
from database.db import DB_PATH, init_db
from reports.executive_report import generate_excel, generate_pdf

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Gera PDF/XLSX a partir de um CSV Compass, sem acessar o portal.")
    parser.add_argument("csv", help="Caminho para o CSV Starlink Fleet Usage")
    parser.add_argument("--period", default="", help="Rotulo do periodo; se omitido, tenta ler do nome do CSV")
    parser.add_argument("--ignore-db-history", action="store_true", help="Gera a previsao sem usar snapshots anteriores do SQLite")
    args = parser.parse_args()

    cfg_path = BASE / "config.json"
    if not cfg_path.exists():
        cfg_path = BASE / "config.example.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logger = logging.getLogger("report-test")
    parsed = CompassCollector(cfg, logger)._parse_csv(Path(args.csv), args.period)
    parsed = enrich(parsed, cfg)
    init_db()
    records = [] if args.ignore_db_history else load_records(
        DB_PATH, lookback_days=int(cfg.get("history", {}).get("lookback_days", 120))
    )
    # Add the test interval in memory without mutating SQLite.
    base_id = max([int(r.get("id") or 0) for r in records] or [0])
    for idx, r in enumerate(parsed, 1):
        rr = dict(r); rr["id"] = base_id + idx
        records.append(rr)
    rows, _ = build_cycle_view(records, cfg)
    for r in rows:
        r["recommended_action"] = _recommended_action(r)
    xlsx = generate_excel(rows, cfg)
    pdf = generate_pdf(rows, cfg)
    print(f"PDF:  {pdf}")
    print(f"XLSX: {xlsx}")


if __name__ == "__main__":
    main()
