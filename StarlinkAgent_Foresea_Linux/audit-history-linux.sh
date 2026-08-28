#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="/opt/starlink-agent"
SERVICE_USER="starlinkagent"
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then echo "Execute com sudo: sudo $0" >&2; exit 1; fi
runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/auditar_historico.py"
