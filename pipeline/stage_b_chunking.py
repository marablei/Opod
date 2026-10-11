"""
Stage B — Chunking.

Reads stage_a_extract/unified_dna.json (Stage A's output) and produces THREE views:

  1. stage_b_chunks/segment_chunks.json  — short chunks (target ~14 s, min 6 s, max 22 s), each carrying every
     piece of evidence (shots, transcript, OCR, objects, asset_roles) that falls inside its time range.
     Feeds Director + Footage.
  2. stage_b_chunks/style_rollup.json    — one whole-video aggregate. Feeds Style (one call per video).
  3. stage_b_chunks/story_input.json     — the full ordered transcript, no chunking. Feeds Story Architect.

WHY THE CHUNKING WAS REWRITTEN (found on the first real video, 249 s, 87 shots):
  The old rule broke the transcript at pauses > 0.4 s. Whisper's segments follow each other almost
  without gaps, so it produced only 4 chunks of 35-71 s (17 shots in one chunk). A Director answer for
  "the whole 71 seconds" can only be generic ("man talking to camera"). Editing decisions happen per
  few seconds, so the chunk must be short enough that one answer can be specific.

NEW RULE (all deterministic, no model involved):
  * Candidate cut points = the boundaries between transcript segments (so we never cut mid-segment).
  * A cut is moved onto a real shot cut when one lies within SNAP_SEC (so a chunk starts on a visual cut).
  * Pick, inside the window [start+MIN, start+MAX], the candidate nearest to TARGET, preferring one that
    ends a sentence (. ? ! ؟ …).
  * No candidate in the window (one very long segment, or no speech at all): forced cut at start+TARGET,
    snapped to a shot cut within FORCED_SNAP_SEC if one exists.
  * A short remainder (< MIN) is merged into the previous chunk, so no chunk is a few-second scrap.
  * Chunks tile the video completely: first starts at 0.0, last ends at the video end.
Every chunk records how its end was chosen ("boundary": sentence | segment | forced | end).
All numbers can be changed with ORION_CHUNK_TARGET_SEC / _MIN_SEC / _MAX_SEC (they are written into
style_rollup.json so each run records which settings it used).
"""
import json
import os
from pathlib import Path

TARGET_CHUNK_SEC = float(os.environ.get("ORION_CHUNK_TARGET_SEC", "14"))
MIN_CHUNK_SEC = float(os.environ.get("ORION_CHUNK_MIN_SEC", "6"))
MAX_CHUNK_SEC = float(os.environ.get("ORION_CHUNK_MAX_SEC", "22"))
SNAP_SEC = 0.6
FORCED_SNAP_SEC = 1.5
SENTENCE_END = (".", "?", "!", "\u061f", "\u2026", "\u3002", "\uff01", "\uff1f")


def _overlaps(item_start: float, item_end: float, chunk_start: float, chunk_end: float) -> bool:
    return item_start < chunk_end and item_end > chunk_start


def _overlaps_point(point_sec: float, chunk_start: float, chunk_end: float) -> bool:
    return chunk_start <= point_sec < chunk_end


def _nearest_cut(t: float, cuts: list[float], max_dist: float) -> float | None:
    best = min(cuts, key=lambda c: abs(c - t), default=None)
    return best if best is not None and abs(best - t) <= max_dist else None


def _candidates(transcript: list[dict], shot_cuts: list[float]) -> list[tuple[float, bool]]:
    """[(cut_time, ends_a_sentence)] at every boundary between two transcript segments."""
    out = []
    for prev, cur in zip(transcript, transcript[1:]):
        lo, hi = prev["end_sec"], max(prev["end_sec"], cur["start_sec"])
        t = (lo + hi) / 2.0
        snapped = _nearest_cut(t, [c for c in shot_cuts if lo - 0.2 <= c <= hi + 0.2], SNAP_SEC)
        out.append((snapped if snapped is not None else t, prev["text"].rstrip().endswith(SENTENCE_END)))
    return out


def build_chunk_ranges(transcript: list[dict], shot_cuts: list[float], video_end: float) -> list[tuple[float, float, str]]:
    """Returns [(start, end, boundary_kind)] tiling [0, video_end]."""
    cands = _candidates(transcript, shot_cuts)
    ranges: list[tuple[float, float, str]] = []
    start = 0.0
    while video_end - start > MAX_CHUNK_SEC:
        window = [(c, sent) for c, sent in cands if start + MIN_CHUNK_SEC <= c <= start + MAX_CHUNK_SEC and c < video_end]
        if window:
            sentence_ones = [w for w in window if w[1]]
            pool = sentence_ones or window
            cut, sent = min(pool, key=lambda w: abs((w[0] - start) - TARGET_CHUNK_SEC))
            kind = "sentence" if sent else "segment"
        else:
            raw = start + TARGET_CHUNK_SEC
            snapped = _nearest_cut(raw, shot_cuts, FORCED_SNAP_SEC)
            cut = snapped if snapped is not None and snapped - start >= MIN_CHUNK_SEC else raw
            kind = "forced"
        ranges.append((start, cut, kind))
        start = cut
    if video_end - start < MIN_CHUNK_SEC and ranges:
        s, _e, k = ranges[-1]
        ranges[-1] = (s, video_end, "end")
    else:
        ranges.append((start, video_end, "end"))
    return ranges


