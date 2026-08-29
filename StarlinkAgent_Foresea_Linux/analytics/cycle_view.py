from __future__ import annotations

import calendar
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from analytics.trends import billing_cycle


def _f(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _d(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def _status(pct: float, overage: float, thresholds: dict) -> str:
    if overage > 0 or pct >= float(thresholds.get("emergency", 95)):
        return "EMERGENCIA"
    if pct >= float(thresholds.get("critical", 85)):
        return "CRITICO"
    if pct >= float(thresholds.get("warning", 70)):
        return "ATENCAO"
    return "NORMAL"


def _risk(current: float, quota: float, projected: float | None, forecast: date | None, cycle_end: date) -> str:
    if quota <= 0:
        return "SEM FRANQUIA"
    if current >= quota:
        return "ESTOURADO"
    if projected is None:
        return "SEM PREVISAO"
    if forecast and forecast <= cycle_end:
        return "ESTOURO PREVISTO"
    if projected >= quota * 0.95:
        return "RISCO ALTO"
    if projected >= quota * 0.85:
        return "RISCO MODERADO"
    return "CONTROLADO"


def _cycle_start_day(unit: str, projection: dict) -> int:
    by_unit = projection.get("cycle_start_day_by_unit", {}) or {}
    value = by_unit.get(unit, projection.get("cycle_start_day", 1))
    try:
        return min(max(int(value or 1), 1), 28)
    except Exception:
        return 1


def _daily_trend(values: list[tuple[date, float]], window: int = 3) -> str:
    if len(values) < 4:
        return "SEM HISTORICO" if len(values) < 2 else "ESTAVEL"
    window = max(int(window or 3), 2)
    nums = [v for _, v in values]
    recent = nums[-window:]
    prior = nums[-2 * window:-window]
    if not prior:
        prior = nums[:-window]
    if not prior:
        return "ESTAVEL"
    r = sum(recent) / len(recent)
    p = sum(prior) / len(prior)
    if p <= 0:
        return "ACELERANDO" if r > 0 else "ESTAVEL"
    ratio = r / p
    if ratio >= 1.20:
        return "ACELERANDO"
    if ratio <= 0.80:
        return "DESACELERANDO"
    return "ESTAVEL"


def _rate(values: list[tuple[date, float]], recent_days: int) -> tuple[float | None, str, int]:
    if not values:
        return None, "AGUARDANDO HISTORICO", 0
    window = max(int(recent_days or 7), 1)
    sample = values[-window:]
    return sum(v for _, v in sample) / len(sample), "MEDIA DIARIA REAL", len(values)


def _confidence(points: int, coverage: float, age_days: int) -> str:
    if age_days > 2:
        return "BAIXA"
    if points >= 7 and coverage >= 0.70:
        return "ALTA"
    if points >= 3:
        return "MEDIA"
    if points >= 1:
        return "BAIXA"
    return "SEM DADOS"




def _daterange(start: date, end: date) -> list[date]:
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _infer_missing_daily(by_day: dict[date, dict], records: list[dict], cycle_start: date) -> dict[date, dict]:
    """Infer a missing single day from an overlapping interval when unambiguous.

    Example: daily 26/08 exists and Compass also has 26/08-27/08. If 27/08 is
    missing, daily_27 = interval_26_27 - daily_26. This is useful when the portal
    default export spans two days. No inference is made when more than one day is
    missing from the interval.
    """
    changed = True
    while changed:
        changed = False
        for r in records:
            ps, pe = _d(r.get("period_start")), _d(r.get("period_end"))
            if not ps or not pe or ps == pe or pe < cycle_start:
                continue
            days = [d for d in _daterange(max(ps, cycle_start), pe)]
            missing = [d for d in days if d not in by_day]
            if len(missing) != 1:
                continue
            known = sum(_f(by_day[d].get("total_gb")) for d in days if d in by_day)
            interval_total = _f(r.get("total_gb"))
            inferred = interval_total - known
            # Small negative differences can be caused by portal rounding. Large
            # negatives indicate the interval is not safely decomposable.
            if inferred < -1.0:
                continue
            inferred = max(inferred, 0.0)
            d = missing[0]
            synthetic = dict(r)
            synthetic.update({
                "period_start": d.isoformat(), "period_end": d.isoformat(),
                "period_days": 1, "total_gb": inferred,
                "source_name": str(r.get("source_name") or "") + " [INFERIDO]",
                "source_file": str(r.get("source_file") or "") + "#inferred",
                "inferred_daily": True,
            })
            by_day[d] = synthetic
            changed = True
    return by_day

def _latest_metadata(records: list[dict]) -> dict:
    if not records:
        return {}
    return max(records, key=lambda r: (str(r.get("period_end") or ""), str(r.get("collected_at") or ""), int(r.get("id") or 0)))


def _choose_authoritative_range(records: list[dict], cycle_start: date, latest_daily: date | None) -> dict | None:
    candidates = []
    for r in records:
        ps, pe = _d(r.get("period_start")), _d(r.get("period_end"))
        days = int(r.get("period_days") or 0)
        if not ps or not pe or days <= 1:
            continue
        if str(r.get("fact_type") or "INTERVAL").upper() == "DAILY":
            continue
        # Prefer cycle-to-date exports. A range starting before cycle_start is also
        # accepted only when cycle_start is day 1 and the range begins on day 1.
        start_ok = ps == cycle_start
        if not start_ok:
            continue
        if latest_daily and pe < latest_daily:
            continue
        candidates.append(r)
    if not candidates:
        return None
    return max(candidates, key=lambda r: (_d(r.get("period_end")) or date.min, int(r.get("period_days") or 0), int(r.get("id") or 0)))


def load_records(db_path: Path, lookback_days: int = 120) -> list[dict]:
    if not Path(db_path).exists():
        return []
    cutoff = (datetime.now().date() - timedelta(days=max(int(lookback_days), 30))).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM usage_history
            WHERE COALESCE(period_end, substr(collected_at,1,10)) >= ?
            ORDER BY unit, period_end, collected_at, id
            """,
            [cutoff],
        ).fetchall()
    return [dict(r) for r in rows]


def build_cycle_view(records: list[dict], config: dict, now: date | None = None) -> tuple[list[dict], dict[str, list[dict]]]:
    """Build one correct cycle-to-date row per unit from interval CSV records.

    Compass Fleet Usage exports are interval totals. A file 2026-08-02_to_2026-08-02
    is consumption for that day, not a cumulative snapshot. Multi-day exports are
    interval aggregates and must never be mixed into a daily time series as if they
    were point-in-time cumulative values.
    """
    system_today = now or datetime.now().date()
    thresholds = config.get("thresholds", {})
    projection = config.get("projection", {})
    hist_cfg = config.get("history", {})
    recent_days = int(hist_cfg.get("rate_recent_days", 7))
    recent_window = int(hist_cfg.get("recent_window_days", 3))
    max_age = int(hist_cfg.get("max_data_age_days", 2))

    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("unit"):
            grouped[str(r["unit"])].append(r)

    result: list[dict] = []
    histories: dict[str, list[dict]] = {}

    for unit, recs in grouped.items():
        # Determine reference from the newest interval we have for the unit.
        newest_day = max((_d(r.get("period_end")) for r in recs if _d(r.get("period_end"))), default=system_today)
        cycle_start, cycle_end = billing_cycle(newest_day, _cycle_start_day(unit, projection))
        cycle_records = [r for r in recs if (_d(r.get("period_end")) or date.min) >= cycle_start]
        if not cycle_records:
            continue

        # Exact one-day exports are the source of truth for daily rate/trend.
        by_day: dict[date, dict] = {}
        for r in cycle_records:
            ps, pe = _d(r.get("period_start")), _d(r.get("period_end"))
            if not ps or not pe or ps != pe or int(r.get("period_days") or 1) != 1:
                continue
            if str(r.get("fact_type") or "DAILY").upper() not in {"", "DAILY"}:
                continue
            prev = by_day.get(pe)
            if prev is None or (str(r.get("collected_at") or ""), int(r.get("id") or 0)) > (str(prev.get("collected_at") or ""), int(prev.get("id") or 0)):
                by_day[pe] = r

        by_day = _infer_missing_daily(by_day, cycle_records, cycle_start)
        daily_values = sorted((d, _f(r.get("total_gb"))) for d, r in by_day.items())
        latest_daily = daily_values[-1][0] if daily_values else None
        authoritative = _choose_authoritative_range(cycle_records, cycle_start, latest_daily)

        daily_sum = sum(v for _, v in daily_values)
        if authoritative is not None:
            current_total = _f(authoritative.get("total_gb"))
            reference_day = _d(authoritative.get("period_end")) or newest_day
            total_method = "INTERVALO CICLO"
            meta = authoritative
        else:
            current_total = daily_sum
            reference_day = latest_daily or newest_day
            total_method = "SOMA DIARIA"
            meta = _latest_metadata(cycle_records)

        quota = _f(meta.get("quota_gb")) or max((_f(r.get("quota_gb")) for r in cycle_records), default=0.0)
        # Fallback quota configured per unit if portal data is absent.
        if quota <= 0:
            quota = _f((config.get("collection", {}).get("monthly_quota_gb", {}) or {}).get(unit))

        rate, rate_method, points = _rate(daily_values, recent_days)
        trend = _daily_trend(daily_values, recent_window)
        elapsed_calendar = max((reference_day - cycle_start).days + 1, 1)
        coverage = min(points / elapsed_calendar, 1.0) if elapsed_calendar else 0.0

        remaining_days = max((cycle_end - reference_day).days, 0)
        projected = current_total + rate * remaining_days if rate is not None else None
        overage = max(current_total - quota, 0.0) if quota > 0 else 0.0
        remaining = max(quota - current_total, 0.0) if quota > 0 else 0.0
        pct = current_total / quota * 100.0 if quota > 0 else 0.0
        projected_overage = max((projected or 0) - quota, 0.0) if projected is not None and quota > 0 else 0.0
        if quota > 0 and current_total >= quota:
            days_to_limit, forecast = 0.0, reference_day
        elif quota > 0 and rate is not None and rate > 0:
            days_to_limit = remaining / rate
            forecast = reference_day + timedelta(days=max(math.ceil(days_to_limit), 0))
        else:
            days_to_limit, forecast = None, None

        age = max((system_today - reference_day).days, 0)
        freshness = "ATUAL" if age <= max_age else "DESATUALIZADO"
        confidence = _confidence(points, coverage, age)
        status = _status(pct, overage, thresholds)
        risk = _risk(current_total, quota, projected, forecast, cycle_end) if bool(projection.get("enabled", True)) else "DESABILITADO"

        # Cumulative history reconstructed only from one-day interval exports.
        cumulative = 0.0
        history_series = []
        for d, value in daily_values:
            cumulative += value
            history_series.append({
                "date": d.isoformat(),
                "daily_gb": value,
                "total_gb": cumulative,
                "quota_gb": quota,
                "usage_pct": (cumulative / quota * 100.0) if quota else 0.0,
            })
        # If a cycle-to-date interval is newer/more complete, append or replace the
        # final cumulative point while preserving daily-derived rate/trend.
        if authoritative is not None:
            if history_series and history_series[-1]["date"] == reference_day.isoformat():
                history_series[-1]["total_gb"] = current_total
                history_series[-1]["usage_pct"] = pct
            elif not history_series or history_series[-1]["date"] < reference_day.isoformat():
                history_series.append({
                    "date": reference_day.isoformat(), "daily_gb": None,
                    "total_gb": current_total, "quota_gb": quota, "usage_pct": pct,
                })

        histories[unit] = history_series
        row = dict(meta)
        row.update({
            "unit": unit,
            "period": f"{cycle_start.isoformat()} - {reference_day.isoformat()}",
            "period_start": cycle_start.isoformat(), "period_end": reference_day.isoformat(),
            "period_days": elapsed_calendar,
            "quota_gb": quota, "total_gb": current_total,
            "remaining_gb": remaining, "overage_gb": overage,
            "usage_pct": pct, "portal_usage_pct": pct,
            "status": status,
            "rate_gb_day": rate, "daily_avg_gb": rate,
            "trend": trend, "history_points": points,
            "days_to_limit": days_to_limit,
            "forecast_limit_date": forecast.isoformat() if forecast else None,
            "cycle_start_date": cycle_start.isoformat(), "cycle_end_date": cycle_end.isoformat(),
            "projected_cycle_end_gb": projected, "projected_gb": projected,
            "projected_overage_gb": projected_overage,
            "forecast_risk": risk,
            "projection_method": f"{total_method} + {rate_method}",
            "data_age_days": age, "data_freshness": freshness,
            "forecast_confidence": confidence,
            "history_series": history_series,
            "daily_coverage_pct": coverage * 100.0,
            "daily_points": points,
        })
        result.append(row)

    result.sort(key=lambda r: (-_f(r.get("usage_pct")), str(r.get("unit") or "")))
    return result, histories
