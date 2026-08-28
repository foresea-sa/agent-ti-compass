#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Execute como root: sudo /opt/starlink-agent/configure-credentials.sh" >&2
  exit 1
fi

INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
SERVICE_USER="${SERVICE_USER:-starlinkagent}"
SECRETS_DIR="/etc/starlink-agent"
SECRETS_FILE="$SECRETS_DIR/secrets.env"

if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  echo "ERRO: virtualenv nao encontrado em $INSTALL_DIR/.venv" >&2
  echo "Execute primeiro: sudo $INSTALL_DIR/install-linux.sh" >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "ERRO: usuario de servico '$SERVICE_USER' nao existe." >&2
  echo "Execute primeiro o install-linux.sh." >&2
  exit 1
fi

install -d -m 0750 -o root -g "$SERVICE_USER" "$SECRETS_DIR"

export SERVICE_USER
export STARLINK_SECRETS_PATH="$SECRETS_FILE"
"$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/credential_setup.py"

chown root:"$SERVICE_USER" "$SECRETS_FILE"
chmod 0640 "$SECRETS_FILE"
chown root:"$SERVICE_USER" "$SECRETS_DIR"
chmod 0750 "$SECRETS_DIR"

echo
echo "Credenciais configuradas com sucesso."
echo "Arquivo: $SECRETS_FILE"
stat -c 'Permissoes: %A (%a)  Proprietario: %U  Grupo: %G' "$SECRETS_FILE"
