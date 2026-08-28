#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then echo "Execute como root." >&2; exit 1; fi
systemctl disable --now caddy 2>/dev/null || true
echo "Caddy desabilitado. O Dashboard continua disponivel apenas localmente em http://127.0.0.1:8787."
echo "Para reabilitar: systemctl enable --now caddy"
