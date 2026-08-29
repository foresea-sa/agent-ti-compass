from __future__ import annotations

from pathlib import Path

_BASE = Path(__file__).resolve().parents[1]
_VERSION_FILE = _BASE / "VERSION.txt"


def get_version(default: str = "desconhecida") -> str:
    try:
        value = _VERSION_FILE.read_text(encoding="utf-8").strip()
        return value or default
    except Exception:
        return default


APP_VERSION = get_version()
