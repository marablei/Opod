"""
Stage G — Corpus Aggregation (new stage, added after the shard/aggregate gap
was caught: Stage E produces 4 shard files PER VIDEO, but LoRA fine-tuning
needs 4 FINAL files total, each aggregated across all videos).

Runs once at the end of the batch, not per-video.
"""
import json
from pathlib import Path

SFT_NAMES = ["director_sft.jsonl", "style_sft.jsonl", "footage_sft.jsonl", "story_architect_sft.jsonl"]


def run(run_dir: Path, video_ids: list[str]):
    for name in SFT_NAMES:
        out_path = run_dir / name
        with open(out_path, "w") as out_f:
            rows_written = 0
            for video_id in video_ids:
                shard_path = run_dir / video_id / "05_sft" / name
                if not shard_path.exists():
                    continue  # video failed before Stage E, or had no rows for this LoRA
                with open(shard_path) as in_f:
                    for line in in_f:
                        line = line.strip()
                        if not line:
                            continue
                        out_f.write(line + "\n")
                        rows_written += 1
        print(f"[Stage G] {name}: {rows_written} rows from {len(video_ids)} videos -> {out_path}")
