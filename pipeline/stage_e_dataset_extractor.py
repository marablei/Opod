"""
Stage E — Dataset Extractor.

Converts Stage D's validated reasoning into chat-style SFT format
(system/user/assistant), matching the dataset shape already agreed on:
one example per chunk for Director/Footage, one example per video for
Style/Story Architect. Rebuilds the exact same USER prompt that was sent
in Stage C (via omni_client.prompts). The SYSTEM prompt stored is the text-only
STUDENT one (no mention of a video), because the model being fine-tuned
(Qwen3-30B-A3B) never sees the video; only the Omni teacher does.

Writes 4 per-video shard files to stage_e_sft/ (before the QA gate; Stage F writes the final ones) — these get concatenated across
all videos by Stage G at the end of the batch, not here.
"""
import json
from pathlib import Path

from omni_client.prompts import (
    build_director_prompt, build_footage_prompt,
    build_style_prompt, build_story_architect_prompt,
    STUDENT_SYSTEM,
)


def _write_jsonl(path: Path, rows: list[dict]):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _target(obj: dict) -> dict:
    """The training target is only what the student must produce: no bookkeeping (video_id, retry notes)."""
    return {k: v for k, v in obj.items() if k not in ("video_id", "_problems", "_attempts")}


def _chat_example(system: str, user: str, assistant_obj: dict) -> dict:
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(_target(assistant_obj), ensure_ascii=False)},
        ]
    }


def run(video_input_dir: Path, video_out_dir: Path):
    judge_dir = video_out_dir / "stage_d_judge"
    chunks_dir = video_out_dir / "stage_b_chunks"
    if not judge_dir.exists():
        raise FileNotFoundError(f"Stage D output not found: {judge_dir}. Run Stage D first.")

    segment_chunks = {c["chunk_id"]: c for c in json.loads((chunks_dir / "segment_chunks.json").read_text(encoding="utf-8"))}
    style_rollup = json.loads((chunks_dir / "style_rollup.json").read_text(encoding="utf-8"))
    story_input = json.loads((chunks_dir / "story_input.json").read_text(encoding="utf-8"))

    director_validated = json.loads((judge_dir / "director_validated.json").read_text(encoding="utf-8"))
    footage_validated = json.loads((judge_dir / "footage_validated.json").read_text(encoding="utf-8"))
    style_validated = json.loads((judge_dir / "style_validated.json").read_text(encoding="utf-8"))
    story_validated = json.loads((judge_dir / "story_architect_validated.json").read_text(encoding="utf-8"))

    out_dir = video_out_dir / "stage_e_sft"
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Director: one row per validated chunk ---
    director_rows = []
    for entry in director_validated:
        chunk = segment_chunks.get(entry["chunk_id"])
        if chunk is None:
            continue  # shouldn't happen post-Judge, but never trust silently
        _teacher_sys, user_p = build_director_prompt(chunk)
        director_rows.append(_chat_example(STUDENT_SYSTEM["director"], user_p, entry))
    _write_jsonl(out_dir / "director_sft.jsonl", director_rows)

    # --- Footage: one row per validated chunk ---
    footage_rows = []
    for entry in footage_validated:
        chunk = segment_chunks.get(entry["chunk_id"])
        if chunk is None:
            continue
        _teacher_sys, user_p = build_footage_prompt(chunk)
        footage_rows.append(_chat_example(STUDENT_SYSTEM["footage"], user_p, entry))
    _write_jsonl(out_dir / "footage_sft.jsonl", footage_rows)

    # --- Style: one row for the whole video, if it passed the Judge ---
    style_rows = []
    if style_validated:
        _teacher_sys, user_p = build_style_prompt(style_rollup)
        style_rows.append(_chat_example(STUDENT_SYSTEM["style"], user_p, style_validated))
    _write_jsonl(out_dir / "style_sft.jsonl", style_rows)

    # --- Story Architect: one row for the whole video, if it passed the Judge ---
    story_rows = []
    if story_validated:
        _teacher_sys, user_p = build_story_architect_prompt(story_input)
        story_rows.append(_chat_example(STUDENT_SYSTEM["story_architect"], user_p, story_validated))
    _write_jsonl(out_dir / "story_architect_sft.jsonl", story_rows)

    return {
        "director_rows": len(director_rows),
        "footage_rows": len(footage_rows),
        "style_rows": len(style_rows),
        "story_rows": len(story_rows),
    }
