#!/usr/bin/env python3
"""
Rebuild the 4 final training files from the per-video results, with no GPU and no model.

Every finished video keeps its own final rows in  <stages folder>/<video>/stage_f_final/*.jsonl .
If a batch crashes, or you just want to re-aggregate (say after deleting a bad video), run this on any
computer after downloading the "Each stage" folder from Drive (only the stage_f_final folders are needed):

    python orchestrator/aggregate_offline.py --stages "D:/Data sets/Each stage"
    python orchestrator/aggregate_offline.py --stages ./stages --out ./final      # write elsewhere
    python orchestrator/aggregate_offline.py --stages ./stages --only video_001,video_004

Only videos whose _state.json says every stage is done are included (use --include-unfinished to override).
Nothing is modified or deleted in the stages folder.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import stage_g_aggregate  # noqa: E402
from orchestrator.state import STAGES  # noqa: E402


def main():
    for stream in (sys.stdout, sys.stderr):  # Arabic video names must not crash a Windows console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", required=True, help="folder with one subfolder per video (the 'Each stage' folder)")
    ap.add_argument("--out", default="", help="where to write the 4 final files (default: next to --stages, in 'final_sft')")
    ap.add_argument("--only", default="", help="comma-separated video folder names")
    ap.add_argument("--include-unfinished", action="store_true")
    a = ap.parse_args()

    stages = Path(a.stages)
    out = Path(a.out) if a.out else stages.parent / "final_sft"
    if not stages.exists():
        sys.exit(f"stages folder not found: {stages}")

    vids, skipped = [], []
    for d in sorted(p for p in stages.iterdir() if p.is_dir()):
        if a.only and d.name not in {x.strip() for x in a.only.split(",")}:
            continue
        if not (d / "stage_f_final").exists():
            skipped.append((d.name, "no stage_f_final folder"))
            continue
        try:
            st = json.loads((d / "_state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            st = {}
        if not a.include_unfinished and not all(st.get(s) == "done" for s in STAGES):
            skipped.append((d.name, "not fully done"))
            continue
        vids.append(d.name)

    # Stage G reads <stages>/<video>/stage_f_final and writes ONLY into --out, so the stages folder
    # is never touched (and no symlinks are needed: they fail on Windows without admin rights).
    stage_g_aggregate.run(run_dir=stages, video_ids=vids, out_dir=out)
    print(f"\n{len(vids)} videos aggregated -> {out}")
    for v, why in skipped:
        print(f"  skipped {v}: {why}")


if __name__ == "__main__":
    main()
