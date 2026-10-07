# Orion Pipeline 1: video -> SFT training data

Turns source videos into 4 SFT datasets (Director, Style, Footage, Story Architect) for LoRA
fine-tuning. **Input is only the video file.** No agent (A01-A30) files are used.

A self-hosted Qwen3-Omni-30B-A3B-Instruct (4-bit AWQ by default) watches and listens to each video.
Everything is saved to Google Drive after every step, so a lost GPU can be replaced and the run resumes.

## Stages
- **A** `pipeline/stage_a_unified_dna.py` + `pipeline/self_extract.py`: ffprobe (duration/fps/size),
  ffmpeg shot cuts, faster-whisper transcript with timestamps -> `stage_a_extract/`
- **B** `stage_b_chunking.py`: chunks of up to 75 s, cut at speech pauses (fixed windows if no speech)
- **C** `omni_client/stage_c_omni.py`: per chunk, video clip + audio + evidence text -> Qwen3-Omni
  (Director, Footage); sampled frames -> Style, Story. Every call is cached for resume.
- **D** `stage_d_judge.py`: rule-based ID/structure checks only. It does NOT verify that claims are true.
- **E** `stage_e_dataset_extractor.py`: builds SFT rows with the text-only student system prompt
- **F** `stage_f_sft_qa.py`: rejects placeholders/malformed/duplicates; holds back Footage rows whose
  candidate pool is empty (still in `stage_e_sft/`, nothing deleted). Writes the video's FINAL rows to `stage_f_final/`.
- **G** `stage_g_aggregate.py`: joins every video's `stage_f_final/` into 4 final `*_sft.jsonl` files.
  Can be re-run offline, no GPU: `python orchestrator/aggregate_offline.py --stages <Each stage folder>`

## Run it
See `deploy/README_RUNPOD.md` (rent a 48 GB GPU, `setup_runpod.sh`, then `run_on_pod.sh --limit 1`).
Test without a GPU: `python tests/simulate_gpu_loss.py --backend folder` (fake data, mock server).

## Layout
```
pipeline/        stages A, B, D, E, F, G + self_extract (no GPU needed except Whisper)
omni_client/     Stage C client + prompts
orchestrator/    run_pipeline.py (resumable), aggregate_offline.py, drive_sync.py, preflight.py, state.py, progress.py
deploy/          RunPod scripts, smoke test, orion.env.example
tests/           fake-data simulation (mock vLLM, fake rclone, stub Whisper)
INPUT/ OUTPUT/   data folders (contents are git-ignored)
```

## Model roles

| Role | Base model | Trained? | Where |
|---|---|---|---|
| Teacher: watches video + reads evidence | Qwen3-Omni-30B-A3B-Instruct | No, inference only | Stage C |
| Director / Style / Footage adapters | Qwen3-30B-A3B (text-only) | LoRA x3 | trained later on this repo's output |
| After Effects / Premiere / Blender adapters | Qwen3-Coder-30B-A3B-Instruct | LoRA x3 | Pipeline 2, not here |

* Teacher prompts mention the video and say to trust what it sees over the evidence text.
* Stored SFT rows use the text-only STUDENT prompt (`omni_client/prompts.py: STUDENT_SYSTEM`).
* Footage rows are held back until the Python retrieval layer (later) gives real candidates.
  `ORION_ALLOW_EMPTY_CANDIDATES=1` overrides. `ORION_USE_AGENTS=1` restores the old agent mode.
* Story Architect is produced as a 4th file; whether it becomes an adapter is an open question.

## Not proven yet
Real GPU, real Whisper, the exact Qwen3-Omni video+audio request format under vLLM, real Drive,
and the quality of the 4-bit teacher. The one-video test on the pod checks these.
