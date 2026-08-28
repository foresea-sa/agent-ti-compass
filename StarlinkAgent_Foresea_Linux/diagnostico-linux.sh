#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
BROWSER_DIR="$INSTALL_DIR/.playwright"

echo "=== Starlink Agent - Diagnostico Linux ==="
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  echo "OS: ${PRETTY_NAME:-${ID:-unknown} ${VERSION_ID:-}}"
fi
echo "Kernel: $(uname -srmo)"
echo "Arquitetura: $(uname -m)"
echo "Python sistema: $(python3 --version 2>&1 || true)"

if [[ -f "$INSTALL_DIR/runtime-profile.env" ]]; then
  echo
  echo "--- Perfil selecionado pelo instalador ---"
  cat "$INSTALL_DIR/runtime-profile.env"
fi

echo
if [[ -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  echo "Python virtualenv: $($INSTALL_DIR/.venv/bin/python --version 2>&1)"
  echo "Pip: $($INSTALL_DIR/.venv/bin/python -m pip --version)"
  echo
  echo "Pacotes principais:"
  "$INSTALL_DIR/.venv/bin/python" -m pip show playwright pandas openpyxl reportlab requests msal 2>/dev/null | grep -E '^(Name|Version):' || true
  echo
  echo "Teste de imports:"
  "$INSTALL_DIR/.venv/bin/python" - <<'PY'
mods = ["playwright", "pandas", "openpyxl", "reportlab", "requests", "msal"]
for mod in mods:
    try:
        __import__(mod)
        print(f"  OK   {mod}")
    except Exception as exc:
        print(f"  ERRO {mod}: {exc}")
PY
else
  echo "ERRO: virtualenv nao encontrado em $INSTALL_DIR/.venv"
fi

echo
echo "Browser dir: $BROWSER_DIR"
if [[ -d "$BROWSER_DIR" ]]; then
  du -sh "$BROWSER_DIR" 2>/dev/null || true
else
  echo "Nao criado."
fi

echo
echo "Systemd:"
for svc in starlink-agent.service starlink-agent.timer starlink-dashboard.service; do
  printf '  %-30s ' "$svc"
  systemctl is-enabled "$svc" 2>/dev/null || true
done
