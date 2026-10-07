#!/usr/bin/env bash
# ONE command on a fresh RunPod pod: installs everything, downloads the model, starts it,
# smoke-tests it, then runs the pipeline. Safe to re-run (new pod, restarted pod, resume).
#
#   git clone <your-repo> /workspace/Orion_Pipeline && bash /workspace/Orion_Pipeline/go.sh --limit 1
#   bash /workspace/Orion_Pipeline/go.sh              # later: everything not yet done
#
# Needs your Drive settings first: either as pod environment variables, or in /workspace/orion.env
# (see deploy/orion.env.example). Arguments are passed to the pipeline (e.g. --limit 1).
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/deploy/_common.sh"
mkdir -p "$WORKSPACE"

# 1. Fail in seconds (not after a 20-minute install) if Drive is not configured.
if ! drive_configured && [ "${ORION_ALLOW_NO_DRIVE:-0}" != "1" ]; then
  die "Drive is not configured, so nothing would be saved if the pod dies.
  Set ORION_DRIVE_INPUT_ID / _STAGES_ID / _SFT_ID (or ORION_DRIVE_REMOTE) and RCLONE_CONFIG_GDRIVE_* as pod environment variables,
  or put them in $ENV_FILE (copy deploy/orion.env.example). Then run this again.
  (To run without Drive anyway: ORION_ALLOW_NO_DRIVE=1)"
fi
if drive_configured && drive_needs_rclone && [ -z "${RCLONE_CONFIG_GDRIVE_TOKEN:-}" ]; then
  die "Drive is configured but RCLONE_CONFIG_GDRIVE_TOKEN is empty. Paste the token from 'rclone authorize \"drive\"'."
fi

# 2. Install + model + server + smoke test (idempotent: a re-run only checks and continues).
bash "$HERE/deploy/setup_runpod.sh"

# 3. Run the pipeline (background; survives closing the terminal). Extra args pass through.
bash "$HERE/deploy/run_on_pod.sh" "$@"