def _build_chunks(transcript: list[dict], shots: list[dict], video_end_sec: float):
    shot_cuts = sorted(s["start_sec"] for s in shots[1:]) if len(shots) > 1 else []
    result = []
    for c_start, c_end, kind in build_chunk_ranges(transcript, shot_cuts, video_end_sec):
        segs = [t for t in transcript if _overlaps(t["start_sec"], t["end_sec"], c_start, c_end)]
        result.append((c_start, c_end, segs, kind))
    return result


def run(video_input_dir: Path, video_out_dir: Path):
    dna_path = video_out_dir / "stage_a_extract" / "unified_dna.json"
    if not dna_path.exists():
        raise FileNotFoundError(f"Stage A output not found: {dna_path}. Run Stage A first.")
    dna = json.loads(dna_path.read_text(encoding="utf-8"))

    chunks_dir = video_out_dir / "stage_b_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    lang_code = (dna.get("language") or {}).get("code")
    shots = dna.get("shots", [])
    transcript = dna.get("transcript", [])
    ocr_regions = dna.get("ocr_text_regions", [])
    objects = dna.get("objects", [])
    asset_roles = dna.get("asset_roles", [])
    video_end_sec = dna.get("source", {}).get("duration_sec") or (transcript[-1]["end_sec"] if transcript else 0.0)

    # --- View 1: short chunks (sentence-bounded, snapped to shot cuts) ---
    pause_chunks = _build_chunks(transcript, shots, video_end_sec)

    segment_chunks = []
    for i, (c_start, c_end, chunk_transcript, boundary) in enumerate(pause_chunks, start=1):
        chunk_id = f"CHUNK_{i:03d}"

        chunk_shots = [s for s in shots if _overlaps(s["start_sec"], s["end_sec"], c_start, c_end)]
        chunk_ocr = [o for o in ocr_regions if _overlaps_point(o.get("first_seen_sec", -1), c_start, c_end)]
        chunk_objects = [o for o in objects if _overlaps_point(o.get("first_seen_sec", -1), c_start, c_end)]
        chunk_assets = [
            a for a in asset_roles
            if _overlaps(a.get("evidence", {}).get("time_range", {}).get("start_sec", 0),
                         a.get("evidence", {}).get("time_range", {}).get("end_sec", 0), c_start, c_end)
        ]

        segment_chunks.append({
            "chunk_id": chunk_id,
            "start_sec": round(c_start, 3),
            "end_sec": round(c_end, 3),
            "duration_sec": round(c_end - c_start, 3),
            "boundary": boundary,
            "shots": chunk_shots,
            "transcript_text": " ".join(t["text"] for t in chunk_transcript).strip(),
            "transcript_segments": chunk_transcript,
            "ocr_text_regions": chunk_ocr,
            "objects": chunk_objects,
            "asset_roles": chunk_assets,
            "evidence_available": dna.get("evidence_available", {}),
            "language": lang_code,
        })

    (chunks_dir / "segment_chunks.json").write_text(json.dumps(segment_chunks, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- View 2: channel-level rollup (whole video) ---
    style_rollup = {
        "video_id": dna.get("video_id"),
        "duration_sec": dna.get("source", {}).get("duration_sec"),
        "dna": dna.get("dna", {}),
        "evidence_available": dna.get("evidence_available", {}),
        "language": lang_code,
        "style_tags": dna.get("style_tags", []),
        "pacing": (dna.get("dna") or {}).get("pacing"),
        "chunking": {"target_sec": TARGET_CHUNK_SEC, "min_sec": MIN_CHUNK_SEC, "max_sec": MAX_CHUNK_SEC},
        "counts": {
            "num_scenes": len(dna.get("scenes", [])),
            "num_shots": len(shots),
            "num_chunks": len(segment_chunks),
            "num_transcript_segments": len(transcript),
            "num_ocr_regions": len(ocr_regions),
            "num_objects": len(objects),
            "num_asset_roles": len(asset_roles),
        },
    }
    (chunks_dir / "style_rollup.json").write_text(json.dumps(style_rollup, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- View 3: whole-transcript view (no chunking) ---
    story_input = {
        "video_id": dna.get("video_id"),
        "duration_sec": dna.get("source", {}).get("duration_sec"),
        "language": lang_code,
        "transcript": transcript,
    }
    (chunks_dir / "story_input.json").write_text(json.dumps(story_input, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "segment_chunks": chunks_dir / "segment_chunks.json",
        "style_rollup": chunks_dir / "style_rollup.json",
        "story_input": chunks_dir / "story_input.json",
        "num_chunks": len(segment_chunks),
    }
