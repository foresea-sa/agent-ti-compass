#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
PROFILE="/etc/starlink-agent/caddy-profile.env"
echo "=== Diagnostico Caddy / Starlink Dashboard ==="
echo "Data: $(date -Is)"
echo "Host: $(hostname -f 2>/dev/null || hostname)"
APP_VERSION="$(tr -d '\r\n' < "$INSTALL_DIR/VERSION.txt" 2>/dev/null || echo desconhecida)"
echo "Starlink Agent: v$APP_VERSION"
echo
if command -v caddy >/dev/null 2>&1; then echo "Caddy: $(caddy version)"; else echo "Caddy: NAO INSTALADO"; fi
if [[ -f "$PROFILE" ]]; then
  echo "Perfil:"; sed -E 's/(PASSWORD|HASH|SECRET)=.*/\1=<oculto>/' "$PROFILE" | sed 's/^/  /'
else
  echo "Perfil: nao configurado ($PROFILE)"
fi
echo
echo "Servicos:"
systemctl --no-pager --full status caddy 2>/dev/null | sed -n '1,16p' || true
systemctl --no-pager --full status starlink-dashboard.service 2>/dev/null | sed -n '1,16p' || true
echo
echo "Portas:"
ss -lntp 2>/dev/null | grep -E '(:80 |:443 |:8787 )|(:80$|:443$|:8787$)' || true
echo
echo "Backend localhost:"
curl -fsS --max-time 5 http://127.0.0.1:8787/health || true; echo
if [[ -f /etc/caddy/Caddyfile ]]; then
  echo; echo "Validacao Caddyfile:"
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
fi
if [[ -f "$PROFILE" ]]; then
  # shellcheck disable=SC1090
  . "$PROFILE"
  H="${CADDY_HOST:-}"
  P="${CADDY_CONSOLIDATED_PATH:-/analise-consolidada}"
  if [[ -n "$H" ]]; then
    echo; echo "HTTPS local via --resolve:"
    for path in / /health "$P"; do
      code="$(curl -k -sS --resolve "$H:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$H$path" || true)"
      printf '  %-28s HTTP %s\n' "$path" "$code"
    done
  fi
fi
echo
echo "Logs recentes Caddy:"
journalctl -u caddy -n 30 --no-pager 2>/dev/null || true
