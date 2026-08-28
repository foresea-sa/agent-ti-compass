from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta

from utils.periods import effective_date


def _month_shift(year: int, month: int, delta: int) -> tuple[int, int]:
    month0 = month - 1 + delta
    return year + month0 // 12, month0 % 12 + 1


def _safe_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(max(int(day), 1), calendar.monthrange(year, month)[1]))


def billing_cycle(reference: date, start_day: int) -> tuple[date, date]:
    start_day = min(max(int(start_day or 1), 1), 28)
    current_start = _safe_date(reference.year, reference.month, start_day)
    if reference >= current_start:
        start = current_start
        ny, nm = _month_shift(reference.year, reference.month, 1)
        next_start = _safe_date(ny, nm, start_day)
    else:
        py, pm = _month_shift(reference.year, reference.month, -1)
        start = _safe_date(py, pm, start_day)
        next_start = current_start
    return start, next_start - timedelta(days=1)


def _effective_day(item: dict) -> date | None:
    return effective_date(item)


def _dedupe_daily(history: list[dict], current: dict) -> list[dict]:
    by_day: dict[date, dict] = {}
    for item in [*history, current]:
        day = _effective_day(item)
        if not day:
            continue
        prev = by_day.get(day)
        if not prev or str(item.get("collected_at") or "") >= str(prev.get("collected_at") or ""):
            by_day[day] = dict(item)
    return [by_day[d] for d in sorted(by_day)]


def _trim_to_cycle_and_reset(series: list[dict], cycle_start: date, reset_tolerance_gb: float) -> list[dict]:
    cycle = [item for item in series if _effective_day(item) and _effective_day(item) >= cycle_start]
    if not cycle:
        return []
    reset_idx = 0
    prev = None
    for idx, item in enumerate(cycle):
        total = float(item.get("total_gb") or 0)
        if prev is not None and total < prev - reset_tolerance_gb:
            reset_idx = idx
        prev = total
    return cycle[reset_idx:]


def _linear_rate(series: list[dict]) -> float | None:
    if len(series) < 2:
        return None
    first_day = _effective_day(series[0])
    if not first_day:
        return None
    points = []
    for item in series:
        day = _effective_day(item)
        if not day:
            continue
        points.append((float((day - first_day).days), float(item.get("total_gb") or 0)))
    if len(points) < 2 or points[-1][0] <= points[0][0]:
        return None
    mx = sum(x for x, _ in points) / len(points)
    my = sum(y for _, y in points) / len(points)
    denom = sum((x - mx) ** 2 for x, _ in points)
    if denom <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in points) / denom
    return max(float(slope), 0.0)


def _interval_rates(series: list[dict]) -> list[float]:
    rates = []
    for a, b in zip(series, series[1:]):
        da, db = _effective_day(a), _effective_day(b)
        if not da or not db:
            continue
        days = (db - da).days
        if days <= 0:
            continue
        delta = float(b.get("total_gb") or 0) - float(a.get("total_gb") or 0)
        if delta >= 0:
            rates.append(delta / days)
    return rates


def _trend_label(series: list[dict], recent_window: int) -> str:
    rates = _interval_rates(series)
    if len(rates) < 2:
        return "SEM HISTORICO" if len(series) < 2 else "ESTAVEL"
    window = max(int(recent_window or 3), 2)
    recent = rates[-window:]
    prior = rates[-2 * window:-window]
    recent_avg = sum(recent) / len(recent)
    prior_avg = sum(prior) / len(prior) if prior else sum(rates[:-1]) / max(len(rates[:-1]), 1)
    if prior_avg <= 0:
        return "ACELERANDO" if recent_avg > 0 else "ESTAVEL"
    ratio = recent_avg / prior_avg
    if ratio >= 1.20:
        return "ACELERANDO"
    if ratio <= 0.80:
        return "DESACELERANDO"
    return "ESTAVEL"


def _forecast_risk(current_total: float, quota: float, projected: float | None, forecast_date: date | None, cycle_end: date) -> str:
    if quota <= 0:
        return "SEM FRANQUIA"
    if current_total >= quota:
        return "ESTOURADO"
    if forecast_date and forecast_date <= cycle_end:
        return "ESTOURO PREVISTO"
    projected = float(projected or 0)
    if projected >= quota * 0.95:
        return "RISCO ALTO"
    if projected >= quota * 0.85:
        return "RISCO MODERADO"
    return "CONTROLADO"


