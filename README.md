# Orion Pipeline — Semantic Reasoning Sub-Pipeline

Replaces Gemini with self-hosted Qwen3-Omni-30B-A3B-Instruct for Pipeline 1's
semantic reasoning stage. Produces 4 SFT training datasets (Director, Style,
Footage, Story Architect) from source video + A01-A30 deterministic agent
outputs.

## Stages
- **Stage A** — Unified DNA merge (A01-A30 → one structured timeline per video)
- **Stage B** — Chunking (segment-level + whole-video views)
- **Stage C** — Qwen3-Omni inference (video clip + evidence → raw reasoning)
- **Stage D** — Semantic Judge (rule-based grounding check)
- **Stage E** — Dataset Extractor (validated reasoning → SFT JSONL shards)
- **Stage F** — SFT QA gate
- **Stage G** — Corpus Aggregation (per-video shards → 4 final training files)

## Status
Scaffolding in progress. Stage A's real parser is pending inspection of the
actual A01-A30 file structure (hardlink issue blocking direct read as of this
commit — see `docs/data_structure_notes.md` once filled in).

## Layout
```
orion_pipeline/
  pipeline/          # Stages A, B, D, E, F, G — pure Python, no GPU
  omni_client/       # Stage C — client that calls the Omni model server
  orchestrator/       # run_pipeline.py — 1-click, checkpointed, resumable
  docs/
  requirements.txt
```

## Model roles (the LLM approach)

| Role | Base model | Trained? | Where it runs in this repo |
|---|---|---|---|
| Teacher: watches video + reads evidence | Qwen3-Omni-30B-A3B-Instruct | No, inference only | Stage C |
| Director / Style / Footage adapters | Qwen3-30B-A3B (text-only) | LoRA fine-tune x3 | trained later on the SFT files this repo produces |
| After Effects / Premiere / Blender adapters | Qwen3-Coder-30B-A3B-Instruct | LoRA fine-tune x3 | Pipeline 2, not in this repo yet |

Consequences built into the code:
* Teacher prompts (Stage C) mention the video and say "trust what you see over the evidence".
* The system prompt stored in each SFT row is the text-only STUDENT prompt (`omni_client/prompts.py:
  STUDENT_SYSTEM`), because the student never sees a video. The user text is identical to the teacher's.
* Footage rows with an empty candidate pool are held back by Stage F (they would teach the adapter to
  reject everything). They are logged and kept in `.pre_qa`, never deleted.
  `ORION_ALLOW_EMPTY_CANDIDATES=1` overrides.
* No A-file agents: duration, shot cuts and the transcript come from the video itself
  (`pipeline/self_extract.py`: ffprobe + ffmpeg + faster-whisper). `ORION_USE_AGENTS=1` restores the old behaviour.
* A Story Architect dataset is also produced (4th file); it is not in the 3-adapter list above. Open question.
