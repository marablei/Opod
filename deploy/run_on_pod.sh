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

phase run_prepare "$*"
bash "$REPO_DIR/deploy/start_dashboard.sh" || true
bash "$REPO_DIR/deploy/start_server.sh"

export ORION_OMNI_MODE=live
export ORION_OMNI_ENDPOINT="http://127.0.0.1:$PORT/v1/chat/completions"
export ORION_OMNI_MODEL="$SERVED_NAME"
export ORION_DRIVE_REMOTE="${ORION_DRIVE_REMOTE:-}"
if ! drive_configured; then
  log "WARNING: no Drive settings — nothing will be saved to Drive. If this pod dies you lose everything. Set them in $ENV_FILE."
fi

# Last gate before spending GPU time: model server answering, Drive readable+writable, no second pipeline, videos present.
LIMIT_ARG=0; prev=""
for a in "$@"; do [ "$prev" = "--limit" ] && LIMIT_ARG="$a"; prev="$a"; done
python3 "$REPO_DIR/deploy/doctor.py" --stage run --limit "$LIMIT_ARG" || die "The doctor found problems (above). Fix the lines marked ✗ and run again."

CMD=("$PY" "$REPO_DIR/orchestrator/run_pipeline.py" --input "$INPUT_DIR" --out "$OUTPUT_DIR" "$@")
if [ "$FG" = 1 ]; then
  "${CMD[@]}"
else
  nohup "${CMD[@]}" >> "$RUN_LOG" 2>&1 &
  echo $! > "$WORKSPACE/orion_run.pid"
  phase pipeline_started "pid $(cat "$WORKSPACE/orion_run.pid")"
  log "pipeline started in background (pid $(cat "$WORKSPACE/orion_run.pid"))"
  echo "  follow log : tail -f $RUN_LOG"
  echo "  status     : $PY $REPO_DIR/deploy/status.py   (or the live dashboard link above)"
  echo "  stop       : kill \$(cat $WORKSPACE/orion_run.pid)   (safe: resumes later)"
fi
