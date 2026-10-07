#!/usr/bin/env bash
# Shared settings for all deploy/*.sh scripts. Source this, don't run it.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${ORION_WORKSPACE:-/workspace}"
ENV_FILE="${ORION_ENV_FILE:-$WORKSPACE/orion.env}"

# Your settings (Drive token, model profile, ...). See deploy/orion.env.example.
if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
fi

VENV="${ORION_VENV:-$WORKSPACE/venv}"
PY="$VENV/bin/python"
export HF_HOME="${HF_HOME:-$WORKSPACE/hf_cache}"       # model weights live on the persistent volume
export HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}"

PORT="${ORION_OMNI_PORT:-8901}"
SERVED_NAME="${ORION_OMNI_MODEL:-qwen3-omni-30b-a3b-instruct}"
VLLM_VERSION="${VLLM_VERSION:-0.11.1}"                  # 0.11.1 is the version reported working with this model
SERVER_LOG="$WORKSPACE/vllm_server.log"
SERVER_PID_FILE="$WORKSPACE/vllm_server.pid"
RUN_LOG="$WORKSPACE/orion_run.log"

INPUT_DIR="${ORION_INPUT_DIR:-$WORKSPACE/INPUT}"
OUTPUT_DIR="${ORION_OUTPUT_DIR:-$WORKSPACE/OUTPUT}"

NGPU=0
if command -v nvidia-smi >/dev/null 2>&1; then
  NGPU="$(nvidia-smi -L 2>/dev/null | wc -l | tr -d ' ')"
fi

# ---- model profiles ---------------------------------------------------------
# awq48 : community 4-bit AWQ build of Qwen3-Omni-30B-A3B-Instruct. Fits one 48 GB
#         card (reported working on a 48 GB RTX 4090D with these exact flags).
#         NOT the official weights: quantised, small quality loss possible.
# bf16  : the official Qwen/Qwen3-Omni-30B-A3B-Instruct weights. ~61 GB of weights
#         alone, so needs 80 GB+ (and the model card's own table shows ~79 GB for a
#         15 s video, ~108 GB for 60 s) -> realistically 2x80 GB or an H200.
MODEL_PROFILE="${MODEL_PROFILE:-awq48}"
case "$MODEL_PROFILE" in
  awq48)
    MODEL_ID="${MODEL_ID:-cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit}"
    MIN_VRAM_GB=44
    MIN_DISK_GB=60
    # 0.90 (not 0.95/0.97) on purpose: leaves ~4-5 GB of the 48 GB free for Whisper, which
    # runs on the same GPU to transcribe each video. If the model OOMs at load, lower the
    # clip size (ORION_CLIP_HEIGHT) rather than raising this.
    PROFILE_FLAGS="--max-model-len 32768 --gpu-memory-utilization 0.90 --max-num-seqs 1"
    ;;
  bf16)
    MODEL_ID="${MODEL_ID:-Qwen/Qwen3-Omni-30B-A3B-Instruct}"
    MIN_VRAM_GB=78
    MIN_DISK_GB=100
    PROFILE_FLAGS="--dtype bfloat16 --max-model-len 32768 --tensor-parallel-size ${NGPU:-1} --gpu-memory-utilization 0.90 --max-num-seqs 1"
    ;;
  *)
    echo "Unknown MODEL_PROFILE='$MODEL_PROFILE' (use awq48 or bf16)" >&2; return 1 2>/dev/null || exit 1 ;;
esac
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"

# Whisper (faster-whisper/CTranslate2) needs the CUDA libs that pip put inside the venv.
for _d in "$VENV"/lib/python*/site-packages/nvidia/*/lib; do
  [ -d "$_d" ] && export LD_LIBRARY_PATH="$_d:${LD_LIBRARY_PATH:-}"
done
# Agent files (P1_A*.jsonl) are ignored: the pipeline reads everything from the video itself.
export ORION_USE_AGENTS="${ORION_USE_AGENTS:-0}"
export ORION_WHISPER_MODEL="${ORION_WHISPER_MODEL:-large-v3-turbo}"

# Drive settings can be one root (ORION_DRIVE_REMOTE) or three folders by ID (ORION_DRIVE_*_ID).
drive_configured() { [ -n "${ORION_DRIVE_REMOTE:-}" ] || [ -n "${ORION_DRIVE_INPUT_ID:-}" ]; }
drive_needs_rclone() {  # true unless the "remote" is a plain folder path
  if [ -n "${ORION_DRIVE_INPUT_ID:-}" ]; then return 0; fi
  [ -n "${ORION_DRIVE_REMOTE:-}" ] && [[ "${ORION_DRIVE_REMOTE}" != /* ]] && [[ "${ORION_DRIVE_REMOTE}" != local:* ]]
}
log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "[$(date +%H:%M:%S)] ERROR: $*" >&2; exit 1; }
server_healthy() { curl -sf -m 5 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; }
