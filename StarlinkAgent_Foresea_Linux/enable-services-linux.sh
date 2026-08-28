#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Execute: sudo ./enable-services-linux.sh" >&2
  exit 1
fi
if [[ ! -s /etc/starlink-agent/secrets.env ]]; then
  echo "Configure primeiro: sudo /opt/starlink-agent/configure-credentials.sh" >&2
  exit 1
fi
systemctl daemon-reload
systemctl enable --now starlink-agent.timer
systemctl enable --now starlink-dashboard.service
# Reinicia para aplicar ExecStartPre/carga inicial em upgrades.
systemctl restart starlink-dashboard.service
systemctl --no-pager --full status starlink-agent.timer || true
systemctl --no-pager --full status starlink-dashboard.service || true
if [[ -f /etc/starlink-agent/caddy-profile.env ]]; then
  . /etc/starlink-agent/caddy-profile.env
  echo "Dashboard HTTPS: https://${CADDY_HOST:-hostname}/"
else
  echo "Dashboard local: http://127.0.0.1:8787"
  echo "Para publicar em HTTPS :443: sudo /opt/starlink-agent/configure-caddy-linux.sh"
fi
