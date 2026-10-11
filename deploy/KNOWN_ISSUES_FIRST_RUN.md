# Orion — first real RunPod run: what broke and how it was fixed

Pod: 1x A40 48 GB, RunPod PyTorch 2.8.0, On-Demand, 100 GB container disk. Model `cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit`, vLLM 0.11.1, faster-whisper 1.2.1.
Status: the issues below were found on the FIRST real run (2026-10-11) and fixed in the repo. Anything not listed here has not been tested on a real pod.

| # | Symptom | Cause | Fix (already in the repo) |
|---|---|---|---|
| 1 | vLLM exits at startup: `AttributeError: Qwen2Tokenizer has no attribute all_special_tokens_extended`; pip warns `vllm 0.11.1 requires transformers<5` | `setup_runpod.sh` ran `pip install -U "transformers>=4.57.0"`, which pulled transformers 5.x | `deploy/setup_runpod.sh` line 70 pins `"transformers>=4.57.0,<5"` (4.57.6 works) |
| 2 | `ERROR: Not enough disk` on every re-run although the pod has 100 GB | The check needs 60 GB free, but the installed venv + model already use ~44 GB, so a re-run sees ~56 GB | Disk check is enforced only on a fresh install (no venv); a re-run needs just 20 GB free |
| 3 | Smoke test: `SELF-EXTRACT FAILED (Whisper/ffmpeg): open() got an unexpected keyword argument 'metadata_errors'` | faster-whisper 1.2.1 decodes audio with PyAV (`av`); the installed `av` 19.0.1 no longer accepts that argument, and `av` 14.4.0 would not build on the pod | `pipeline/self_extract.py` now decodes audio with ffmpeg to 16 kHz float32 and passes the array to Whisper (`_decode_audio_ffmpeg`), so PyAV's `open()` is never used. No `av` pin needed |
| 4 | Smoke test: `✓` video+audio, then `MULTI-IMAGE REQUEST FAILED ... Connection refused`; vLLM log: `torch.OutOfMemoryError` in the vision encoder | vLLM took 90% of the card (40.6 GiB) and Whisper large-v3 holds ~3.8 GiB on the same GPU, leaving ~26 MiB for the image step | `--gpu-memory-utilization 0.80` for the `awq48` profile in `deploy/_common.sh`. (Pod-side override: `VLLM_EXTRA_ARGS="--gpu-memory-utilization 0.80"` in `/workspace/orion.env`) |
| 5 | `Drive check failed — Cannot reach Drive`; rclone: `parsing time "..."` | The Drive token in `orion.env` was still the example placeholder (`"..."`): saving the real token had failed | Token saved from a file, not typed into a hidden prompt (see "Saving the Drive token" below) |

## How each of these is now prevented (second round)
| Problem | Guard in the repo |
|---|---|
| transformers 5.x breaks vLLM | `deploy/constraints.txt` applied to every pip install + a final re-pin + `doctor.py --stage post` fails if transformers >= 5 |
| disk check on re-run | fresh-install only (done) + doctor reports free GB |
| PyAV / audio decode | ffmpeg decode (done); faster-whisper pinned to 1.2.1 |
| GPU memory (vLLM vs Whisper) | 0.80 (done) + doctor `--stage run` fails if < 2.5 GB GPU memory is free for Whisper |
| bad/placeholder Drive token | `deploy/save_drive_token.py` validates + never prints; doctor checks the token JSON before any install |
| Drive names with hidden spaces, rclone 1.58 | exact names + `--files-from-raw` (done) |
| two pipelines at once | pgrep guard + doctor check |
| stale cache after prompt/rule change | cache key = prompt fingerprint + validator version |
| silent Style/Story rejection | explicit schemas, reasons logged, validators shared by Stage C and D |
| not knowing what is running | `dashboard/server.py` + `deploy/status.py`, built only from real files |

## Saving the Drive token (reliable, copy-paste only)
Do not put the token inside any command. Do not send screenshots that show it. Three steps, one at a time:
1. `cat > /workspace/drive_token.txt`  → Enter (cursor waits)
2. paste the token `{...}` → Enter → Ctrl+D
3. `clear`, then run the "save" box from the guide (reads the file, validates, writes `orion.env`, deletes the file)
Then test: `rclone ls "gdrive,root_folder_id=$ORION_DRIVE_INPUT_ID:"` must list your video.

## Notes
- A token that has appeared in chat or a screenshot is exposed: revoke it at myaccount.google.com/permissions (rclone → Remove access) and make a new one.
- The web terminal truncates very long pastes (~25 lines). Keep boxes short, paste one at a time.
- Container disk is erased on Stop. Next time consider a persistent volume mounted at `/workspace`, so the venv and model (~44 GB) survive and setup takes seconds instead of ~30 min. Check RunPod's current storage price first.
- Model load ~4-5 min each time the server starts; install + model download ~20 min the first time.
- Still unproven at the time of writing: the full pipeline on a real video, output quality, Arabic / mixed-language transcripts, timing per video, cost per video.
