"""
Cheap checks that run BEFORE spending GPU time on a video, and a data-quality
report written right after Stage A.

Why this exists: Stage A silently treats a missing agent file as "no data"
(returns []), so a video with a missing A18 would flow through the whole
pipeline with an empty transcript and nobody would notice until the training
rows looked thin. Better to stop (or at least warn loudly) up front.

This does NOT verify that agent output is *correct* (e.g. OCR misreads) —
that's still an open problem. It only catches missing/truncated data.
"""
import json
import os
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

# Hard requirement: the transcript. Chunk boundaries come from its timestamps, and it is the
# text the Director/Footage/Story prompts are built on. Everything else is an optional HINT:
# the model watches the video itself, and prompts leave out evidence that is missing.
REQUIRED_AGENTS = ["A18"]
# Used if present; absence only produces a warning.
EXPECTED_AGENTS = ["A03", "A05", "A06", "A07", "A09", "A19", "A24", "A26", "A28"]

MIN_TRANSCRIPT_COVERAGE = 0.90


def check_file_list(names: list[str]) -> tuple[list[str], list[str]]:
    """(problems, warnings) from a list of file names in one video's bundle."""
    problems, warnings = [], []
    if not any(Path(n).suffix.lower() in VIDEO_EXTENSIONS for n in names):
        problems.append(f"no video file found (expected one of {sorted(VIDEO_EXTENSIONS)})")
    if os.environ.get("ORION_USE_AGENTS", "0") != "1":
        return problems, warnings  # agent files are ignored: only the video matters
    have = {Path(n).stem.replace("P1_", "") for n in names if n.startswith("P1_A") and n.endswith(".jsonl")}
    for tag in REQUIRED_AGENTS:
        if tag not in have:
            problems.append(f"missing required agent file P1_{tag}.jsonl")
    for tag in EXPECTED_AGENTS:
        if tag not in have:
            warnings.append(f"missing P1_{tag}.jsonl (continuing without it)")
    return problems, warnings


def check_input_dir(video_input_dir: Path) -> tuple[list[str], list[str]]:
    if not video_input_dir.exists():
        return [f"input folder {video_input_dir} does not exist"], []
    problems, warnings = check_file_list([p.name for p in video_input_dir.iterdir() if p.is_file()])
    for p in (video_input_dir.glob("P1_A*.jsonl") if os.environ.get("ORION_USE_AGENTS", "0") == "1" else []):
        if p.stat().st_size == 0:
            problems.append(f"{p.name} is empty (0 bytes)")
    return problems, warnings


def quality_report(video_out_dir: Path) -> dict:
    """Written to <video>/_data_quality.json after Stage A. Never raises."""
    report = {"warnings": []}
    try:
        dna = json.loads((video_out_dir / "01_unified_dna.json").read_text())
        duration = (dna.get("source") or {}).get("duration_sec") or 0.0
        transcript = dna.get("transcript", [])
        last_end = max((t.get("end_sec", 0.0) for t in transcript), default=0.0)
        coverage = (last_end / duration) if duration else None
        report.update({
            "duration_sec": duration,
            "transcript_segments": len(transcript),
            "transcript_last_end_sec": last_end,
            "transcript_coverage": round(coverage, 3) if coverage is not None else None,
            "shots": len(dna.get("shots", [])),
            "ocr_regions": len(dna.get("ocr_text_regions", [])),
            "objects": len(dna.get("objects", [])),
        })
        if not transcript:
            report["warnings"].append("transcript is EMPTY (A18 produced nothing usable)")
        elif coverage is not None and coverage < MIN_TRANSCRIPT_COVERAGE:
            report["warnings"].append(
                f"transcript only covers {coverage:.0%} of the video "
                f"(ends at {last_end:.0f}s of {duration:.0f}s) — likely truncated A18 run"
            )
        if report["shots"] == 0:
            report["warnings"].append("no shots detected")
    except Exception as e:  # report must never break the run
        report["warnings"].append(f"quality report failed: {e}")
    (video_out_dir / "_data_quality.json").write_text(json.dumps(report, indent=2))
    return report
