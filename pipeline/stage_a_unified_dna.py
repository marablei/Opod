"""
Stage A — Unified DNA Builder.

Built against your REAL A01-A30 output structure (inspected directly from
D:\\Sam\\Ais\\Orion_Pipeline\\A1A30Agents on 2026-10-02).

Key finding: A25 (VideoObservationManifest) and A26 (VideoDNABuilder)
already do most of the deterministic aggregation work. Stage A does NOT
reinvent that merge — it builds on top of A26's video_dna record (the
pacing/motion/audio/shot backbone) and attaches the evidence Director,
Footage, Style, and Story Architect actually need to reason over:
transcript, OCR, objects, asset roles, style tags.

Confirmed real envelope (every P1_A*.jsonl line):
    {
      "agent_id": "P1_A0X", "created_at", "input_id",
      "payload": {...agent-specific...},
      "pipeline", "project_id", "provenance", "record_count",
      "record_index", "record_type", "run_id", "schema_version",
      "source_sha256"
    }
Files with record_count > 1 have one JSON object per line (same agent_id,
incrementing record_index). Files with record_count == 1 are a single line
whose payload may itself be a wrapper object (e.g. A25, A26, A28, A29).

Confirmed per-agent roles used here:
    A03  -> source technical probe (duration, fps, resolution)
    A05  -> scenes      (scene_id, start_sec, end_sec)
    A06  -> shots        (shot_id, start_sec, end_sec, mean_luma, motion_score, cut_in_type)
    A07  -> objects      (object_id, label, bbox_norm, confidence, first_seen_sec)
    A09  -> OCR          (region_id, text, bbox_norm, confidence, first_seen_sec)
    A18  -> transcript   (voice_segment: id, start_sec, end_sec, text, words[])
    A19  -> music_beats  (beat strength values)
    A24  -> asset_roles  (graphic/asset lifecycle + structural_role)
    A26  -> video_dna    (audio/motion/pacing profile + temporal_events=shots)
    A28  -> style_tags   (embedding-similarity tags + scores)

Everything else (A01, A01b, A02, A04, A08, A10-A17, A20-A23, A27, A29, A30)
is kept verbatim under "supplementary_agents" so nothing is silently
dropped, even though it isn't elevated to a first-class field yet.
"""
import json
import os
from pathlib import Path


def _read_records(path: Path) -> list[dict]:
    """One JSON object per line. Returns the list of top-level envelopes."""
    records = []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return records
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _payloads(records: list[dict]) -> list[dict]:
    return [r["payload"] for r in records]


def _agent_path(video_input_dir: Path, tag: str) -> Path:
    return video_input_dir / f"P1_{tag}.jsonl"


def _agents_on() -> bool:
    """P1_A*.jsonl agent files are IGNORED by default (ORION_USE_AGENTS=0): everything is
    taken from the video itself by pipeline/self_extract.py. Set ORION_USE_AGENTS=1 to use
    the agent files as before."""
    return os.environ.get("ORION_USE_AGENTS", "0") == "1"


def _load_agent(video_input_dir: Path, tag: str) -> list[dict]:
    if not _agents_on():
        return []
    path = _agent_path(video_input_dir, tag)
    if not path.exists():
        return []
    return _read_records(path)


def _probe_duration(video_input_dir: Path):
    """Duration in seconds from the video file via ffprobe; None if unavailable."""
    import subprocess
    for p in sorted(video_input_dir.iterdir()):
        if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
            try:
                out = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(p)],
                    capture_output=True, text=True, timeout=60, check=True).stdout.strip()
                return float(out)
            except (OSError, ValueError, subprocess.SubprocessError):
                return None
    return None


