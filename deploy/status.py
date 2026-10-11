#!/usr/bin/env python3
"""Compact terminal view of the run (same real data as the dashboard).   python3 deploy/status.py   |   watch -n 5 python3 deploy/status.py"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator import run_view  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("ORION_OUTPUT_DIR", Path(os.environ.get("ORION_WORKSPACE", "/workspace")) / "OUTPUT"))
s = run_view.snapshot(out)
r, m = s["run"], s["machine"]
fmt = lambda x: "-" if x is None else (f"{x/60:.1f} min" if x >= 90 else f"{x:.0f} s")
state = "RUNNING" if r["running"] else ("not started" if not r["started_at"] else "finished/idle")
print(f"Orion: {state} | elapsed {fmt(r['elapsed_sec'])} | last event {fmt(r['last_event_age_sec'])} ago | this run {r.get('completed') or 0}/{r.get('total_videos') or '?'} videos")
g = (m["gpu"] or [None])[0]
print(f"model server: {'up' if m['model_server']['up'] else 'DOWN'} | GPU: " + (f"{g['util_pct']}% {g['mem_used_mb']/1024:.0f}/{g['mem_total_mb']/1024:.0f} GB" if g else "n/a")
      + (f" | disk free {m['disk']['free_gb']} GB" if m["disk"] else "") + (f" | est. cost ${r['cost_so_far']}" if r["cost_so_far"] is not None else ""))
ph = s["setup"]["phases"]
if ph and not r["running"]:
    print(f"setup: last step '{ph[-1]['phase']}' {fmt(time.time()-ph[-1]['t'])} ago")
print()
sym = {"done": "✓", "in_progress": "…", "failed": "✗", "pending": "·"}
print(f"{'video':<34} " + " ".join(k[-1].upper() for k in run_view.STAGES) + "   time   model calls")
for v in s["videos"]:
    p = v["stage_c_progress"]
    calls = f"{p['done']}/{p['total']} (re-asked {p['retried']}, gave up {p['gave_up']})" if p else ""
    print(f"{v['name'][:34]:<34} " + " ".join(sym[v['stages'][k]] for k in run_view.STAGES) + f"   {fmt(v['total_sec']):<7}{calls}" + ("  <== now" if v["is_current"] else ""))
    for w in v["warnings"]:
        print(f"    ⚠ {w}")
    if v["failed_stage"]:
        print("    ✗ " + (v["errors_tail"][-1] if v["errors_tail"] else "failed"))
print(f"\ncorpus rows: " + ", ".join(f"{k.replace('_sft.jsonl','')} {c}" for k, c in s["corpus_rows"].items()))
for e in s["recent_problems"][-3:]:
    print(f"problem: {e.get('video')} {e['kind']} {e.get('stage','')}: {e.get('error','')[:160]}")
