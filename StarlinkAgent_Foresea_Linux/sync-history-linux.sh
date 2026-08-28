#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="/opt/starlink-agent"
SERVICE_USER="starlinkagent"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Execute com sudo: sudo $0" >&2
  exit 1
fi

[[ -x "$INSTALL_DIR/.venv/bin/python" ]] || { echo "Virtualenv nao encontrado em $INSTALL_DIR/.venv" >&2; exit 1; }
[[ -f "$INSTALL_DIR/bootstrap_dashboard.py" ]] || { echo "bootstrap_dashboard.py nao encontrado." >&2; exit 1; }

echo "Sincronizando todos os CSVs de data/raw com o SQLite..."
runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/bootstrap_dashboard.py"

echo
echo "Reiniciando dashboard para aplicar analytics v0.9.1..."
systemctl restart starlink-dashboard.service 2>/dev/null || true

echo "Concluido. Atualize o navegador com Ctrl+F5."
