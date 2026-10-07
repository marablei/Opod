#!/usr/bin/env bash
# Start (or confirm) the Qwen3-Omni vLLM server. Idempotent.
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

if server_healthy; then
  log "model server already up on port $PORT"
  exit 0
fi
[ -x "$PY" ] || die "venv not found at $VENV — run deploy/setup_runpod.sh first."

# Clear a stale process from an earlier launch.
if [ -f "$SERVER_PID_FILE" ] && kill -0 "$(cat "$SERVER_PID_FILE")" 2>/dev/null; then
  log "stopping stale server pid $(cat "$SERVER_PID_FILE")"
  kill "$(cat "$SERVER_PID_FILE")" 2>/dev/null || true
  sleep 5
fi

log "starting vLLM | profile=$MODEL_PROFILE | model=$MODEL_ID | gpus=$NGPU"
log "flags: $PROFILE_FLAGS $VLLM_EXTRA_ARGS"
# shellcheck disable=SC2086
nohup "$VENV/bin/vllm" serve "$MODEL_ID" \
  --served-model-name "$SERVED_NAME" \
  --host 127.0.0.1 --port "$PORT" \
  $PROFILE_FLAGS $VLLM_EXTRA_ARGS \
  > "$SERVER_LOG" 2>&1 &
echo $! > "$SERVER_PID_FILE"

WAIT="${ORION_SERVER_WAIT_SEC:-1800}"
START=$(date +%s)
while true; do
  if server_healthy; then
    log "model server is up (took $(( $(date +%s) - START ))s)"
    exit 0
  fi
  if ! kill -0 "$(cat "$SERVER_PID_FILE")" 2>/dev/null; then
    echo "---- last 40 lines of $SERVER_LOG ----" >&2
    tail -n 40 "$SERVER_LOG" >&2
    die "vLLM exited during startup. Common causes: out of memory (try lowering max-model-len via VLLM_EXTRA_ARGS, or a bigger GPU), or transformers not recognising qwen3_omni_moe."
  fi
  if [ $(( $(date +%s) - START )) -gt "$WAIT" ]; then
    tail -n 40 "$SERVER_LOG" >&2
    die "server not ready after ${WAIT}s."
  fi
  sleep 10
  log "waiting for model to load ... ($(( $(date +%s) - START ))s)"
done
