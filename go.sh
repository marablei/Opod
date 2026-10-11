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

# 0a. Settings RunPod gave this pod (template environment variables / secrets), e.g. the Drive token and GH_TOKEN.
if [ -f /etc/rp_environment ]; then set -a; source /etc/rp_environment; set +a; fi

# 0b. Always run the newest code: pull from GitHub, then restart this script once (token comes from GH_TOKEN, never typed).
if [ -z "${ORION_UPDATED:-}" ] && [ -d "$HERE/.git" ]; then
  _GT="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  if [ -n "$_GT" ]; then
    git -C "$HERE" -c "http.extraheader=Authorization: Basic $(printf 'x-access-token:%s' "$_GT" | base64 -w0)" pull --ff-only -q \
      && echo "[orion] code updated to the newest version" || echo "[orion] could not update the code (continuing with the version on disk)"
  else
    git -C "$HERE" pull --ff-only -q 2>/dev/null && echo "[orion] code updated" \
      || echo "[orion] no GH_TOKEN set and repo not public: using the version on disk"
  fi
  unset _GT
  export ORION_UPDATED=1
  exec bash "$HERE/go.sh" "$@"
fi

# 0c. Settings file: created automatically (folder ids, model profile). The Drive token is NOT in it: it comes from the pod's
#     environment (RunPod secret) - or from the file if you saved one with deploy/save_drive_token.py.
_WS="${ORION_WORKSPACE:-/workspace}"; mkdir -p "$_WS"
if [ ! -f "${ORION_ENV_FILE:-$_WS/orion.env}" ]; then
  cp "$HERE/deploy/orion.env.example" "${ORION_ENV_FILE:-$_WS/orion.env}"
  echo "[orion] created ${ORION_ENV_FILE:-$_WS/orion.env} from the example"
fi

source "$HERE/deploy/_common.sh"
mkdir -p "$WORKSPACE"

phase go_start "$*"
# 0. Live dashboard first, so you can watch setup and model loading too.
bash "$HERE/deploy/start_dashboard.sh" || true

# 1. Fail in seconds (not after a 20-minute install) if something is wrong: GPU, disk, settings, token, network.
python3 "$HERE/deploy/doctor.py" --stage pre || die "The doctor found problems (above). Fix the lines marked ✗, then run this again. Nothing was installed."
phase doctor_pre_ok ""

# 2. Install + model + server + smoke test (idempotent: a re-run only checks and continues).
bash "$HERE/deploy/setup_runpod.sh"
python3 "$HERE/deploy/doctor.py" --stage post || die "A package check failed after setup (above)."

# 3. Run the pipeline (background; survives closing the terminal). Extra args pass through.
bash "$HERE/deploy/run_on_pod.sh" "$@"
