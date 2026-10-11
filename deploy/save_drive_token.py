#!/usr/bin/env python3
"""
Save the Google Drive token into the pod's settings file WITHOUT putting it in any command line.

  1.  cat > /workspace/drive_token.txt        (press Enter; the cursor waits)
  2.  paste the token, press Enter, then Ctrl+D
  3.  python3 deploy/save_drive_token.py

What it does: reads the file, accepts the token even if the terminal split it over several lines or you pasted the
whole "Paste the following into your remote machine ---> {...} <---End paste" line, checks that it is a real token
(valid JSON, has access_token + refresh_token, no "..." placeholder), writes it into orion.env, overwrites and deletes
the file. It never prints the token.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WS = Path(os.environ.get("ORION_WORKSPACE", "/workspace"))
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else WS / "drive_token.txt"
ENV = Path(os.environ.get("ORION_ENV_FILE", WS / "orion.env"))


def die(msg):
    print("✗ " + msg)
    sys.exit(1)


if not SRC.exists():
    die(f"{SRC} not found. First run:  cat > {SRC}   then paste the token, press Enter, then Ctrl+D.")
raw = SRC.read_text(encoding="utf-8", errors="replace")
flat = "".join(raw.split())                       # remove newlines/spaces the terminal may have added
m = re.search(r"\{.*\}", flat)
if not m:
    die("no { ... } token found in the file. Copy only the part from { to } that 'rclone authorize' printed.")
tok = m.group(0)
try:
    t = json.loads(tok)
except json.JSONDecodeError as e:
    die(f"the text is not valid JSON ({e.msg} at position {e.pos}); the paste was probably cut. Redo steps 1-2.")
for k, min_len in (("access_token", 20), ("refresh_token", 20), ("token_type", 3)):
    v = t.get(k)
    if not isinstance(v, str) or len(v) < min_len or "..." in v:
        die(f'"{k}" is missing or still the example placeholder. Redo steps 1-2 with the real token.')
if "..." in tok:
    die("the token still contains '...' (example text).")

if not ENV.exists():
    ex = REPO / "deploy" / "orion.env.example"
    ENV.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ex, ENV)
    print(f"(created {ENV} from the example)")
lines = [l for l in ENV.read_text(encoding="utf-8").splitlines() if not l.startswith("RCLONE_CONFIG_GDRIVE_TOKEN=")]
lines.append("RCLONE_CONFIG_GDRIVE_TOKEN='" + json.dumps(t, separators=(",", ":")) + "'")
ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
try:
    os.chmod(ENV, 0o600)
except OSError:
    pass
try:                                              # overwrite, then remove, the plain-text copy
    SRC.write_bytes(b"\0" * max(1, SRC.stat().st_size))
    SRC.unlink()
except OSError:
    pass
print(f"✓ token saved to {ENV} ({len(tok)} characters; file {SRC.name} wiped). It was not printed.")

if shutil.which("rclone"):
    env = {**os.environ, "RCLONE_CONFIG_GDRIVE_TYPE": "drive", "RCLONE_CONFIG_GDRIVE_SCOPE": "drive",
           "RCLONE_CONFIG_GDRIVE_TOKEN": json.dumps(t, separators=(",", ":"))}
    fid = ""
    for l in ENV.read_text(encoding="utf-8").splitlines():
        if l.startswith("ORION_DRIVE_INPUT_ID="):
            fid = l.split("=", 1)[1].split("#")[0].strip().strip("'\"")
    if fid:
        r = subprocess.run(["rclone", "lsf", f"gdrive,root_folder_id={fid}:", "--max-depth", "1"], env=env, capture_output=True, text=True, timeout=60)
        print("✓ Drive answered; the input folder lists:" if r.returncode == 0 else "✗ Drive did NOT accept the token:")
        print("  " + (r.stdout.strip().replace("\n", "\n  ") if r.returncode == 0 else r.stderr.strip().splitlines()[-1][:200]))
        sys.exit(0 if r.returncode == 0 else 2)
else:
    print("(rclone is not installed yet; the Drive test runs later in go.sh)")
