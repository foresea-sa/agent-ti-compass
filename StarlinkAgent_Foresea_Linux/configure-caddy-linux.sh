#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Execute como root: sudo /opt/starlink-agent/configure-caddy-linux.sh" >&2
  exit 1
fi

INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
CONFIG="$INSTALL_DIR/config.json"
PROFILE_DIR="/etc/starlink-agent"
PROFILE="$PROFILE_DIR/caddy-profile.env"
CADDYFILE="/etc/caddy/Caddyfile"
BACKUP_DIR="/etc/caddy/backup"
CERT_DIR="/etc/caddy/certs"

log(){ printf '%s\n' "$*"; }
fail(){ printf 'ERRO: %s\n' "$*" >&2; exit 1; }
version_ge(){ [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]; }

[[ -f "$CONFIG" ]] || fail "$CONFIG nao encontrado. Instale o Starlink Agent primeiro."

log "=== Starlink Agent - Configuracao HTTPS com Caddy ==="
log "Arquitetura: cliente -> HTTPS :443 -> Caddy -> 127.0.0.1:8787"

# Instalar Caddy usando o gerenciador nativo da distribuicao.
if ! command -v caddy >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || fail "apt-get nao encontrado; instale Caddy manualmente."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  if ! apt-get install -y caddy; then
    fail "Nao foi possivel instalar o pacote 'caddy' via apt. Configure o repositorio Caddy aprovado pela sua empresa e execute novamente."
  fi
fi

CADDY_VERSION_RAW="$(caddy version 2>/dev/null || true)"
CADDY_VERSION="$(printf '%s' "$CADDY_VERSION_RAW" | sed -nE 's/^v?([0-9]+\.[0-9]+\.[0-9]+).*/\1/p')"
[[ -n "$CADDY_VERSION" ]] || fail "Nao foi possivel identificar a versao do Caddy: $CADDY_VERSION_RAW"
AUTH_DIRECTIVE="basicauth"
if version_ge "$CADDY_VERSION" "2.8.0"; then AUTH_DIRECTIVE="basic_auth"; fi
log "Caddy detectado: $CADDY_VERSION_RAW (diretiva de auth: $AUTH_DIRECTIVE)"

# Forcar backend somente em localhost. Faz backup antes.
cp -a "$CONFIG" "$CONFIG.before-caddy.$(date +%Y%m%d_%H%M%S).bak"
"$INSTALL_DIR/.venv/bin/python" - "$CONFIG" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); cfg=json.loads(p.read_text())
d=cfg.setdefault('dashboard',{})
d['host']='127.0.0.1'; d['port']=8787; d['reverse_proxy']='caddy'; d['public_https_port']=443
d.setdefault('consolidated_path','/analise-consolidada')
d['consolidated_api_path']='/api/consolidated-dashboard'
p.write_text(json.dumps(cfg,indent=2,ensure_ascii=False)+'\n')
PY
chown starlinkagent:starlinkagent "$CONFIG" 2>/dev/null || true
chmod 640 "$CONFIG"
systemctl restart starlink-dashboard.service 2>/dev/null || true
sleep 1
if ! curl -fsS --max-time 10 http://127.0.0.1:8787/health >/dev/null; then
  fail "Dashboard nao responde em 127.0.0.1:8787/health. Corrija o dashboard antes de habilitar Caddy."
fi

DEFAULT_HOST="$(hostname -f 2>/dev/null || hostname)"
read -r -p "FQDN HTTPS [$DEFAULT_HOST]: " HOST
HOST="${HOST:-$DEFAULT_HOST}"
HOST="${HOST#http://}"; HOST="${HOST#https://}"; HOST="${HOST%%/*}"
[[ "$HOST" =~ ^[A-Za-z0-9.-]+$ ]] || fail "FQDN invalido: $HOST"

