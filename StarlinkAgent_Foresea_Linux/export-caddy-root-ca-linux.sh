#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
OUT="$INSTALL_DIR/output/caddy-root-ca.crt"
PROFILE="/etc/starlink-agent/caddy-profile.env"
if [[ ! -f "$PROFILE" ]]; then echo "Caddy nao configurado pelo agente." >&2; exit 1; fi
# shellcheck disable=SC1090
. "$PROFILE"
if [[ "${CADDY_TLS_MODE:-}" != "internal" ]]; then echo "TLS nao esta em modo internal; nao ha Root CA do Caddy para exportar." >&2; exit 1; fi
for src in \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt \
  /root/.local/share/caddy/pki/authorities/local/root.crt; do
  if [[ -f "$src" ]]; then
    install -m 0644 "$src" "$OUT"
    echo "Root CA publica exportada para: $OUT"
    echo "Distribua SOMENTE este .crt. Nao compartilhe root.key."
    exit 0
  fi
done
echo "Root CA nao encontrada. Reinicie o Caddy e execute novamente." >&2
exit 1
