"""
Stage D — Semantic Judge (rule-based grounding check).

For every claim in director_raw / footage_raw / story_architect_raw: is it
traceable back to something that actually exists in this video's evidence
(stage_a_extract/unified_dna.json, stage_b_chunks/*.json)? Unsupported claims are rejected,
not silently kept, and logged to _errors-style rejected_claims.log so you
can see exactly what was thrown out and why.

Style gets a lighter check since its output (a Style Profile config) isn't
made of evidence-citing claims the same way Director/Footage/Story
Architect are — it's checked for video_id match and required-field
presence only.

Rule-based checks, all in omni_client/validators.py (the same code Stage C uses to re-ask the model):
  - Director: chunk_id exists; exact keys; treatment/beat kinds from one fixed vocabulary; beats in order, inside the
    chunk and covering >= 60% of it; treatment consistent with the beats; footage/audio queries not generic; durations
    computed by code; quoted words must occur in the chunk's transcript.
  - Footage: chunk_id exists; asset ids must exist in the candidate pool (none exists yet -> any id is rejected).
  - Story Architect: exact keys; every segment id real; acts in video order; quotes must occur in the transcript.
  - Style: video_id matches; exactly the 6 keys; short values ("not_present" allowed); no codec/resolution facts;
    pacing_target computed from the measured cut rate.
Tier 3 (LLM judge) stays stubbed. NOTE: none of this sees the video, so it cannot prove a label is TRUE.
"""
import json
import time
from pathlib import Path

from omni_client import validators


def _log_rejection(log_path: Path, stage: str, detail: str):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{stage}] {detail}\n")


def _strip_meta(entry):
    return {k: v for k, v in entry.items() if k not in ("_problems", "_attempts")} if isinstance(entry, dict) else entry


def _validate_director(entries: list[dict], chunks_by_id: dict, log_path: Path) -> list[dict]:
    """Same checks Stage C ran (omni_client/validators.py), repeated here so nothing invalid can pass
    even if it came from an old cache or another pod."""
    validated = []
    for entry in entries:
        chunk = chunks_by_id.get(entry.get("chunk_id"))
        if chunk is None:
            _log_rejection(log_path, "director", f"chunk_id '{entry.get('chunk_id')}' not found in this video's chunks — rejected")
            continue
        entry = validators.normalize_director(_strip_meta(entry), chunk)
        problems = validators.validate_director(entry, chunk)
        if problems:
            fixed = validators.salvage_audio(entry, problems)
            if fixed is not None and not validators.validate_director(fixed, chunk):
                entry, problems = fixed, []
                _log_rejection(log_path, "director", f"{chunk['chunk_id']} kept: audio query stayed generic after re-asks; stored as 'music bed present; character not specified'")
        if problems:
            _log_rejection(log_path, "director", f"{chunk['chunk_id']} rejected: " + " | ".join(problems))
            continue
        validated.append(entry)
    return validated


def _validate_footage(entries: list[dict], chunks_by_id: dict, log_path: Path) -> list[dict]:
    validated = []
    for entry in entries:
        chunk = chunks_by_id.get(entry.get("chunk_id"))
        if chunk is None:
            _log_rejection(log_path, "footage", f"chunk_id '{entry.get('chunk_id')}' not found in this video's chunks — rejected")
            continue
        asset_ids = entry.get("asset_ids") or entry.get("confirmed_asset_ids") or []
        if asset_ids:
            # TODO: once the Python retrieval layer is wired in, check against that chunk's actual
            # candidate pool instead of rejecting outright.
            _log_rejection(log_path, "footage",
                           f"chunk_id '{chunk['chunk_id']}' references asset_ids {asset_ids} but no candidate "
                           f"pool exists yet for this chunk (retrieval not wired in) — rejected")
            continue
        validated.append(_strip_meta(entry))
    return validated


def _validate_story_architect(story: dict, story_input: dict, log_path: Path) -> dict | None:
    story = validators.normalize_story(_strip_meta(story), story_input)
    problems = validators.validate_story(story, story_input)
    if problems:
        _log_rejection(log_path, "story_architect", "rejected: " + " | ".join(problems))
        return None
    return story


def _validate_style(style: dict, expected_video_id: str, rollup: dict, log_path: Path) -> dict | None:
    if style.get("video_id") != expected_video_id:
        _log_rejection(log_path, "style", f"video_id mismatch: got '{style.get('video_id')}' expected '{expected_video_id}'")
        return None
    style = validators.normalize_style(_strip_meta(style), rollup)
    problems = validators.validate_style(style, rollup)
    if problems:
        _log_rejection(log_path, "style", "rejected: " + " | ".join(problems))
        return None
    return style


def run(video_input_dir: Path, video_out_dir: Path):
    raw_dir = video_out_dir / "stage_c_omni"
    chunks_dir = video_out_dir / "stage_b_chunks"
    if not raw_dir.exists():
        raise FileNotFoundError(f"Stage C output not found: {raw_dir}. Run Stage C first.")

    segment_chunks = json.loads((chunks_dir / "segment_chunks.json").read_text(encoding="utf-8"))
    story_input = json.loads((chunks_dir / "story_input.json").read_text(encoding="utf-8"))
    style_rollup = json.loads((chunks_dir / "style_rollup.json").read_text(encoding="utf-8"))
    chunks_by_id = {c["chunk_id"]: c for c in segment_chunks}
    video_id = story_input["video_id"]

    out_dir = video_out_dir / "stage_d_judge"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "rejected_claims.log"
    # Never delete an earlier log: append a run header so every run stays visible.
    with open(log_path, "a", encoding="utf-8") as _f:
        _f.write(f"\n=== Stage D run {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    director_raw = json.loads((raw_dir / "director_raw.json").read_text(encoding="utf-8"))
    footage_raw = json.loads((raw_dir / "footage_raw.json").read_text(encoding="utf-8"))
    style_raw = json.loads((raw_dir / "style_raw.json").read_text(encoding="utf-8"))
    story_raw = json.loads((raw_dir / "story_architect_raw.json").read_text(encoding="utf-8"))

    director_validated = _validate_director(director_raw, chunks_by_id, log_path)
    footage_validated = _validate_footage(footage_raw, chunks_by_id, log_path)
    style_validated = _validate_style(style_raw, video_id, style_rollup, log_path)
    story_validated = _validate_story_architect(story_raw, story_input, log_path)

    (out_dir / "director_validated.json").write_text(json.dumps(director_validated, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "footage_validated.json").write_text(json.dumps(footage_validated, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "style_validated.json").write_text(json.dumps(style_validated, indent=2, ensure_ascii=False) if style_validated else "null", encoding="utf-8")
    (out_dir / "story_architect_validated.json").write_text(json.dumps(story_validated, indent=2, ensure_ascii=False) if story_validated else "null", encoding="utf-8")

    return {
        "director_kept": len(director_validated), "director_total": len(director_raw),
        "footage_kept": len(footage_validated), "footage_total": len(footage_raw),
        "style_kept": style_validated is not None,
        "story_kept": story_validated is not None,
        "rejected_claims_log": str(log_path),
    }