read -r -p "Habilitar redirect HTTP :80 -> HTTPS :443? [n]: " REDIRECT_CHOICE
REDIRECT_CHOICE="${REDIRECT_CHOICE:-n}"
AUTO_HTTPS_GLOBAL="{\n    auto_https disable_redirects\n}\n\n"
REDIRECT_ENABLED="false"
if [[ "$REDIRECT_CHOICE" =~ ^[SsYy]$ ]]; then
  AUTO_HTTPS_GLOBAL=""
  REDIRECT_ENABLED="true"
fi

log ""
log "Modo TLS:"
log "  1 - Caddy Internal CA (recomendado para primeiro teste; distribuir Root CA via GPO)"
log "  2 - Certificado corporativo existente (CRT/PEM + chave privada)"
read -r -p "Escolha [1]: " TLS_CHOICE
TLS_CHOICE="${TLS_CHOICE:-1}"

TLS_BLOCK=""
TLS_MODE=""
CERT_SOURCE=""
KEY_SOURCE=""
if [[ "$TLS_CHOICE" == "1" ]]; then
  TLS_MODE="internal"
  TLS_BLOCK="    tls internal"
elif [[ "$TLS_CHOICE" == "2" ]]; then
  TLS_MODE="corporate"
  read -r -p "Caminho do certificado CRT/PEM: " CERT_SOURCE
  read -r -p "Caminho da chave privada KEY/PEM: " KEY_SOURCE
  [[ -f "$CERT_SOURCE" ]] || fail "Certificado nao encontrado: $CERT_SOURCE"
  [[ -f "$KEY_SOURCE" ]] || fail "Chave nao encontrada: $KEY_SOURCE"
  install -d -m 0750 -o root -g caddy "$CERT_DIR"
  install -m 0644 -o root -g caddy "$CERT_SOURCE" "$CERT_DIR/starlink-dashboard.crt"
  install -m 0640 -o root -g caddy "$KEY_SOURCE" "$CERT_DIR/starlink-dashboard.key"
  TLS_BLOCK="    tls $CERT_DIR/starlink-dashboard.crt $CERT_DIR/starlink-dashboard.key"
else
  fail "Opcao TLS invalida."
fi

DEFAULT_USER="infraestrutura"
read -r -p "Usuario para Analise Consolidada [$DEFAULT_USER]: " ADMIN_USER
ADMIN_USER="${ADMIN_USER:-$DEFAULT_USER}"
[[ "$ADMIN_USER" =~ ^[A-Za-z0-9._@-]+$ ]] || fail "Usuario de Basic Auth contem caracteres nao suportados."

while true; do
  read -r -s -p "Senha para Analise Consolidada: " ADMIN_PASS; echo
  [[ -n "$ADMIN_PASS" ]] || { echo "A senha nao pode ser vazia."; continue; }
  read -r -s -p "Repita a senha: " ADMIN_PASS2; echo
  [[ "$ADMIN_PASS" == "$ADMIN_PASS2" ]] && break
  echo "As senhas nao coincidem. Tente novamente."
done

HASH="$(caddy hash-password --algorithm bcrypt --plaintext "$ADMIN_PASS" 2>/dev/null || caddy hash-password --plaintext "$ADMIN_PASS" 2>/dev/null || true)"
unset ADMIN_PASS ADMIN_PASS2
[[ -n "$HASH" ]] || fail "Nao foi possivel gerar o hash bcrypt com 'caddy hash-password'."

install -d -m 0750 -o root -g caddy /etc/caddy
install -d -m 0750 -o root -g caddy "$BACKUP_DIR"
if [[ -f "$CADDYFILE" ]]; then
  cp -a "$CADDYFILE" "$BACKUP_DIR/Caddyfile.$(date +%Y%m%d_%H%M%S).bak"
fi

