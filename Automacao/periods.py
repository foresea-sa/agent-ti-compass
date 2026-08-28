from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from pathlib import Path

_DATE_RE = re.compile(r"(20\d{2})[-_/](\d{2})[-_/](\d{2})")


def _parse_date_token(token: str) -> date | None:
    m = _DATE_RE.search(str(token or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_period(value: str | None = None, filename: str | Path | None = None) -> dict:
    """Parse a Compass date range from page label or exported CSV filename.

    Accepted examples:
      2026-08-01 - 2026-08-27
      Starlink_Fleet_Usage_x_2026-08-01_to_2026-08-27.csv
    """
    candidates = [str(value or "")]
    if filename:
        candidates.append(Path(filename).name)
    found: list[date] = []
    for text in candidates:
        for y, m, d in _DATE_RE.findall(text):
            try:
                found.append(date(int(y), int(m), int(d)))
            except ValueError:
                pass
        if len(found) >= 2:
            break
    if not found:
        return {"period_start": None, "period_end": None, "period_days": None, "period_label": str(value or "").strip()}
    start = found[0]
    end = found[1] if len(found) > 1 else found[0]
    if end < start:
        start, end = end, start
    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "period_days": (end - start).days + 1,
        "period_label": f"{start.isoformat()} - {end.isoformat()}",
    }


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_key(row: dict) -> str:
    payload = "|".join([
        str(row.get("unit") or ""),
        str(row.get("period_start") or ""),
        str(row.get("period_end") or ""),
        str(row.get("service_line") or ""),
        f"{float(row.get('quota_gb') or 0):.6f}",
        f"{float(row.get('total_gb') or 0):.6f}",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def effective_date(row: dict) -> date | None:
    value = row.get("period_end") or row.get("collected_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return _parse_date_token(str(value))
