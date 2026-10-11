"""
Stage G — Corpus Aggregation (new stage, added after the shard/aggregate gap
was caught: Stage E produces 4 shard files PER VIDEO, but LoRA fine-tuning
needs 4 FINAL files total, each aggregated across all videos).

Runs once at the end of the batch, not per-video.
"""
import json
import os
from pathlib import Path

SFT_NAMES = ["director_sft.jsonl", "style_sft.jsonl", "footage_sft.jsonl", "story_architect_sft.jsonl"]


def _language_summary(run_dir: Path, video_ids: list[str]) -> dict:
    """{language code: number of videos}, from each video's self_extract.json (for the log only)."""
    counts: dict = {}
    for v in video_ids:
        try:
            se = json.loads((run_dir / v / "stage_a_extract" / "self_extract.json").read_text(encoding="utf-8"))
            code = (se.get("language") or {}).get("code") or "unknown"
        except (OSError, json.JSONDecodeError):
            code = "unknown"
        counts[code] = counts.get(code, 0) + 1
    return counts


def run(run_dir: Path, video_ids: list[str], out_dir: Path | None = None):
    """Reads <run_dir>/<video>/stage_f_final/*.jsonl; writes the 4 corpus files into out_dir (default run_dir)."""
    out_dir = out_dir or run_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    quarantined = []
    if os.environ.get("ORION_INCLUDE_QUARANTINED", "0") != "1":
        for v in video_ids:
            try:
                verdict = json.loads((run_dir / v / "_video_verdict.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if verdict.get("state") == "quarantined":
                quarantined.append({"video": v, "reasons": verdict.get("reasons", [])})
        if quarantined:
            skip = {q["video"] for q in quarantined}
            video_ids = [v for v in video_ids if v not in skip]
            print(f"[Stage G] QUARANTINED, left out of the final files (kept on disk): {sorted(skip)}")
    (out_dir / "_quarantined_videos.json").write_text(json.dumps(quarantined, indent=2, ensure_ascii=False), encoding="utf-8")
    for name in SFT_NAMES:
        out_path = out_dir / name
        with open(out_path, "w", encoding="utf-8") as out_f:
            rows_written = 0
            for video_id in video_ids:
                shard_path = run_dir / video_id / "stage_f_final" / name
                if not shard_path.exists():
                    continue  # video failed before Stage E, or had no rows for this LoRA
                with open(shard_path, encoding="utf-8") as in_f:
                    for line in in_f:
                        line = line.strip()
                        if not line:
                            continue
                        out_f.write(line + "\n")
                        rows_written += 1
        print(f"[Stage G] {name}: {rows_written} rows from {len(video_ids)} videos -> {out_path}")
    langs = _language_summary(run_dir, video_ids)
    if langs:
        print(f"[Stage G] videos by spoken language: {langs}")
