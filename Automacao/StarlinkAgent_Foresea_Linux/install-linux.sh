#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Execute como root: sudo ./install-linux.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
SERVICE_USER="${SERVICE_USER:-starlinkagent}"
SECRETS_DIR="/etc/starlink-agent"
BROWSER_DIR="$INSTALL_DIR/.playwright"
PROFILE_FILE="$INSTALL_DIR/runtime-profile.env"

log() { printf '%s\n' "$*"; }
fail() { printf 'ERRO: %s\n' "$*" >&2; exit 1; }

version_ge() {
  # Uso: version_ge 3.10 3.9
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

# -----------------------------------------------------------------------------
# 1) Detectar sistema operacional
# -----------------------------------------------------------------------------
[[ -r /etc/os-release ]] || fail "/etc/os-release nao encontrado. Distribuicao nao suportada."
# shellcheck disable=SC1091
. /etc/os-release
OS_ID="${ID:-unknown}"
OS_VERSION="${VERSION_ID:-unknown}"
OS_NAME="${PRETTY_NAME:-$OS_ID $OS_VERSION}"
ARCH="$(uname -m)"

case "$OS_ID" in
  ubuntu|debian) ;;
  *) fail "Distribuicao '$OS_NAME' nao suportada por este instalador. Use Ubuntu/Debian." ;;
esac

command -v apt-get >/dev/null 2>&1 || fail "apt-get nao encontrado."

log "=== Foresea Starlink Agent v0.8.2 - Instalacao Linux adaptativa ==="
log "Origem: $SOURCE_DIR"
log "Destino: $INSTALL_DIR"
log "Usuario de servico: $SERVICE_USER"
log "OS detectado: $OS_NAME"
log "Arquitetura: $ARCH"

# -----------------------------------------------------------------------------
# 2) Dependencias base do sistema e Python padrao da distribuicao
# -----------------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl

PYTHON_BIN="$(command -v python3)"
PY_VERSION="$($PYTHON_BIN -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_FULL="$($PYTHON_BIN --version 2>&1)"
log "Python detectado: $PY_FULL ($PYTHON_BIN)"

version_ge "$PY_VERSION" "3.8" || fail "Python $PY_VERSION nao suportado. Minimo: Python 3.8."

# -----------------------------------------------------------------------------
# 3) Escolher perfil por OS e, secundariamente, pela versao real do Python
# -----------------------------------------------------------------------------
REQ_FILE=""
PROFILE=""
PROFILE_REASON=""

if [[ "$OS_ID" == "ubuntu" && "$OS_VERSION" == 20.04* ]]; then
  PROFILE="legacy"
  REQ_FILE="requirements-linux-legacy.txt"
  PROFILE_REASON="Ubuntu 20.04 (Focal): perfil compativel com Python 3.8/Playwright 1.48"
elif [[ "$OS_ID" == "debian" && "$OS_VERSION" == 11* ]]; then
  PROFILE="legacy"
  REQ_FILE="requirements-linux-legacy.txt"
  PROFILE_REASON="Debian 11: perfil legado para maior compatibilidade do Chromium"
elif [[ "$OS_ID" == "ubuntu" && ( "$OS_VERSION" == 22.04* || "$OS_VERSION" == 24.04* ) ]]; then
  if version_ge "$PY_VERSION" "3.9"; then
    PROFILE="modern"
    REQ_FILE="requirements-linux-modern.txt"
    PROFILE_REASON="Ubuntu $OS_VERSION com Python $PY_VERSION"
  else
    PROFILE="legacy"
    REQ_FILE="requirements-linux-legacy.txt"
    PROFILE_REASON="Ubuntu $OS_VERSION, mas Python $PY_VERSION exige perfil legado"
  fi
elif [[ "$OS_ID" == "debian" && "$OS_VERSION" == 12* ]]; then
  if version_ge "$PY_VERSION" "3.9"; then
    PROFILE="modern"
    REQ_FILE="requirements-linux-modern.txt"
    PROFILE_REASON="Debian 12 com Python $PY_VERSION"
  else
    PROFILE="legacy"
    REQ_FILE="requirements-linux-legacy.txt"
    PROFILE_REASON="Debian 12, mas Python $PY_VERSION exige perfil legado"
  fi
else
  # Ubuntu/Debian nao mapeado: usar a versao real do Python como fallback seguro.
  if version_ge "$PY_VERSION" "3.9"; then
    PROFILE="modern"
    REQ_FILE="requirements-linux-modern.txt"
    PROFILE_REASON="OS nao mapeado explicitamente; selecionado por Python $PY_VERSION"
  else
    PROFILE="legacy"
    REQ_FILE="requirements-linux-legacy.txt"
    PROFILE_REASON="OS nao mapeado explicitamente; Python $PY_VERSION requer perfil legado"
  fi
  log "AVISO: $OS_NAME nao possui perfil homologado explicitamente. Continuando com fallback '$PROFILE'."
fi

[[ -f "$SOURCE_DIR/$REQ_FILE" ]] || fail "Arquivo de requisitos ausente: $SOURCE_DIR/$REQ_FILE"
log "Perfil selecionado: $PROFILE"
log "Motivo: $PROFILE_REASON"
log "Requirements: $REQ_FILE"

# -----------------------------------------------------------------------------
# 4) Preparar usuario/pasta e copiar projeto
# -----------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [[ "$(readlink -f "$SOURCE_DIR")" != "$(readlink -f "$INSTALL_DIR")" ]]; then
  cp -a "$SOURCE_DIR/." "$INSTALL_DIR/"
