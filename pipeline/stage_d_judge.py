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

Rule-based checks implemented here (Tier 3 LLM judge stays stubbed, as in
Pipeline 1 — only wired in later for cases these rules can't resolve):
  - Director: chunk_id must exist among this video's real chunks. Director
    no longer references asset_ids directly — it states footage_need/
    audio_need as search queries (no library access, by design), so there's
    nothing to validate against a candidate pool here. Just structural
    presence of those fields is checked.
  - Footage: chunk_id must exist; any referenced asset_id must exist in
    that chunk's actual candidate pool (currently always empty until the
    Python retrieval layer is wired in — so today, any non-empty asset_ids
    reference fails, which is correct: there ARE no real candidates yet).
  - Story Architect: every segment_id cited must match a real transcript
    segment id for this video.
  - Style: video_id must match; required fields must be present.
"""
import json
import re
import time
from pathlib import Path

STYLE_REQUIRED_FIELDS = [
    "pacing_target", "typography_system", "color_lut",
    "graphic_density", "sound_design_intensity", "map_motion_frequency",
]


def _log_rejection(log_path: Path, stage: str, detail: str):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{stage}] {detail}\n")


def _validate_director(entries: list[dict], valid_chunk_ids: set, log_path: Path) -> list[dict]:
    validated = []
    for entry in entries:
        chunk_id = entry.get("chunk_id")
        if chunk_id not in valid_chunk_ids:
            _log_rejection(log_path, "director", f"chunk_id '{chunk_id}' not found in this video's chunks — rejected")
            continue

        missing = [f for f in ("footage_need", "audio_need") if f not in entry]
        if missing:
            _log_rejection(log_path, "director", f"chunk_id '{chunk_id}' missing required fields {missing} — rejected")
            continue

        validated.append(entry)
    return validated


def _validate_footage(entries: list[dict], valid_chunk_ids: set, log_path: Path) -> list[dict]:
    validated = []
    for entry in entries:
        chunk_id = entry.get("chunk_id")
        if chunk_id not in valid_chunk_ids:
            _log_rejection(log_path, "footage", f"chunk_id '{chunk_id}' not found in this video's chunks — rejected")
            continue

        asset_ids = entry.get("asset_ids") or entry.get("confirmed_asset_ids") or []
        if asset_ids:
            # TODO: once the Python retrieval layer is wired in, check
            # against that chunk's actual candidate pool instead of
            # rejecting outright.
            _log_rejection(log_path, "footage",
                            f"chunk_id '{chunk_id}' references asset_ids {asset_ids} but no candidate "
                            f"pool exists yet for this chunk (retrieval not wired in) — rejected")
            continue

        validated.append(entry)
    return validated


def _normalize_segment_id(raw) -> int | None:
    """Transcript ids are integers (1, 2, 3 ...) and the prompt shows them as
    'seg_1'. A real model may answer 7, "7", "seg_7" or "SEG_7"; all mean the same
    segment. Returns the integer id, or None if it isn't a segment reference at all
    (e.g. the 'FAKE' placeholder). Found by the mock-server simulation: without this,
    almost every real Story Architect answer would have been rejected."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    m = re.fullmatch(r"\s*(?:seg[_\-\s]?)?0*(\d+)\s*", str(raw), flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _validate_story_architect(story: dict, valid_segment_ids: set, log_path: Path) -> dict | None:
    all_points = []
    for key in ("act_1_hook", "act_1_anchor", "act_3_payoff", "act_3_closing"):
        if key in story:
            all_points.append(story[key])
    all_points.extend(story.get("act_2_escalation", []))

    if not all_points:
        return None

    normalized = []
    for point in all_points:
        raw = point.get("segment_id") if isinstance(point, dict) else None
        seg = _normalize_segment_id(raw)
        if seg is None or seg not in valid_segment_ids:
            _log_rejection(log_path, "story_architect",
                            f"segment_id '{raw}' not found in this video's transcript — rejected")
            return None  # any ungrounded point invalidates the whole structure, not a partial keep
        normalized.append((point, seg))

    # Store every id in the one format the prompt uses, so training targets are consistent.
    for point, seg in normalized:
        point["segment_id"] = f"seg_{seg}"
    return story


def _validate_style(style: dict, expected_video_id: str, log_path: Path) -> dict | None:
    if style.get("video_id") != expected_video_id:
        _log_rejection(log_path, "style", f"video_id mismatch: got '{style.get('video_id')}' expected '{expected_video_id}'")
        return None
    missing = [f for f in STYLE_REQUIRED_FIELDS if f not in style]
    if missing:
        _log_rejection(log_path, "style", f"missing required fields: {missing}")
        return None
    return style


def run(video_input_dir: Path, video_out_dir: Path):
    raw_dir = video_out_dir / "stage_c_omni"
    chunks_dir = video_out_dir / "stage_b_chunks"
    if not raw_dir.exists():
        raise FileNotFoundError(f"Stage C output not found: {raw_dir}. Run Stage C first.")

    segment_chunks = json.loads((chunks_dir / "segment_chunks.json").read_text(encoding="utf-8"))
    story_input = json.loads((chunks_dir / "story_input.json").read_text(encoding="utf-8"))
    valid_chunk_ids = {c["chunk_id"] for c in segment_chunks}
    valid_segment_ids = {t["id"] for t in story_input["transcript"]}
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

    director_validated = _validate_director(director_raw, valid_chunk_ids, log_path)
    footage_validated = _validate_footage(footage_raw, valid_chunk_ids, log_path)
    style_validated = _validate_style(style_raw, video_id, log_path)
    story_validated = _validate_story_architect(story_raw, valid_segment_ids, log_path)

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
