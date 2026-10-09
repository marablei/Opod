#!/usr/bin/env python3
"""Compact progress view from the OUTPUT folder.  watch -n 10 python deploy/status.py"""
import json
import os
import sys
import time
from pathlib import Path

out = Path(os.environ.get("ORION_OUTPUT_DIR", Path(os.environ.get("ORION_WORKSPACE", "/workspace")) / "OUTPUT"))
if len(sys.argv) > 1:
    out = Path(sys.argv[1])
STAGES = ["stage_a", "stage_b", "stage_c", "stage_d", "stage_e", "stage_f"]

batch = out / "_batch_status.json"
if batch.exists():
    b = json.loads(batch.read_text(encoding="utf-8"))
    eta = b.get("eta_sec_remaining") or 0
    print(f"This run: {b['completed']}/{b['total_videos']} videos | now: {b.get('current_video')} @ {b.get('current_stage')}"
          f" | avg {((b.get('avg_video_sec') or 0)/60):.1f} min/video | ETA {eta/3600:.2f} h"
          f" | updated {int(time.time()-b['updated_at'])}s ago")
print(f"\n{'video':<28} " + " ".join(s[-1].upper() for s in STAGES) + "   total_min")
done = 0
rows = sorted(p for p in out.iterdir() if p.is_dir() and (p / "_state.json").exists()) if out.exists() else []
for d in rows:
    st = json.loads((d / "_state.json").read_text(encoding="utf-8"))
    tm = json.loads((d / "_timing.json").read_text(encoding="utf-8")) if (d / "_timing.json").exists() else {}
    sym = {"done": "✓", "in_progress": "…", "failed": "✗", "pending": "·"}
    marks = " ".join(sym.get(st.get(s, "pending"), "?") for s in STAGES)
    full = all(st.get(s) == "done" for s in STAGES)
    done += full
    q = d / "_data_quality.json"
    warn = " ⚠ " + "; ".join(json.loads(q.read_text(encoding="utf-8")).get("warnings", []))[:60] if q.exists() and json.loads(q.read_text(encoding="utf-8")).get("warnings") else ""
    print(f"{d.name:<28} {marks}   {sum(v for k, v in tm.items() if k.endswith('_sec'))/60:6.1f}{warn}")
print(f"\n{done}/{len(rows)} videos fully done in {out}")
