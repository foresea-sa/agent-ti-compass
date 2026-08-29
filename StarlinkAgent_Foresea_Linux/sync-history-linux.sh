#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="/opt/starlink-agent"
SERVICE_USER="starlinkagent"
DB="$INSTALL_DIR/database/starlink.db"
BACKUP_DIR="$INSTALL_DIR/database/backup"
MODE="${1:-}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Execute com sudo: sudo $0 [--rebuild]" >&2
  exit 1
fi

[[ -x "$INSTALL_DIR/.venv/bin/python" ]] || { echo "Virtualenv nao encontrado em $INSTALL_DIR/.venv" >&2; exit 1; }
[[ -f "$INSTALL_DIR/bootstrap_dashboard.py" ]] || { echo "bootstrap_dashboard.py nao encontrado." >&2; exit 1; }

if [[ -n "$MODE" && "$MODE" != "--rebuild" ]]; then
  echo "Uso: sudo $0 [--rebuild]" >&2
  exit 2
fi

if [[ "$MODE" == "--rebuild" ]]; then
  echo "Modo REBUILD: reconstruindo SQLite a partir de data/raw com o parser atual."
  mkdir -p "$BACKUP_DIR"
  if [[ -f "$DB" ]]; then
    STAMP="$(date +%Y%m%d_%H%M%S)"
    BACKUP="$BACKUP_DIR/starlink_before_v097_rebuild_${STAMP}.db"
    cp -a "$DB" "$BACKUP"
    echo "Backup criado: $BACKUP"
  fi
  systemctl stop starlink-dashboard.service 2>/dev/null || true
  rm -f "$DB"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/database"
fi

echo "Sincronizando todos os CSVs de data/raw com o SQLite..."
runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/bootstrap_dashboard.py"

echo
echo "Reiniciando dashboard para aplicar a analise historica atual..."
systemctl restart starlink-dashboard.service 2>/dev/null || true

echo "Concluido. Atualize o navegador com Ctrl+F5."
if [[ "$MODE" == "--rebuild" ]]; then
  echo "Rebuild concluido. Arquivos de intervalo continuam auditaveis, mas nao sao classificados como consumo diario."
fi
