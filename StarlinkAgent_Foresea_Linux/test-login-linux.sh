#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Execute: sudo ./test-login-linux.sh" >&2
  exit 1
fi
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
SERVICE_USER="${SERVICE_USER:-starlinkagent}"
SECRETS_FILE="/etc/starlink-agent/secrets.env"
if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "Segredos nao encontrados. Execute sudo $INSTALL_DIR/configure-credentials.sh" >&2
  exit 1
fi
runuser -u "$SERVICE_USER" -- env \
  STARLINK_SECRETS_FILE="$SECRETS_FILE" \
  STARLINK_HEADLESS_TEST=1 \
  STARLINK_RESET_SESSION=1 \
  PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/.playwright" \
  "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/bootstrap_login.py"
