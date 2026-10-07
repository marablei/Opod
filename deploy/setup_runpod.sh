#!/usr/bin/env bash
# Orion — one-shot RunPod bootstrap. Safe to re-run (idempotent) after a pod restart.
#
#   git clone <repo> /workspace/Orion_Pipeline && cd /workspace/Orion_Pipeline
#   cp deploy/orion.env.example /workspace/orion.env   # then edit it (Drive token!)
#   bash deploy/setup_runpod.sh
#
# What it does: checks the GPU/disk, installs ffmpeg + rclone + vLLM into a venv,
# downloads Qwen3-Omni, starts the model server, then runs a multimodal smoke test
# so you find out about payload/memory problems BEFORE a real batch.
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
mkdir -p "$WORKSPACE"
exec > >(tee -a "$WORKSPACE/orion_setup.log") 2>&1
trap 'echo "[setup] FAILED at line $LINENO — see $WORKSPACE/orion_setup.log" >&2' ERR

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

log "== Orion setup | profile=$MODEL_PROFILE | model=$MODEL_ID | repo=$REPO_DIR"

# ---- 0. hardware sanity (fail BEFORE downloading 20-60 GB) -------------------
command -v nvidia-smi >/dev/null 2>&1 || die "nvidia-smi not found — this pod has no GPU driver. Pick a GPU pod."
[ "$NGPU" -ge 1 ] || die "No GPU visible."
TOTAL_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | awk '{s+=$1} END{print s}')"
TOTAL_GB=$(( TOTAL_MIB / 1024 ))
log "GPUs: $NGPU | total VRAM: ${TOTAL_GB} GB"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
if [ "$TOTAL_GB" -lt "$MIN_VRAM_GB" ]; then
  die "Profile '$MODEL_PROFILE' needs >= ${MIN_VRAM_GB} GB total VRAM, this pod has ${TOTAL_GB} GB. Rent a bigger GPU (awq48: one 48 GB card; bf16: 80 GB+, realistically 2x80 GB)."
fi
if [ "$MODEL_PROFILE" = "bf16" ] && [ "$TOTAL_GB" -lt 150 ]; then
  log "WARNING: official BF16 weights + video tokens are tight below ~150 GB (model card: ~79 GB for 15 s of video, ~108 GB for 60 s, measured with transformers). If the smoke test OOMs, lower ORION_CLIP_HEIGHT/ORION_CLIP_FPS, use more GPUs, or switch to MODEL_PROFILE=awq48."
fi
mkdir -p "$HF_HOME" "$INPUT_DIR" "$OUTPUT_DIR"
FREE_GB="$(df -BG --output=avail "$WORKSPACE" | tail -1 | tr -dc '0-9')"
log "free disk on $WORKSPACE: ${FREE_GB} GB (need >= ${MIN_DISK_GB})"
[ "$FREE_GB" -ge "$MIN_DISK_GB" ] || die "Not enough disk. Give the pod a bigger volume/container disk (>= ${MIN_DISK_GB} GB)."

# ---- 1. system packages ------------------------------------------------------
NEED_APT=""
for bin in ffmpeg curl git; do command -v "$bin" >/dev/null 2>&1 || NEED_APT="$NEED_APT $bin"; done
python3 -m venv --help >/dev/null 2>&1 || NEED_APT="$NEED_APT python3-venv"
if [ -n "$NEED_APT" ]; then
  log "apt installing:$NEED_APT"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq $NEED_APT unzip
fi

# ---- 2. rclone (Google Drive) ------------------------------------------------
if drive_needs_rclone; then
  if ! command -v rclone >/dev/null 2>&1; then
    log "installing rclone"
    curl -fsSL https://rclone.org/install.sh | $SUDO bash
  fi
  log "rclone: $(rclone version | head -1)"
else
  log "Drive sync via rclone not configured (ORION_DRIVE_REMOTE empty or a plain folder) — skipping rclone."
fi

# ---- 3. python venv + packages -----------------------------------------------
PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
case "$PYV" in 3.9|3.10|3.11|3.12) ;; *) die "Python $PYV not supported by vLLM $VLLM_VERSION (need 3.9-3.12).";; esac
[ -x "$PY" ] || { log "creating venv at $VENV"; python3 -m venv "$VENV"; }
"$VENV/bin/pip" install -q -U pip wheel
if ! "$PY" -c "import vllm" >/dev/null 2>&1; then
  log "installing vllm==$VLLM_VERSION (several minutes)"
  "$VENV/bin/pip" install "vllm==$VLLM_VERSION"
fi
log "installing model support packages"
"$VENV/bin/pip" install -q -U "transformers>=4.57.0" accelerate qwen-omni-utils huggingface_hub rich pydantic
"$VENV/bin/pip" install -q hf_transfer || { log "hf_transfer unavailable, using normal downloads"; export HF_HUB_ENABLE_HF_TRANSFER=0; }
"$VENV/bin/pip" install -q -r "$REPO_DIR/requirements.txt"
log "installing faster-whisper (speech-to-text, replaces the A18 agent)"
"$VENV/bin/pip" install -q "faster-whisper>=1.1.0"

# ---- 4. download the model ---------------------------------------------------
log "downloading $MODEL_ID to $HF_HOME (cached: instant on re-run)"
MODEL_ID="$MODEL_ID" "$PY" - <<'PYEOF'
import os
from huggingface_hub import snapshot_download
p = snapshot_download(repo_id=os.environ["MODEL_ID"], token=os.environ.get("HF_TOKEN") or None)
print("model files at:", p)

PYEOF

# transformers must recognise the qwen3_omni_moe architecture (older releases don't).
if ! MODEL_ID="$MODEL_ID" "$PY" -c "import os;from transformers import AutoConfig;AutoConfig.from_pretrained(os.environ['MODEL_ID'])" >/dev/null 2>&1; then
  log "installed transformers does not know qwen3_omni_moe — installing from GitHub main"
  "$VENV/bin/pip" install -q -U "git+https://github.com/huggingface/transformers"
  MODEL_ID="$MODEL_ID" "$PY" -c "import os;from transformers import AutoConfig;AutoConfig.from_pretrained(os.environ['MODEL_ID'])" \
    || die "transformers still can't load the model config."
fi

# ---- 4b. Whisper: download + test-load while the GPU is still empty ----------
log "downloading/loading Whisper '$ORION_WHISPER_MODEL' (CPU fallback is automatic if GPU libs are missing)"
REPO_DIR="$REPO_DIR" "$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["REPO_DIR"])
from faster_whisper import WhisperModel
from pipeline import self_extract
self_extract._load_whisper(WhisperModel)
PYEOF

# ---- 5. start the server -----------------------------------------------------
bash "$REPO_DIR/deploy/start_server.sh"

# ---- 6. smoke test (real multimodal request: video + audio + text) ----------
log "running multimodal smoke test"
ORION_OMNI_MODE=live ORION_OMNI_ENDPOINT="http://127.0.0.1:$PORT/v1/chat/completions" \
  "$PY" "$REPO_DIR/deploy/smoke_test.py" || die "Smoke test failed. Read $SERVER_LOG and the error above BEFORE running a batch."

cat <<DONE

==================== SETUP COMPLETE ====================
Model server : http://127.0.0.1:$PORT  (log: $SERVER_LOG)
Next, the 1-video test:   bash $REPO_DIR/deploy/run_on_pod.sh --limit 1
Then everything:          bash $REPO_DIR/deploy/run_on_pod.sh
Watch progress:           watch -n 10 $PY $REPO_DIR/deploy/status.py
=========================================================
DONE
