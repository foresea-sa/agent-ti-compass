import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from utils.periods import parse_period, snapshot_key

DB_PATH = Path(__file__).resolve().parent / "starlink.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT NOT NULL,
    period TEXT,
    period_start TEXT,
    period_end TEXT,
    period_days INTEGER,
    fact_type TEXT,
    unit TEXT NOT NULL,
    source_name TEXT,
    source_file TEXT,
    source_sha256 TEXT,
    snapshot_key TEXT,
    terminal TEXT,
    kit_name TEXT,
    service_line TEXT,
    plan_name TEXT,
    quota_gb REAL,
    priority_gb REAL,
    booster_gb REAL,
    standard_gb REAL,
    overage_gb REAL,
    download_gb REAL,
    upload_gb REAL,
    total_gb REAL,
    remaining_gb REAL,
    usage_pct REAL,
    portal_usage_pct REAL,
    daily_avg_gb REAL,
    projected_gb REAL,
    status TEXT,
    rate_gb_day REAL,
    trend TEXT,
    history_points INTEGER,
    days_to_limit REAL,
    forecast_limit_date TEXT,
    cycle_start_date TEXT,
    cycle_end_date TEXT,
    projected_cycle_end_gb REAL,
    projected_overage_gb REAL,
    forecast_risk TEXT,
    projection_method TEXT,
    data_age_days INTEGER,
    data_freshness TEXT,
    forecast_confidence TEXT
);
"""

EXPECTED_COLUMNS = {
    "period": "TEXT", "period_start": "TEXT", "period_end": "TEXT", "period_days": "INTEGER", "fact_type": "TEXT",
    "source_name": "TEXT", "source_file": "TEXT", "source_sha256": "TEXT", "snapshot_key": "TEXT",
    "terminal": "TEXT", "kit_name": "TEXT", "service_line": "TEXT", "plan_name": "TEXT",
    "quota_gb": "REAL", "priority_gb": "REAL", "booster_gb": "REAL", "standard_gb": "REAL",
    "overage_gb": "REAL", "download_gb": "REAL", "upload_gb": "REAL", "total_gb": "REAL",
    "remaining_gb": "REAL", "usage_pct": "REAL", "portal_usage_pct": "REAL", "daily_avg_gb": "REAL",
    "projected_gb": "REAL", "status": "TEXT", "rate_gb_day": "REAL", "trend": "TEXT",
    "history_points": "INTEGER", "days_to_limit": "REAL", "forecast_limit_date": "TEXT",
    "cycle_start_date": "TEXT", "cycle_end_date": "TEXT", "projected_cycle_end_gb": "REAL",
    "projected_overage_gb": "REAL", "forecast_risk": "TEXT", "projection_method": "TEXT",
    "data_age_days": "INTEGER", "data_freshness": "TEXT", "forecast_confidence": "TEXT",
}

INSERT_COLUMNS = [
    "collected_at", "period", "period_start", "period_end", "period_days", "fact_type", "unit", "source_name",
    "source_file", "source_sha256", "snapshot_key", "terminal", "kit_name", "service_line", "plan_name",
    "quota_gb", "priority_gb", "booster_gb", "standard_gb", "overage_gb", "download_gb", "upload_gb",
    "total_gb", "remaining_gb", "usage_pct", "portal_usage_pct", "daily_avg_gb", "projected_gb", "status",
    "rate_gb_day", "trend", "history_points", "days_to_limit", "forecast_limit_date", "cycle_start_date",
    "cycle_end_date", "projected_cycle_end_gb", "projected_overage_gb", "forecast_risk", "projection_method",
    "data_age_days", "data_freshness", "forecast_confidence",
]


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(SCHEMA)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(usage_history)").fetchall()}
        for name, sql_type in EXPECTED_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE usage_history ADD COLUMN {name} {sql_type}")
        # v0.9.7 self-healing migration: older live collections could trust a
        # stale page label over the actual exported filename. If source_file says
        # 01->28 but the row was stored as 28->28, correct the metadata before
        # analytics classify it as DAILY.
        conn.row_factory = sqlite3.Row
        legacy_rows = conn.execute(
            "SELECT * FROM usage_history WHERE source_file IS NOT NULL AND source_file <> ''"
        ).fetchall()
        repaired = 0
        removed_duplicates = 0
        for raw in legacy_rows:
            row = dict(raw)
            meta = parse_period(None, row.get("source_file"))
            ps, pe = meta.get("period_start"), meta.get("period_end")
            if not ps or not pe:
                continue
            expected_days = int(meta.get("period_days") or 0)
            current = (str(row.get("period_start") or ""), str(row.get("period_end") or ""), int(row.get("period_days") or 0))
            expected = (ps, pe, expected_days)
            expected_type = "DAILY" if expected_days == 1 else "INTERVAL"
            if current == expected and str(row.get("fact_type") or "") == expected_type:
                continue
            fixed = dict(row)
            fixed.update({
                "period": meta.get("period_label"),
                "period_start": ps,
                "period_end": pe,
                "period_days": expected_days,
                "fact_type": expected_type,
            })
            new_key = snapshot_key(fixed)
            duplicate = conn.execute(
                "SELECT id FROM usage_history WHERE snapshot_key=? AND id<>? LIMIT 1",
                (new_key, row["id"]),
            ).fetchone()
            if duplicate:
                conn.execute("DELETE FROM usage_history WHERE id=?", (row["id"],))
                removed_duplicates += 1
            else:
                conn.execute(
                    """UPDATE usage_history
                       SET period=?, period_start=?, period_end=?, period_days=?, fact_type=?, snapshot_key=?
                       WHERE id=?""",
                    (meta.get("period_label"), ps, pe, expected_days, expected_type, new_key, row["id"]),
                )
                repaired += 1

        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_history_unit_date ON usage_history(unit, collected_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_history_unit_period_end ON usage_history(unit, period_end)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_history_fact_type ON usage_history(fact_type, period_end)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_history_snapshot_key ON usage_history(snapshot_key) WHERE snapshot_key IS NOT NULL")
        conn.commit()


def insert_rows(rows):
    placeholders = ", ".join(["?"] * len(INSERT_COLUMNS))
    sql = f"INSERT OR IGNORE INTO usage_history ({', '.join(INSERT_COLUMNS)}) VALUES ({placeholders})"
    values = [[r.get(col) for col in INSERT_COLUMNS] for r in rows]
    with sqlite3.connect(DB_PATH) as conn:
        before = conn.total_changes
        conn.executemany(sql, values)
        conn.commit()
        return conn.total_changes - before


def get_history_by_units(units, lookback_days=90):
    """Return one latest snapshot per unit/effective day.

    v0.8 prefers Compass period_end as the effective observation date. This prevents a
    manually imported old CSV from being treated as if its data belonged to today.
    """
    units = [str(u) for u in units if u]
    if not units:
        return {}
    cutoff = (datetime.now() - timedelta(days=int(lookback_days))).date().isoformat()
    marks = ",".join(["?"] * len(units))
    query = f"""
        SELECT id, collected_at, period, period_start, period_end, period_days, unit, quota_gb, total_gb,
               usage_pct, portal_usage_pct, priority_gb, booster_gb, standard_gb, status,
               source_file, source_sha256, data_freshness
        FROM usage_history
        WHERE unit IN ({marks})
          AND COALESCE(period_end, substr(collected_at,1,10)) >= ?
        ORDER BY unit, COALESCE(period_end, substr(collected_at,1,10)), collected_at, id
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        fetched = [dict(r) for r in conn.execute(query, [*units, cutoff]).fetchall()]

    daily = defaultdict(dict)
    for row in fetched:
        day = str(row.get("period_end") or row.get("collected_at") or "")[:10]
        existing = daily[row["unit"]].get(day)
        if not existing or (str(row.get("collected_at") or ""), int(row.get("id") or 0)) > (str(existing.get("collected_at") or ""), int(existing.get("id") or 0)):
            daily[row["unit"]][day] = row
    return {unit: sorted(days.values(), key=lambda r: str(r.get("period_end") or r.get("collected_at") or "")) for unit, days in daily.items()}
