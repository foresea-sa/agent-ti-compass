#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/starlink-agent}"
VIDEO_DIR="$INSTALL_DIR/logs/debug/videos"
LAST="$(find "$VIDEO_DIR" -maxdepth 1 -type f \( -name '*.mp4' -o -name '*.webm' \) -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
if [[ -z "$LAST" ]]; then
  echo "Nenhum video encontrado em $VIDEO_DIR"
  exit 1
fi
ls -lh "$LAST"
echo "$LAST"
