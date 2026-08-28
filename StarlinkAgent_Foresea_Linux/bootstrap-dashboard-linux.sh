#!/usr/bin/env bash
set -u
BASE=/opt/starlink-agent
PY="$BASE/.venv/bin/python"
LOCK="$BASE/data/collection.lock"
LOG="$BASE/logs/dashboard-bootstrap-service.log"
mkdir -p "$BASE/data" "$BASE/logs"

if [[ ! -x "$PY" ]]; then
  echo "AVISO: Python do agente nao encontrado; dashboard iniciara sem carga inicial." | tee -a "$LOG"
  exit 0
fi

# O mesmo lock e usado pelo coletor diario para impedir duas sessoes Compass simultaneas.
/usr/bin/flock -w 360 "$LOCK" "$PY" "$BASE/bootstrap_dashboard.py" >>"$LOG" 2>&1
RC=$?
if [[ $RC -ne 0 ]]; then
  echo "AVISO: carga inicial falhou (rc=$RC). Dashboard sera iniciado mesmo assim. Consulte $LOG" | tee -a "$LOG"
fi
exit 0
