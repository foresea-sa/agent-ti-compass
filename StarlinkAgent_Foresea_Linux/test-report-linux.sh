#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
SERVICE_USER="${SERVICE_USER:-starlinkagent}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo: sudo $INSTALL_DIR/test-report-linux.sh <csv> [--period \"Teste\"]" >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Uso: sudo $INSTALL_DIR/test-report-linux.sh <arquivo.csv> [--period \"Teste\"]" >&2
  exit 2
fi

CSV="$1"
shift
[[ -f "$CSV" ]] || { echo "CSV nao encontrado: $CSV" >&2; exit 3; }

exec runuser -u "$SERVICE_USER" -- \
  "$INSTALL_DIR/.venv/bin/python" \
  "$INSTALL_DIR/gerar_relatorio_teste.py" \
  "$CSV" "$@"
