#!/usr/bin/env python3
"""Pre-flight for Drive: can we reach it, and is every INPUT bundle complete?
Runs without downloading any video. Exit 0 = good to go, 2 = fix first."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator.drive_sync import DriveSync
from orchestrator import preflight

ws = Path(os.environ.get("ORION_WORKSPACE", "/workspace"))
d = DriveSync.from_env(os.environ.get("ORION_DRIVE_REMOTE", ""), ws / "INPUT", ws / "OUTPUT")
if not d.enabled:
    print("No Drive settings (ORION_DRIVE_REMOTE or the three ORION_DRIVE_*_ID) — nothing to check."); sys.exit(0)
print(f"Drive backend: {d.describe()}")
if not d.ensure_remote_dirs():
    print("✗ Cannot reach Drive. Check the rclone token and folder IDs (see deploy/README_RUNPOD.md)."); sys.exit(2)
# Write test: proves the token may WRITE to the stages + final-SFT folders before any GPU time is spent.
import tempfile, shutil, subprocess
marker = Path(tempfile.mkdtemp()) / "_orion_connected.txt"
marker.write_text("Orion pod connected. Safe to ignore.\n", encoding="utf-8")
for label, root in (("stages", d.stage_root), ("final SFT", d.sft_root)):
    if d.local_backend:
        Path(root).mkdir(parents=True, exist_ok=True); shutil.copy(marker, Path(root) / marker.name); ok = True
    else:
        ok = d._rclone(["copyto", str(marker), d._j(root, marker.name), "--stats", "0", "-q"], retries=2) is not None
    print(("✓" if ok else "✗") + f" can write to the {label} folder")
    if not ok:
        print("   (the Google account behind the token needs EDIT access to that folder)"); sys.exit(2)
videos = d.list_input_videos()
if not videos:
    print(f"✗ No videos found in the input folder ({d.in_root})"); sys.exit(2)
bad = 0
for v in videos:
    names = d.list_input_files(v)
    if names is None:
        print(f"✗ {v}: cannot list files"); bad += 1; continue
    problems, warnings = preflight.check_file_list(names)
    n_agents = sum(1 for n in names if n.startswith("P1_A"))
    status = "✓" if not problems else "✗"
    print(f"{status} {v}: {len(names)} files, {n_agents} agent files" + "".join(f"\n    PROBLEM: {p}" for p in problems) + "".join(f"\n    warn: {w}" for w in warnings))
    bad += bool(problems)
print(f"\n{len(videos)} bundles, {bad} with problems")
sys.exit(2 if bad else 0)
