from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from collectors.compass import CompassCollector

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Valida um CSV exportado do Compass sem acessar o portal.")
    parser.add_argument("csv", help="Caminho para o CSV Starlink Fleet Usage")
    parser.add_argument("--period", default="arquivo manual", help="Rotulo opcional do periodo")
    args = parser.parse_args()

    cfg_path = BASE / "config.json"
    if not cfg_path.exists():
        cfg_path = BASE / "config.example.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    logger = logging.getLogger("validator")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())

    rows = CompassCollector(cfg, logger)._parse_csv(Path(args.csv), args.period)
    print("\nUNIDADE | FRANQUIA GB | CONSUMO GB | SALDO GB | OVERAGE GB | USO % | TERMINAL")
    print("-" * 110)
    for r in sorted(rows, key=lambda x: x.get("portal_usage_pct", 0), reverse=True):
        print(
            f"{r['unit']:7} | {r['quota_gb']:11.0f} | {r['total_gb']:10.0f} | "
            f"{r['remaining_gb']:8.0f} | {r['overage_gb']:10.0f} | "
            f"{r.get('portal_usage_pct', 0):6.2f}% | {r.get('terminal', '')}"
        )

if __name__ == "__main__":
    main()
