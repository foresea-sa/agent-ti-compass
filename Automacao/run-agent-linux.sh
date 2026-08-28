#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then echo "Execute com sudo." >&2; exit 1; fi
systemctl start starlink-agent.service
systemctl --no-pager --full status starlink-agent.service || true
echo
echo "Logs recentes:"
journalctl -u starlink-agent.service -n 80 --no-pager
