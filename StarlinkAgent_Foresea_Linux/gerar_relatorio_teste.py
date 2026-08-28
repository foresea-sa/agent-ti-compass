from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from agent import _recommended_action, enrich
from analytics.trends import apply_historical_analytics
from collectors.compass import CompassCollector
from database.db import get_history_by_units, init_db
from reports.executive_report import generate_excel, generate_pdf

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Gera PDF/XLSX v0.8 a partir de um CSV Compass, sem acessar o portal.")
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
    rows = CompassCollector(cfg, logger)._parse_csv(Path(args.csv), args.period)
    rows = enrich(rows, cfg)
    init_db()
    history = {} if args.ignore_db_history else get_history_by_units(
        [r.get("unit") for r in rows], lookback_days=int(cfg.get("history", {}).get("lookback_days", 90))
    )
    rows = apply_historical_analytics(rows, cfg, history)
    for r in rows:
        r["recommended_action"] = _recommended_action(r)
    xlsx = generate_excel(rows, cfg)
    pdf = generate_pdf(rows, cfg)
    print(f"PDF:  {pdf}")
    print(f"XLSX: {xlsx}")


if __name__ == "__main__":
    main()
