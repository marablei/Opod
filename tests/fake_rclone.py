#!/usr/bin/env python3
"""Tiny rclone look-alike for tests: supports copy / lsf / mkdir on remotes named
`gdrive:...`, mapped to $FAKE_RCLONE_ROOT. Only exists to check that drive_sync builds
valid commands and parses output correctly - it is NOT proof of real rclone semantics."""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(os.environ["FAKE_RCLONE_ROOT"])
EXC = {"_clips", "_frames", "_audio"}
a = sys.argv[1:]
cmd, rest = a[0], a[1:]
VAL = {"--transfers", "--checkers", "--stats", "--exclude", "--max-depth", "--retries"}
pos, flags, i = [], {}, 0
while i < len(rest):
    t = rest[i]
    if t in VAL:
        flags.setdefault(t, []).append(rest[i + 1]); i += 2
    elif t.startswith("-"):
        flags[t] = True; i += 1
    else:
        pos.append(t); i += 1


def real(p):
    return ROOT / p.split(":", 1)[1] if ":" in p and not p.startswith("/") else Path(p)


if cmd == "mkdir":
    real(pos[0]).mkdir(parents=True, exist_ok=True)
elif cmd == "lsf":
    d = real(pos[0])
    if not d.exists():
        sys.exit(3)
    for x in sorted(d.iterdir()):
        if "--dirs-only" in flags and not x.is_dir(): continue
        if "--files-only" in flags and not x.is_file(): continue
        print(x.name + ("/" if x.is_dir() else ""))
elif cmd == "copy":
    src, dst = real(pos[0]), real(pos[1])
    assert "**/_clips/**" in flags.get("--exclude", []), "filters missing"
    depth1 = "--max-depth" in flags
    if not src.exists():
        sys.exit(3)

    def walk(s, d, level=1):
        d.mkdir(parents=True, exist_ok=True)
        for x in s.iterdir():
            if x.is_dir():
                if x.name in EXC or depth1: continue
                walk(x, d / x.name, level + 1)
            else:
                t = d / x.name
                if t.exists() and t.stat().st_size == x.stat().st_size and int(t.stat().st_mtime) == int(x.stat().st_mtime): continue
                if "--update" in flags and t.exists() and t.stat().st_mtime > x.stat().st_mtime: continue
                shutil.copy2(x, t)
    walk(src, dst)
else:
    sys.exit(9)
