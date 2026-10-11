#!/usr/bin/env bash
# Re-run ONLY the judge / rows / QA stages (D-F) for every video made by the new pipeline. No model calls: takes seconds, costs nothing.
# Use it after a validator fix (e.g. the beat-overlap repair) to rescue answers the old rules had dropped. Old V1 output is left alone.
#   bash /workspace/Opod/deploy/rejudge.sh
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
# 1) always run the newest code (then restart this script once so the new version is the one that runs)
if [ -z "${ORION_UPDATED:-}" ]; then
  ( cd "$REPO_DIR" && git pull -q --ff-only ) && log "code updated: $(cd "$REPO_DIR" && git log -1 --format=%s)" || log "could not update code; continuing with the current version"
  export ORION_UPDATED=1; exec bash "$0" "$@"
fi
# 2) if a pipeline run is still going, wait for it instead of failing
if [ -f "$WORKSPACE/orion_run.pid" ] && kill -0 "$(cat "$WORKSPACE/orion_run.pid")" 2>/dev/null; then
  log "a pipeline run is still going; waiting for it to finish (Ctrl+C cancels the wait only)"
  while kill -0 "$(cat "$WORKSPACE/orion_run.pid")" 2>/dev/null; do sleep 15; done
fi
ONLY=""
for d in "$OUTPUT_DIR"/*/; do
  n="$(basename "$d")"
  [ -f "$d/stage_c_omni/_progress.json" ] && ONLY="${ONLY:+$ONLY,}$n"
done
[ -n "$ONLY" ] || die "no video from the new pipeline found in $OUTPUT_DIR"
log "re-judging (stages D-F only): $ONLY"
exec bash "$REPO_DIR/deploy/run_on_pod.sh" --redo-from stage_d --only "$ONLY"