fi
mkdir -p "$INSTALL_DIR/data/raw" "$INSTALL_DIR/database" "$INSTALL_DIR/logs/debug" "$INSTALL_DIR/output" "$INSTALL_DIR/assets" "$BROWSER_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Guardar perfil efetivamente usado para suporte/diagnostico.
cat > "$PROFILE_FILE" <<PROFILE
STARLINK_AGENT_VERSION=0.8.2
OS_ID=$OS_ID
OS_VERSION=$OS_VERSION
OS_NAME=$(printf '%q' "$OS_NAME")
ARCH=$ARCH
PYTHON_BIN=$PYTHON_BIN
PYTHON_VERSION=$PY_VERSION
DEPENDENCY_PROFILE=$PROFILE
REQUIREMENTS_FILE=$REQ_FILE
PROFILE_REASON=$(printf '%q' "$PROFILE_REASON")
PROFILE
chown "$SERVICE_USER:$SERVICE_USER" "$PROFILE_FILE"
chmod 640 "$PROFILE_FILE"

# -----------------------------------------------------------------------------
# 5) Virtualenv. Recriar automaticamente se foi criado com outra versao Python.
# -----------------------------------------------------------------------------
RECREATE_VENV=0
if [[ -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  VENV_PY_VERSION="$($INSTALL_DIR/.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  if [[ "$VENV_PY_VERSION" != "$PY_VERSION" ]]; then
    log "Virtualenv existente usa Python ${VENV_PY_VERSION:-desconhecido}; sistema usa $PY_VERSION. Recriando .venv."
    RECREATE_VENV=1
  fi
else
  RECREATE_VENV=1
fi

if [[ "$RECREATE_VENV" -eq 1 ]]; then
  rm -rf "$INSTALL_DIR/.venv"
  runuser -u "$SERVICE_USER" -- "$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
fi

runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
log "Instalando dependencias do perfil '$PROFILE'..."
runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/$REQ_FILE"

# Confirmar que os modulos criticos realmente foram instalados antes do Chromium.
runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import pandas, requests, openpyxl, reportlab, msal
from playwright.sync_api import sync_playwright
print("Dependencias Python: OK")
PY

# -----------------------------------------------------------------------------
# 6) Chromium/Playwright
# -----------------------------------------------------------------------------
log "Instalando dependencias de sistema do Chromium..."
"$INSTALL_DIR/.venv/bin/python" -m playwright install-deps chromium
mkdir -p "$BROWSER_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$BROWSER_DIR"
log "Instalando Chromium do Playwright em $BROWSER_DIR ..."
runuser -u "$SERVICE_USER" -- env PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" "$INSTALL_DIR/.venv/bin/python" -m playwright install chromium
chown -R "$SERVICE_USER:$SERVICE_USER" "$BROWSER_DIR"

# Smoke test headless simples: valida bibliotecas do SO + binario do Chromium.
log "Validando inicializacao headless do Chromium..."
runuser -u "$SERVICE_USER" -- env PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DIR" "$INSTALL_DIR/.venv/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<html><title>ok</title><body>ok</body></html>")
    assert page.title() == "ok"
    browser.close()
print("Chromium headless: OK")
PY

# -----------------------------------------------------------------------------
# 7) Configuracao e systemd
# -----------------------------------------------------------------------------
if [[ ! -f "$INSTALL_DIR/config.json" ]]; then
  cp "$INSTALL_DIR/config.example.json" "$INSTALL_DIR/config.json"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/config.json"
chmod 640 "$INSTALL_DIR/config.json"

install -d -m 0750 -o root -g "$SERVICE_USER" "$SECRETS_DIR"

render_unit() {
  local src="$1" dst="$2"
  sed -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" -e "s|@@SERVICE_USER@@|$SERVICE_USER|g" "$src" > "$dst"
  chmod 644 "$dst"
}
render_unit "$INSTALL_DIR/linux/systemd/starlink-agent.service.in" /etc/systemd/system/starlink-agent.service
render_unit "$INSTALL_DIR/linux/systemd/starlink-agent.timer.in" /etc/systemd/system/starlink-agent.timer
render_unit "$INSTALL_DIR/linux/systemd/starlink-dashboard.service.in" /etc/systemd/system/starlink-dashboard.service
systemctl daemon-reload

cat <<MSG

Instalacao base concluida.

AMBIENTE DETECTADO:
  OS:          $OS_NAME
  Arquitetura: $ARCH
  Python:      $PY_FULL
  Perfil:      $PROFILE
  Requisitos:  $REQ_FILE

O perfil tambem foi salvo em:
  $PROFILE_FILE

PROXIMOS PASSOS:
1) Ajuste $INSTALL_DIR/config.json
2) Configure segredos:
   sudo $INSTALL_DIR/configure-credentials.sh
3) Teste login headless:
   sudo $INSTALL_DIR/test-login-linux.sh
4) Teste agente:
   sudo systemctl start starlink-agent.service
   sudo journalctl -u starlink-agent.service -n 100 --no-pager
5) Habilite agendamento e dashboard:
   sudo $INSTALL_DIR/enable-services-linux.sh
6) Dashboard local: http://127.0.0.1:8787
7) Diagnostico do ambiente:
   sudo $INSTALL_DIR/diagnostico-linux.sh

Para mudar o horario padrao de 06:00, edite /etc/systemd/system/starlink-agent.timer
e execute: sudo systemctl daemon-reload && sudo systemctl restart starlink-agent.timer
MSG
