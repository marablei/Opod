# Orion Pipeline 1: video -> SFT training data

Turns source videos into 4 SFT datasets (Director, Style, Footage, Story Architect) for LoRA
fine-tuning. **Input is only the video file.** No agent (A01-A30) files are used.

A self-hosted Qwen3-Omni-30B-A3B-Instruct (4-bit AWQ by default) watches and listens to each video.
Everything is saved to Google Drive after every step, so a lost GPU can be replaced and the run resumes.

## Stages
- **A** `pipeline/stage_a_unified_dna.py` + `pipeline/self_extract.py`: ffprobe (duration/fps/size),
  ffmpeg shot cuts, faster-whisper transcript with timestamps -> `stage_a_extract/`
- **B** `stage_b_chunking.py`: short chunks (target 14 s, 6-22 s) cut at sentence/segment ends and snapped to real shot cuts (fixed windows if no speech)
- **C** `omni_client/stage_c_omni.py`: per chunk, video clip + audio + evidence text (transcript with timings, shot cuts) -> Qwen3-Omni
  (Director; Footage calls are off until retrieval exists); 12 sampled frames -> Style, Story. Every answer is checked by
  `omni_client/validators.py`; an invalid one is sent back with the reasons (re-ask, default 2x). Every call is cached for resume.
- **D** `stage_d_judge.py`: re-runs the same validators (format, durations, beat timing/consistency, segment ids, act order,
  quotes must exist in the transcript). It still does NOT see the video, so it cannot prove a label is *true*.
- **E** `stage_e_dataset_extractor.py`: builds SFT rows with the text-only student system prompt
- **F** `stage_f_sft_qa.py`: rejects placeholders/malformed/duplicates; holds back Footage rows whose
  candidate pool is empty (still in `stage_e_sft/`, nothing deleted). Writes the video's FINAL rows to `stage_f_final/`.
- **G** `stage_g_aggregate.py`: joins every video's `stage_f_final/` into 4 final `*_sft.jsonl` files.
  Can be re-run offline, no GPU: `python orchestrator/aggregate_offline.py --stages <Each stage folder>`

## Run it
Short guide: `deploy/RUN_GUIDE.md` (rent a 48 GB GPU, clone, save the Drive token, `bash go.sh --limit 5`).
Details: `deploy/README_RUNPOD.md`. Known problems and their fixes: `deploy/KNOWN_ISSUES_FIRST_RUN.md`.
Live view while it runs: `dashboard/server.py` (started by go.sh) or `python3 deploy/status.py`. Every number comes from real files.
Pre-flight check that stops before GPU money is spent: `python3 deploy/doctor.py`.
Test without a GPU: `python tests/test_quality.py` and `python tests/simulate_gpu_loss.py --backend folder` (mock server).

## Layout
```
pipeline/        stages A, B, D, E, F, G + self_extract (no GPU needed except Whisper)
omni_client/     Stage C client, prompts, validators (the checks on every model answer)
orchestrator/    run_pipeline.py (resumable), drive_sync.py, preflight.py, state.py, progress.py,
                 events.py (live event log), run_view.py (read-only view of a run), report.py (end-of-run report)
dashboard/       live web page (server.py + index.html), read-only
config/          glossary.txt (words Whisper should prefer, e.g. TRAI)
deploy/          RunPod scripts, doctor.py, save_drive_token.py, smoke test, orion.env.example
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
