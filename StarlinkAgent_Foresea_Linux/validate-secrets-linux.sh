#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
SERVICE_USER="${SERVICE_USER:-starlinkagent}"
SECRETS_DIR="/etc/starlink-agent"
SECRETS_FILE="$SECRETS_DIR/secrets.env"

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Execute com sudo." >&2; exit 1; }
[[ -f "$SECRETS_FILE" ]] || { echo "ERRO: $SECRETS_FILE nao existe." >&2; exit 1; }

printf 'Diretorio: '; stat -c '%A (%a) %U:%G %n' "$SECRETS_DIR"
printf 'Arquivo:    '; stat -c '%A (%a) %U:%G %n' "$SECRETS_FILE"

runuser -u "$SERVICE_USER" -- env STARLINK_SECRETS_FILE="$SECRETS_FILE" \
  "$INSTALL_DIR/.venv/bin/python" -c "from utils.secrets import get_secret; u=get_secret('compass_username'); p=get_secret('compass_password'); print('Usuario Compass carregado:', 'OK' if u else 'FALHOU'); print('Senha Compass carregada:', 'OK' if p else 'FALHOU'); raise SystemExit(0 if u and p else 2)"
