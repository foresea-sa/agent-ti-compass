#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then echo "Execute com sudo." >&2; exit 1; fi
systemctl restart starlink-dashboard.service
systemctl --no-pager --full status starlink-dashboard.service || true
echo "Dashboard: http://127.0.0.1:8787"
