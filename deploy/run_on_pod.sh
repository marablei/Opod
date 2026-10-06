#!/usr/bin/env bash
# Run the pipeline on the pod. Extra args go to the orchestrator.
#
#   bash deploy/run_on_pod.sh --limit 1      # 1-video test
#   bash deploy/run_on_pod.sh                # everything not yet done
#   bash deploy/run_on_pod.sh --fg ...       # stay in the foreground
#
# Runs in the BACKGROUND by default (survives your SSH/web-terminal closing).
# All progress is mirrored to Drive, so even if the pod dies you just start a
# new one, run setup_runpod.sh, then this same command again.
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

FG=0
if [ "${1:-}" = "--fg" ]; then FG=1; shift; fi
[ -x "$PY" ] || die "venv missing — run deploy/setup_runpod.sh first."

bash "$REPO_DIR/deploy/start_server.sh"

export ORION_OMNI_MODE=live
export ORION_OMNI_ENDPOINT="http://127.0.0.1:$PORT/v1/chat/completions"
export ORION_OMNI_MODEL="$SERVED_NAME"
export ORION_DRIVE_REMOTE="${ORION_DRIVE_REMOTE:-}"
if [ -z "$ORION_DRIVE_REMOTE" ]; then
  log "WARNING: ORION_DRIVE_REMOTE is empty — nothing will be saved to Drive. If this pod dies you lose everything. Set it in $ENV_FILE."
else
  log "checking Drive access + input bundles ..."
  "$PY" "$REPO_DIR/deploy/drive_check.py" || die "Drive check failed — fix before spending GPU time."
fi

CMD=("$PY" "$REPO_DIR/orchestrator/run_pipeline.py" --input "$INPUT_DIR" --out "$OUTPUT_DIR" "$@")
if [ "$FG" = 1 ]; then
  "${CMD[@]}"
else
  nohup "${CMD[@]}" >> "$RUN_LOG" 2>&1 &
  echo $! > "$WORKSPACE/orion_run.pid"
  log "pipeline started in background (pid $(cat "$WORKSPACE/orion_run.pid"))"
  echo "  follow log : tail -f $RUN_LOG"
  echo "  status     : $PY $REPO_DIR/deploy/status.py"
  echo "  stop       : kill \$(cat $WORKSPACE/orion_run.pid)   (safe: resumes later)"
fi
