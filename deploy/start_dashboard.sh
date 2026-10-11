#!/usr/bin/env bash
# Start the live dashboard (read-only web page) in the background. Safe to run twice.
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
mkdir -p "$OUTPUT_DIR"
REV="$(cd "$REPO_DIR" && git rev-parse HEAD 2>/dev/null || echo none)"
if curl -sf -m 2 "http://127.0.0.1:$DASH_PORT/healthz" >/dev/null 2>&1 && [ "$(cat "$WORKSPACE/dashboard.rev" 2>/dev/null)" != "$REV" ]; then
  log "dashboard is running old code; restarting it"
  ps -eo pid,args | grep "[d]ashboard/server.py" | awk '{print $1}' | xargs -r kill || true
  sleep 2
fi
if curl -sf -m 2 "http://127.0.0.1:$DASH_PORT/healthz" >/dev/null 2>&1; then
  log "dashboard already running on port $DASH_PORT"
else
  nohup python3 "$REPO_DIR/dashboard/server.py" --out "$OUTPUT_DIR" --port "$DASH_PORT" > "$WORKSPACE/dashboard.log" 2>&1 &
  echo $! > "$WORKSPACE/dashboard.pid"
  echo "$REV" > "$WORKSPACE/dashboard.rev"
  sleep 1
fi
KEYQ=""; [ -n "${ORION_DASH_KEY:-}" ] && KEYQ="?key=$ORION_DASH_KEY"
echo "  LIVE DASHBOARD (open in your browser):"
if [ -n "${RUNPOD_POD_ID:-}" ]; then
  echo "    https://${RUNPOD_POD_ID}-${DASH_PORT}.proxy.runpod.net/$KEYQ"
  echo "    (works if HTTP port $DASH_PORT is listed under 'Expose HTTP ports' of the pod; otherwise use: python3 $REPO_DIR/deploy/status.py)"
else
  echo "    http://127.0.0.1:$DASH_PORT/$KEYQ"
fi
