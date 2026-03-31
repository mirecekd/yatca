#!/bin/bash
set -euo pipefail

A0_HEALTH_URL="${A0_HEALTH_URL:-http://127.0.0.1/health}"
WAIT_TIMEOUT="${YATCA_A0_WAIT_TIMEOUT:-300}"
CHECK_INTERVAL="${YATCA_A0_CHECK_INTERVAL:-2}"
PYTHON_BIN="/opt/venv/bin/python3"
PIP_BIN="/opt/venv/bin/pip"
BRIDGE_FILE="/a0/usr/workdir/telegram_bridge.py"

log() {
  echo "[YATCA] $*"
}

log "Ensuring Python dependencies are installed..."
"$PIP_BIN" install -q python-telegram-bot aiohttp python-dotenv >/tmp/yatca_pip.log 2>&1 || {
  cat /tmp/yatca_pip.log
  exit 1
}

log "Waiting for A0 health at $A0_HEALTH_URL ..."
start_ts=$(date +%s)
while true; do
  if curl -fsS --max-time 3 "$A0_HEALTH_URL" >/dev/null 2>&1; then
    log "A0 is healthy. Starting telegram bridge..."
    break
  fi
  now_ts=$(date +%s)
  elapsed=$((now_ts - start_ts))
  if [ "$elapsed" -ge "$WAIT_TIMEOUT" ]; then
    log "Timed out after ${WAIT_TIMEOUT}s waiting for A0 health."
    exit 1
  fi
  sleep "$CHECK_INTERVAL"
done

exec "$PYTHON_BIN" "$BRIDGE_FILE"