def _confidence(points: int, freshness: str, method: str) -> str:
    if freshness == "DESATUALIZADO":
        return "BAIXA"
    if method == "HISTORICO" and points >= 5:
        return "ALTA"
    if method == "HISTORICO" and points >= 2:
        return "MEDIA"
    return "BAIXA"


def apply_historical_analytics(rows: list[dict], config: dict, history_by_unit: dict[str, list[dict]]) -> list[dict]:
    hist_cfg = config.get("history", {})
    proj_cfg = config.get("projection", {})
    start_day = int(proj_cfg.get("cycle_start_day", 1))
    min_points = max(int(hist_cfg.get("min_points_for_history_rate", 2)), 2)
    recent_window = max(int(hist_cfg.get("recent_window_days", 3)), 2)
    reset_tolerance = float(hist_cfg.get("reset_tolerance_gb", 25))
    max_age = max(int(hist_cfg.get("max_data_age_days", 2)), 0)
    system_today = datetime.now().date()

    for row in rows:
        reference_day = _effective_day(row) or system_today
        cycle_start, cycle_end = billing_cycle(reference_day, start_day)
        history = history_by_unit.get(row.get("unit"), [])
        series = _trim_to_cycle_and_reset(_dedupe_daily(history, row), cycle_start, reset_tolerance)
        current_total = float(row.get("total_gb") or 0)
        quota = float(row.get("quota_gb") or 0)

        hist_rate = _linear_rate(series) if len(series) >= min_points else None
        period_days = int(row.get("period_days") or 0)
        period_start = row.get("period_start")
        if period_days > 0 and period_start:
            fallback_rate = current_total / period_days if current_total > 0 else 0.0
            fallback_method = "MEDIA DO PERIODO COMPASS"
        else:
            elapsed_days = max((reference_day - cycle_start).days + 1, 1)
            fallback_rate = current_total / elapsed_days if current_total > 0 else 0.0
            fallback_method = "MEDIA DO CICLO"

        if hist_rate is not None and hist_rate > 0:
            rate, method = hist_rate, "HISTORICO"
        else:
            rate, method = fallback_rate, fallback_method

        projection_enabled = bool(proj_cfg.get("enabled", True))
        remaining_days = max((cycle_end - reference_day).days, 0)
        projected_end = current_total + rate * remaining_days if projection_enabled else None
        projected_overage = max(float(projected_end or 0) - quota, 0.0) if projection_enabled and quota > 0 else 0.0
        remaining = max(quota - current_total, 0.0) if quota > 0 else 0.0

        if projection_enabled and quota > 0 and current_total >= quota:
            days_to_limit, forecast_date = 0.0, reference_day
        elif projection_enabled and quota > 0 and rate > 0:
            days_to_limit = remaining / rate
            forecast_date = reference_day + timedelta(days=max(math.ceil(days_to_limit), 0))
        else:
            days_to_limit, forecast_date = None, None

        trend = _trend_label(series, recent_window)
        risk = _forecast_risk(current_total, quota, projected_end, forecast_date, cycle_end) if projection_enabled else "DESABILITADO"
        age = max((system_today - reference_day).days, 0)
        freshness = "ATUAL" if age <= max_age else "DESATUALIZADO"
        confidence = _confidence(len(series), freshness, method)

        history_series = []
        for item in series:
            day = _effective_day(item)
            if not day:
                continue
            total = float(item.get("total_gb") or 0)
            item_quota = float(item.get("quota_gb") or quota or 0)
            history_series.append({
                "date": day.isoformat(), "total_gb": total, "quota_gb": item_quota,
                "usage_pct": (total / item_quota * 100.0) if item_quota else 0.0,
            })

        row.update({
            "rate_gb_day": rate, "daily_avg_gb": rate, "trend": trend, "history_points": len(series),
            "days_to_limit": days_to_limit, "forecast_limit_date": forecast_date.isoformat() if forecast_date else None,
            "cycle_start_date": cycle_start.isoformat(), "cycle_end_date": cycle_end.isoformat(),
            "projected_cycle_end_gb": projected_end, "projected_gb": projected_end,
            "projected_overage_gb": projected_overage, "forecast_risk": risk, "projection_method": method,
            "data_age_days": age, "data_freshness": freshness, "forecast_confidence": confidence,
            "history_series": history_series,
        })
    return rows
