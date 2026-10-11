#!/usr/bin/env bash
# Re-run ONLY the judge / rows / QA stages (D-F) for every video made by the new pipeline. No model calls: takes seconds, costs nothing.
# Use it after a validator fix (e.g. the beat-overlap repair) to rescue answers the old rules had dropped. Old V1 output is left alone.
#   bash /workspace/Opod/deploy/rejudge.sh
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
ONLY=""
for d in "$OUTPUT_DIR"/*/; do
  n="$(basename "$d")"
  [ -f "$d/stage_c_omni/_progress.json" ] && ONLY="${ONLY:+$ONLY,}$n"
done
[ -n "$ONLY" ] || die "no video from the new pipeline found in $OUTPUT_DIR"
log "re-judging (stages D-F only): $ONLY"
exec bash "$REPO_DIR/deploy/run_on_pod.sh" --redo-from stage_d --only "$ONLY"
