from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
from analytics.cycle_view import build_cycle_view, load_records
from database.db import DB_PATH, init_db

BASE = Path(__file__).resolve().parent
cfg_path = BASE / 'config.json'
if not cfg_path.exists(): cfg_path = BASE / 'config.example.json'
cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
init_db()
records = load_records(DB_PATH, int(cfg.get('history',{}).get('lookback_days',120)))
counts=defaultdict(lambda:{'daily':0,'range':0})
for r in records:
    if int(r.get('period_days') or 0)==1:
        counts[r.get('unit')]['daily'] += 1
    else:
        counts[r.get('unit')]['range'] += 1
rows,_=build_cycle_view(records,cfg)
print('UNIDADE | DIARIOS | INTERVALOS | CONSUMO CICLO GB | GB/DIA | USO % | METODO')
print('-'*100)
for r in rows:
    c=counts[r['unit']]
    rate='--' if r.get('rate_gb_day') is None else f"{float(r['rate_gb_day']):.1f}"
    print(f"{r['unit']:7} | {c['daily']:7} | {c['range']:10} | {float(r.get('total_gb') or 0):15.1f} | {rate:>6} | {float(r.get('usage_pct') or 0):5.1f} | {r.get('projection_method')}")
