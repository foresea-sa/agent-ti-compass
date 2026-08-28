#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Execute como root: sudo ./install-linux.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
SERVICE_USER="${SERVICE_USER:-starlinkagent}"
SECRETS_DIR="/etc/starlink-agent"
BROWSER_DIR="$INSTALL_DIR/.playwright"

echo "=== Foresea Starlink Agent v0.8 - Instalacao Linux ==="
echo "Origem: $SOURCE_DIR"
echo "Destino: $INSTALL_DIR"
echo "Usuario de servico: $SERVICE_USER"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl

mkdir -p "$INSTALL_DIR"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# Copy project without carrying runtime artifacts from a previous installation.
if [[ "$(readlink -f "$SOURCE_DIR")" != "$(readlink -f "$INSTALL_DIR")" ]]; then
  cp -a "$SOURCE_DIR/." "$INSTALL_DIR/"
fi
mkdir -p "$INSTALL_DIR/data/raw" "$INSTALL_DIR/database" "$INSTALL_DIR/logs/debug" "$INSTALL_DIR/output" "$INSTALL_DIR/assets" "$BROWSER_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  runuser -u "$SERVICE_USER" -- python3 -m venv "$INSTALL_DIR/.venv"
fi
runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements-linux.txt"

# System dependencies for Chromium are installed as root; browser binaries stay inside /opt.
"$INSTALL_DIR/.venv/bin/python" -m playwright install-deps chromium
chown -R "$SERVICE_USER:$SERVICE_USER" "$BROWSER_DIR"
runuser -u "$SERVICE_USER" -- env PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" "$INSTALL_DIR/.venv/bin/python" -m playwright install chromium

if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
  cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/config.json"
chmod 640 "$INSTALL_DIR/config.json"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

render_unit() {
  local src="$1" dst="$2"
  sed -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" -e "s|@@SERVICE_USER@@|$SERVICE_USER|g" "$src" > "$dst"
  chmod 644 "$dst"
}
render_unit "$INSTALL_DIR/linux/systemd/starlink-agent.service.in" /etc/systemd/system/starlink-agent.service
render_unit "$INSTALL_DIR/linux/systemd/starlink-agent.timer.in" /etc/systemd/system/starlink-agent.timer
render_unit "$INSTALL_DIR/linux/systemd/starlink-dashboard.service.in" /etc/systemd/system/starlink-dashboard.service
systemctl daemon-reload

cat <<MSG

Instalacao base concluida.

PROXIMOS PASSOS:
1) Ajuste $INSTALL_DIR/config.json
2) Configure segredos:
   sudo $INSTALL_DIR/credential_setup.py
3) Teste login headless:
   sudo $INSTALL_DIR/test-login-linux.sh
4) Teste agente:
   sudo systemctl start starlink-agent.service
   sudo journalctl -u starlink-agent.service -n 100 --no-pager
5) Habilite agendamento e dashboard:
   sudo $INSTALL_DIR/enable-services-linux.sh
6) Dashboard local: http://127.0.0.1:8787

Para mudar o horario padrao de 06:00, edite /etc/systemd/system/starlink-agent.timer
e execute: sudo systemctl daemon-reload && sudo systemctl restart starlink-agent.timer
MSG
