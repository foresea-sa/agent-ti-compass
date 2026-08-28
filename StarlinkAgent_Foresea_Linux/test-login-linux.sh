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
VERSION="$(tr -d '\r\n' < "$INSTALL_DIR/VERSION.txt" 2>/dev/null || echo desconhecida)"
COLLECTOR_SHA="$(sha256sum "$INSTALL_DIR/collectors/compass.py" 2>/dev/null | awk '{print $1}' || true)"
echo "Teste de login automatico do Compass."
echo "Versao implantada: $VERSION"
echo "Collector SHA-256: ${COLLECTOR_SHA:-indisponivel}"
echo "O navegador sera executado em modo headless; credenciais: variaveis de ambiente/arquivo de segredos."
echo "Uma gravacao compacta MP4 sera gerada em $INSTALL_DIR/logs/debug/videos/."
echo "Nenhuma senha sera exibida no console."
echo
runuser -u "$SERVICE_USER" -- env \
  STARLINK_SECRETS_FILE="$SECRETS_FILE" \
  STARLINK_HEADLESS_TEST=1 \
  STARLINK_RESET_SESSION=1 \
  STARLINK_RECORD_VIDEO=1 \
  PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/.playwright" \
  "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/bootstrap_login.py"