def run(video_input_dir: Path, video_out_dir: Path):
    video_out_dir.mkdir(parents=True, exist_ok=True)

    all_tags = [p.stem.replace("P1_", "") for p in sorted(video_input_dir.glob("P1_A*.jsonl"))] if _agents_on() else []
    known_tags = {
        "A03", "A05", "A06", "A07", "A09", "A18", "A19", "A24", "A26", "A28",
    }

    # Real input_id (the video's actual name, from the agent envelope) rather
    # than the folder name, so this survives however the orchestrator names
    # per-video folders.
    a01_records = _load_agent(video_input_dir, "A01")
    video_id = a01_records[0]["input_id"] if a01_records else video_input_dir.name

    # --- A03: source technical probe ---
    a03 = _payloads(_load_agent(video_input_dir, "A03"))
    source_info = {}
    if a03:
        v = a03[0].get("video", {})
        au = a03[0].get("audio", {})
        source_info = {
            "duration_sec": a03[0].get("container", {}).get("duration_sec"),
            "fps": v.get("fps_avg"),
            "width": v.get("width"),
            "height": v.get("height"),
            "codec": v.get("codec"),
            "audio_codec": au.get("codec"),
        }

    # --- A26: video_dna (the deterministic backbone) ---
    a26 = _payloads(_load_agent(video_input_dir, "A26"))
    dna = {}
    shots = []
    if a26:
        dna_record = a26[0].get("dna_records", [{}])[0]
        dna = {
            "audio": dna_record.get("audio", {}),
            "motion": dna_record.get("motion", {}),
            "pacing": dna_record.get("pacing", {}),
            "derived_labels": dna_record.get("derived_labels", []),
            "confidence": dna_record.get("confidence", {}),
        }
        shots = [
            {"shot_id": e["shot_id"], "start_sec": e["start_sec"], "end_sec": e["end_sec"],
             "duration_sec": e["duration_seconds"]}
            for e in dna_record.get("temporal_events", []) if e.get("modality_type") == "shot"
        ]

    # --- A06: enrich shots with per-shot technical detail (luma, motion score, cut type) ---
    a06_by_shot = {p["shot_id"]: p for p in _payloads(_load_agent(video_input_dir, "A06"))}
    for shot in shots:
        extra = a06_by_shot.get(shot["shot_id"], {})
        shot["mean_luma"] = extra.get("mean_luma")
        shot["motion_score"] = extra.get("motion_score")
        shot["cut_in_type"] = extra.get("cut_in_type")

    # --- A05: scenes ---
    scenes = [
        {"scene_id": p["scene_id"], "start_sec": p["start_sec"], "end_sec": p["end_sec"]}
        for p in _payloads(_load_agent(video_input_dir, "A05"))
    ]

    # --- A18: transcript (the core text evidence for Director/Footage/Story Architect) ---
    transcript = [
        {"id": p["id"], "start_sec": p["start_sec"], "end_sec": p["end_sec"], "text": p["text"],
         "words": p.get("words", [])}
        for p in _payloads(_load_agent(video_input_dir, "A18"))
    ]

    # --- A09: OCR text regions ---
    ocr_text_regions = [
        {"region_id": p["region_id"], "text": p["text"], "confidence": p.get("confidence"),
         "first_seen_sec": p.get("first_seen_sec"), "bbox_norm": p.get("bbox_norm")}
        for p in _payloads(_load_agent(video_input_dir, "A09"))
    ]

    # --- A07: object detections ---
    objects = [
        {"object_id": p["object_id"], "label": p["label"], "confidence": p.get("confidence"),
         "first_seen_sec": p.get("first_seen_sec"), "bbox_norm": p.get("bbox_norm")}
        for p in _payloads(_load_agent(video_input_dir, "A07"))
    ]

    # --- A24: asset/graphic roles ---
    asset_roles = _payloads(_load_agent(video_input_dir, "A24"))

    # --- A28: style tags ---
    a28 = _payloads(_load_agent(video_input_dir, "A28"))
    style_tags = []
    if a28 and a28[0].get("classifications"):
        style_tags = a28[0]["classifications"][0].get("tags", [])

    # --- A19: music beats ---
    music_beats = _payloads(_load_agent(video_input_dir, "A19"))

    # --- Everything else: kept verbatim, nothing silently dropped ---
    supplementary_agents = {}
    for tag in all_tags:
        if tag not in known_tags:
            supplementary_agents[tag] = _payloads(_load_agent(video_input_dir, tag))

    # A-files are OPTIONAL hints. Record which kinds of evidence actually exist, so the
    # prompts can leave a line out instead of saying "0 shots / no text / no objects"
    # (which a model would read as "nothing is on screen" when really the agent just
    # didn't run or failed).
    def _has(tag):
        return _agents_on() and _agent_path(video_input_dir, tag).exists() and _agent_path(video_input_dir, tag).stat().st_size > 0

    if not _agents_on():
        # No agent files: take facts, shots and transcript from the video itself.
        from pipeline import self_extract
        se = self_extract.run(video_input_dir, video_out_dir)
        source_info = se["source"]
        shots = se["shots"]
        transcript = se["transcript"]
        dna = {"pacing": se["pacing"]} if se.get("pacing") else {}

    evidence_available = {
        "shots": bool(shots) or _has("A06"),
        "ocr": _has("A09"),
        "objects": _has("A07"),
        "style_dna": bool(dna),
    }

    # Video length: prefer A03; otherwise ask the video file itself (ffprobe).
    if not source_info.get("duration_sec"):
        source_info["duration_sec"] = _probe_duration(video_input_dir)

    unified = {
        "video_id": video_id,
        "evidence_available": evidence_available,
        "source": source_info,
        "dna": dna,
        "shots": shots,
        "scenes": scenes,
        "transcript": transcript,
        "ocr_text_regions": ocr_text_regions,
        "objects": objects,
        "asset_roles": asset_roles,
        "style_tags": style_tags,
        "music_beats": music_beats,
        "supplementary_agents": supplementary_agents,
    }

    out_path = video_out_dir / "stage_a_extract" / "unified_dna.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(unified, indent=2))
    return out_path
