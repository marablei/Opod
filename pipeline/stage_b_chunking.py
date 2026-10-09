"""
Stage B — Chunking.

Reads stage_a_extract/unified_dna.json (Stage A's output) and produces THREE views,
matching the architecture diagram exactly:

  1. stage_b_chunks/segment_chunks.json  — chunks bounded by transcript pauses,
     each carrying every piece of evidence (shots, transcript, OCR,
     objects, asset_roles) that falls inside that chunk's time range.
     Feeds Director + Footage.
  2. stage_b_chunks/style_rollup.json    — one whole-video aggregate (pacing,
     motion, audio profile, style tags, counts). Feeds Style — one call
     per video, not per segment.
  3. stage_b_chunks/story_input.json     — the full ordered transcript, no
     chunking at all. Feeds Story Architect's whole-script reasoning.

CHUNK BOUNDARY — corrected after checking real data:
Chunk boundaries come from PAUSES IN THE TRANSCRIPT, not from A05's raw
scene cuts. A05's 189 scenes are shot-level cinematographic cuts (cut
whenever the camera/composition changes) — using them directly would mean
~189 Director/Footage calls per video, not the ~20-40 the whole time/cost
plan assumes, and it would cut mid-sentence since a cinematographic cut
doesn't care where a sentence ends.

PAUSE_THRESHOLD_SEC = 0.4 was picked by checking this video's actual gap
distribution: gaps between consecutive transcript segments. At 0.4s, 31
natural breaks occur across a 20-minute video — squarely in the 20-40
target range, and grounded in real data rather than guessed. A new video
with different pacing will naturally produce a different chunk count with
this same rule; that's intended, not a bug.
"""
import json
from pathlib import Path

PAUSE_THRESHOLD_SEC = 0.4

# Safety cap: found empirically that pure pause-based chunking can produce
# a chunk spanning most of the video if no pause above PAUSE_THRESHOLD_SEC
# occurs for a long stretch (confirmed on real data: one chunk hit 608s /
# over 10 minutes before this cap was added). Past this duration, force a
# break at the best available gap even if it's below the pause threshold,
# so Director/Footage never receive one call's worth of half a video.
MAX_CHUNK_SEC = 75.0


def _overlaps(item_start: float, item_end: float, chunk_start: float, chunk_end: float) -> bool:
    return item_start < chunk_end and item_end > chunk_start


def _overlaps_point(point_sec: float, chunk_start: float, chunk_end: float) -> bool:
    return chunk_start <= point_sec < chunk_end


def _build_pause_bounded_chunks(transcript: list[dict], video_end_sec: float) -> list[tuple[float, float, list[dict]]]:
    """Returns [(chunk_start, chunk_end, [transcript_segments_in_chunk]), ...].

    Primary rule: break at a transcript pause > PAUSE_THRESHOLD_SEC.
    Then a separate post-pass (_enforce_max_duration) splits anything that
    still exceeds MAX_CHUNK_SEC — covers both a long stretch with no pause
    big enough to trigger, AND a long stretch with NO transcript at all
    (confirmed on real data: this video's A18 transcript stops at 599.88s
    of a 1208s video, leaving a trailing ~608s span with zero speech and
    zero OCR — looks like an upstream transcription truncation, not a
    real silent passage. Worth checking that agent's run for this video.
    Either way, Stage B can't assume every video's transcript covers the
    full duration, so the final chunk always gets capped here too.)
    """
    if not transcript:
        # No speech found (music-only video, or speech-to-text off): fixed windows.
        return _enforce_max_duration([(0.0, video_end_sec, [])])

    raw_chunks = []
    current_segs = [transcript[0]]
    for prev, cur in zip(transcript, transcript[1:]):
        gap = cur["start_sec"] - prev["end_sec"]
        if gap > PAUSE_THRESHOLD_SEC:
            raw_chunks.append(current_segs)
            current_segs = [cur]
        else:
            current_segs.append(cur)
    raw_chunks.append(current_segs)

    result = []
    for i, segs in enumerate(raw_chunks):
        start = segs[0]["start_sec"]
        end = raw_chunks[i + 1][0]["start_sec"] if i + 1 < len(raw_chunks) else video_end_sec
        result.append((start, end, segs))

    return _enforce_max_duration(result)


def _enforce_max_duration(chunks: list[tuple[float, float, list[dict]]]) -> list[tuple[float, float, list[dict]]]:
    """Splits any chunk longer than MAX_CHUNK_SEC into equal-sized pieces,
    re-filtering transcript segments into each new sub-range. Handles the
    "long stretch with no transcript at all" case, which can't be fixed by
    picking a better in-loop pause since there's no pause data to pick from.
    """
    final = []
    for start, end, segs in chunks:
        duration = end - start
        if duration <= MAX_CHUNK_SEC:
            final.append((start, end, segs))
            continue
        n_pieces = max(2, int(duration // MAX_CHUNK_SEC) + 1)
        piece_len = duration / n_pieces
        for p in range(n_pieces):
            p_start = start + p * piece_len
            p_end = start + (p + 1) * piece_len if p < n_pieces - 1 else end
            p_segs = [s for s in segs if _overlaps(s["start_sec"], s["end_sec"], p_start, p_end)]
            final.append((p_start, p_end, p_segs))
    return final


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

    # --- View 1: segment-level chunks (pause-bounded) ---
    pause_chunks = _build_pause_bounded_chunks(transcript, video_end_sec)

    segment_chunks = []
    for i, (c_start, c_end, chunk_transcript) in enumerate(pause_chunks, start=1):
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