CONSOLIDATED_PATH="$("$INSTALL_DIR/.venv/bin/python" - "$CONFIG" <<'PY'
import json,sys
cfg=json.load(open(sys.argv[1])); print(cfg.get('dashboard',{}).get('consolidated_path','/analise-consolidada'))
PY
)"
[[ "$CONSOLIDATED_PATH" == /* ]] || CONSOLIDATED_PATH="/$CONSOLIDATED_PATH"

if [[ -n "$AUTO_HTTPS_GLOBAL" ]]; then
  printf '%b' "$AUTO_HTTPS_GLOBAL" > "$CADDYFILE"
else
  : > "$CADDYFILE"
fi
cat >> "$CADDYFILE" <<EOF
$HOST {
$TLS_BLOCK

    encode zstd gzip

    # A capa e os dashboards individuais ficam disponiveis na rede interna.
    # A pagina consolidada e seu endpoint dedicado exigem autenticacao.
    @consolidado path ${CONSOLIDATED_PATH}* /api/consolidated-dashboard*
    $AUTH_DIRECTIVE @consolidado {
        $ADMIN_USER $HASH
    }

    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "no-referrer"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        -Server
    }

    reverse_proxy 127.0.0.1:8787

    log {
        output file /var/log/caddy/starlink-dashboard-access.log
    }
}
EOF
chown root:caddy "$CADDYFILE"
chmod 0640 "$CADDYFILE"

log "Validando Caddyfile..."
caddy validate --config "$CADDYFILE" --adapter caddyfile
systemctl daemon-reload
systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy
sleep 2

install -d -m 0750 -o root -g starlinkagent "$PROFILE_DIR"
cat > "$PROFILE" <<EOF
CADDY_HOST=$HOST
CADDY_TLS_MODE=$TLS_MODE
CADDY_VERSION=$CADDY_VERSION
CADDY_AUTH_DIRECTIVE=$AUTH_DIRECTIVE
CADDY_CONSOLIDATED_PATH=$CONSOLIDATED_PATH
CADDY_CONSOLIDATED_USER=$ADMIN_USER
CADDY_HTTP_REDIRECT=$REDIRECT_ENABLED
CADDY_CONFIGURED_AT=$(date -Is)
EOF
chown root:starlinkagent "$PROFILE"
chmod 0640 "$PROFILE"

# Validar localmente mesmo antes de DNS corporativo estar pronto.
COVER_CODE="$(curl -k -sS --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$HOST/" || true)"
ADMIN_CODE="$(curl -k -sS --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$HOST$CONSOLIDATED_PATH" || true)"
HEALTH_CODE="$(curl -k -sS --resolve "$HOST:443:127.0.0.1" -o /dev/null -w '%{http_code}' "https://$HOST/health" || true)"

log ""
log "Caddy configurado."
log "  HTTPS:       https://$HOST/"
log "  Unidade:     https://$HOST/unidade/HTQ"
log "  Consolidado: https://$HOST$CONSOLIDATED_PATH (Basic Auth)"
log "  Backend:     http://127.0.0.1:8787"
log "  Testes locais: capa=$COVER_CODE health=$HEALTH_CODE consolidado_sem_auth=$ADMIN_CODE"

if [[ "$COVER_CODE" != "200" || "$HEALTH_CODE" != "200" ]]; then
  log "AVISO: Capa/health nao retornaram HTTP 200. Execute: sudo $INSTALL_DIR/diagnostico-caddy-linux.sh"
fi
if [[ "$ADMIN_CODE" != "401" ]]; then
  log "AVISO: Consolidado sem credenciais deveria retornar 401, mas retornou $ADMIN_CODE."
fi

if [[ "$TLS_MODE" == "internal" ]]; then
  log ""
  log "TLS Internal CA ativo. Para exportar somente o certificado RAIZ publico (nunca a chave privada):"
  log "  sudo $INSTALL_DIR/export-caddy-root-ca-linux.sh"
  log "Depois distribua o .crt via GPO/PKI corporativa para remover alertas nos navegadores."
fi

log ""
if [[ "$REDIRECT_ENABLED" == "true" ]]; then
  log "Firewall: liberar TCP/443 e TCP/80 (redirect HTTP -> HTTPS ativo)."
else
  log "Firewall: liberar somente TCP/443. Redirect na porta 80 esta desabilitado."
fi
log "Nao exponha TCP/8787; o Dashboard permanece em 127.0.0.1."
