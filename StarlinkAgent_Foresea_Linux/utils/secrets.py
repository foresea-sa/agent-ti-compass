from __future__ import annotations

import os
import platform
from pathlib import Path

SERVICE = "Foresea-Starlink-Agent"

_ENV_MAP = {
    "compass_username": "STARLINK_COMPASS_USERNAME",
    "compass_password": "STARLINK_COMPASS_PASSWORD",
    "graph_tenant_id": "STARLINK_GRAPH_TENANT_ID",
    "graph_client_id": "STARLINK_GRAPH_CLIENT_ID",
    "graph_client_secret": "STARLINK_GRAPH_CLIENT_SECRET",
}


def _load_explicit_env_file() -> dict[str, str]:
    """Read an env file only when STARLINK_SECRETS_FILE explicitly points to it.

    Linux systemd normally injects /etc/starlink-agent/secrets.env directly as
    environment variables, so this fallback is mainly useful for controlled
    manual tests. The project never auto-loads a local .env file.
    """
    path_value = os.environ.get("STARLINK_SECRETS_FILE", "").strip()
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace('\\\\', '\\')
        values[key] = value
    return values


def get_secret(name: str) -> str | None:
    env_name = _ENV_MAP.get(name)
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return value
        value = _load_explicit_env_file().get(env_name)
        if value:
            return value

    # Windows distribution stores secrets in Windows Credential Manager via keyring.
    if platform.system().lower() == "windows":
        try:
            import keyring
        except ImportError:
            return None
        try:
            return keyring.get_password(SERVICE, name)
        except Exception:
            return None
    return None


def secret_source() -> str:
    if os.environ.get("STARLINK_COMPASS_USERNAME") or os.environ.get("STARLINK_SECRETS_FILE"):
        return "variaveis de ambiente/arquivo de segredos"
    if platform.system().lower() == "windows":
        return "Windows Credential Manager"
    return "variaveis de ambiente do servico"
