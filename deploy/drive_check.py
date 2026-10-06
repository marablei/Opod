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
d = DriveSync(os.environ.get("ORION_DRIVE_REMOTE", ""), ws / "INPUT", ws / "OUTPUT")
if not d.enabled:
    print("ORION_DRIVE_REMOTE not set — nothing to check."); sys.exit(0)
print(f"Drive backend: {d.describe()}")
if not d.ensure_remote_dirs():
    print("✗ Cannot reach Drive. Check the rclone token in orion.env (see deploy/README_RUNPOD.md)."); sys.exit(2)
videos = d.list_input_videos()
if not videos:
    print(f"✗ No video folders under {d.remote}/INPUT"); sys.exit(2)
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
