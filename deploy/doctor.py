#!/usr/bin/env python3
"""
Orion doctor: checks everything that went wrong on the first real run, BEFORE it can cost GPU time.
Standard library only (runs with the pod's system python3, before anything is installed).

    python3 deploy/doctor.py --stage pre     # before installing: GPU, disk, settings file, Drive token, network
    python3 deploy/doctor.py --stage post    # after installing: package versions, ffmpeg, model files
    python3 deploy/doctor.py --stage run     # before the batch: model server up, Drive reachable, videos found
    python3 deploy/doctor.py --stage all     # every check that can run now

Each line is PASS / WARN / FAIL with the exact fix. Exit code 2 if anything FAILED (go.sh then stops).
"""
import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WS = Path(os.environ.get("ORION_WORKSPACE", "/workspace"))
ENV_FILE = Path(os.environ.get("ORION_ENV_FILE", WS / "orion.env"))
VENV = Path(os.environ.get("ORION_VENV", WS / "venv"))
results: list[tuple[str, str, str, str]] = []


def add(level, name, detail="", fix=""):
    results.append((level, name, detail, fix))


def load_env_file() -> dict:
    """KEY=VALUE lines of orion.env the way bash would read them (quotes and trailing # comments handled)."""
    env = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            try:
                parts = shlex.split(v, comments=True)
            except ValueError:
                parts = [v.strip().strip("'\"")]
            env[k.strip()] = parts[0] if parts else ""
    except OSError:
        pass
    return env


ENV = {**load_env_file(), **{k: v for k, v in os.environ.items() if k.startswith(("ORION_", "RCLONE_", "MODEL_", "HF_"))}}


def run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def port_state(port: int) -> str:
    """'free' | 'model' (an OpenAI-style server answers) | 'busy'."""
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
    except OSError:
        return "free"
    finally:
        s.close()
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2).read()
        return "model"
    except Exception:  # noqa: BLE001
        return "busy"


# --------------------------------------------------------------------------- PRE
def check_pre():
    py = sys.version_info
    ok = (3, 9) <= py[:2] <= (3, 12)
    add("PASS" if ok else "FAIL", "python version", f"{py.major}.{py.minor}", "" if ok else "vLLM 0.11.1 needs Python 3.9-3.12; pick the 'Runpod Pytorch 2.8.0' template")

    profile = ENV.get("MODEL_PROFILE", "awq48")
    need_vram, need_disk = (44, 60) if profile == "awq48" else (78, 100)
    rc, out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if rc != 0:
        add("FAIL", "GPU", "nvidia-smi not found or failed", "this pod has no GPU: terminate it and deploy a GPU pod (A40 48 GB)")
    else:
        total = 0
        names = []
        for line in out.strip().splitlines():
            n, m = [x.strip() for x in line.rsplit(",", 1)]
            names.append(n)
            total += int(float(m)) // 1024
        add("PASS" if total >= need_vram else "FAIL", "GPU memory", f"{', '.join(names)} = {total} GB (profile {profile} needs >= {need_vram} GB)",
            "" if total >= need_vram else "rent one 48 GB card (A40 / A6000 / L40S)")

    WS.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(str(WS)).free // 10**9
    fresh = not (VENV / "bin" / "python").exists()
    need = need_disk if fresh else 20
    add("PASS" if free >= need else "FAIL", "disk space", f"{free} GB free on {WS} ({'fresh install' if fresh else 'already installed'}, need >= {need})",
        "" if free >= need else "redeploy with a container disk of at least 100 GB")

    if not ENV_FILE.exists():
        add("FAIL", "settings file", f"{ENV_FILE} does not exist", f"cp {REPO}/deploy/orion.env.example {ENV_FILE}")
    else:
        add("PASS", "settings file", str(ENV_FILE))
        for key in ("ORION_DRIVE_INPUT_ID", "ORION_DRIVE_STAGES_ID", "ORION_DRIVE_SFT_ID"):
            if not ENV.get(key):
                add("FAIL", key, "is empty", f"put the folder id into {ENV_FILE}")
        tok = ENV.get("RCLONE_CONFIG_GDRIVE_TOKEN", "")
        if not tok:
            add("FAIL", "Drive token", "RCLONE_CONFIG_GDRIVE_TOKEN is empty", "put the token in a RunPod secret + template variable once (deploy/RUN_GUIDE.md, part 1), or run: python3 " + str(REPO / "deploy/save_drive_token.py"))
        else:
            try:
                t = json.loads(tok)
                bad = [k for k, n in (("access_token", 20), ("refresh_token", 20), ("token_type", 3))
                       if not isinstance(t.get(k), str) or len(t.get(k, "")) < n or "..." in t.get(k, "")]
                if bad:
                    add("FAIL", "Drive token", f"missing/placeholder fields: {bad}", "run: python3 " + str(REPO / "deploy/save_drive_token.py"))
                else:
                    add("PASS", "Drive token", f"valid JSON, refresh_token present ({len(tok)} characters)")
            except json.JSONDecodeError as e:
                add("FAIL", "Drive token", f"not valid JSON ({e.msg})", "run: python3 " + str(REPO / "deploy/save_drive_token.py"))

    for label, url, level in (("PyPI (pip installs)", "https://pypi.org/simple/pip/", "FAIL"),
                              ("Hugging Face (model download)", "https://huggingface.co/", "FAIL"),
                              ("GitHub", "https://github.com/", "WARN"),
                              ("Google Drive API", "https://www.googleapis.com/discovery/v1/apis", "WARN")):
        try:
            urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10)
            add("PASS", f"network: {label}")
        except urllib.error.HTTPError:
            add("PASS", f"network: {label}", "reachable")  # any HTTP answer = reachable
        except Exception as e:  # noqa: BLE001
            add(level, f"network: {label}", type(e).__name__, "the pod cannot reach it: check the pod's network, or try another pod/region")


# --------------------------------------------------------------------------- POST
def check_post():
    py = VENV / "bin" / "python"
    if not py.exists():
        add("FAIL", "python venv", f"{py} missing", f"bash {REPO}/deploy/setup_runpod.sh")
        return
    code = ("import importlib, json;"
            "v={};\n"
            "for m in ('transformers','vllm','faster_whisper','numpy','huggingface_hub'):\n"
            "    try: v[m]=importlib.import_module(m).__version__\n"
            "    except Exception as e: v[m]='ERR '+type(e).__name__\n"
            "print(json.dumps(v))")
    rc, out = run([str(py), "-c", code], timeout=120)
    try:
        v = json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        add("FAIL", "python packages", out[-200:], f"bash {REPO}/deploy/setup_runpod.sh")
        return
    tv = v.get("transformers", "")
    m = re.match(r"(\d+)\.", tv)
    add("PASS" if m and int(m.group(1)) < 5 else "FAIL", "transformers < 5", tv,
        "" if m and int(m.group(1)) < 5 else f'{py} -m pip install "transformers>=4.57.0,<5"  (version 5 breaks vLLM 0.11.1)')
    want = ENV.get("VLLM_VERSION", "0.11.1")
    add("PASS" if v.get("vllm") == want else "FAIL", "vllm version", f"{v.get('vllm')} (want {want})", f"{py} -m pip install vllm=={want}")
    for mod in ("faster_whisper", "numpy", "huggingface_hub"):
        add("PASS" if not str(v.get(mod)).startswith("ERR") else "FAIL", f"package {mod}", str(v.get(mod)), f"bash {REPO}/deploy/setup_runpod.sh")
    for b in ("ffmpeg", "ffprobe"):
        add("PASS" if shutil.which(b) else "FAIL", b, shutil.which(b) or "not found", "apt-get install -y ffmpeg")
    if ENV.get("ORION_DRIVE_INPUT_ID") or ENV.get("ORION_DRIVE_REMOTE"):
        add("PASS" if shutil.which("rclone") else "FAIL", "rclone", shutil.which("rclone") or "not found",
            "curl -fsSL https://rclone.org/install.sh | bash")
    hf = Path(ENV.get("HF_HOME", WS / "hf_cache"))
    model = ENV.get("MODEL_ID", "cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit").replace("/", "--")
    mdir = hf / "hub" / f"models--{model}"
    size = sum(f.stat().st_size for f in mdir.rglob("*") if f.is_file()) // 10**9 if mdir.exists() else 0
    add("PASS" if size >= 10 else "WARN", "model files in cache", f"{size} GB in {mdir}",
        "" if size >= 10 else "not downloaded yet (setup_runpod.sh downloads it)")


# --------------------------------------------------------------------------- RUN
def check_run(limit: int):
    port = int(ENV.get("ORION_OMNI_PORT", "8901"))
    st = port_state(port)
    add("PASS" if st == "model" else "FAIL", f"model server (port {port})", {"model": "answering", "free": "not running", "busy": "port in use by something else"}[st],
        "" if st == "model" else f"bash {REPO}/deploy/start_server.sh   (log: {WS}/vllm_server.log)")
    rc, out = run(["ps", "-eo", "pid,args"])
    n = sum(1 for l in out.splitlines() if "orchestrator/run_pipeline.py" in l and "grep" not in l and str(os.getpid()) not in l.split()[:1])
    add("PASS" if n == 0 else "FAIL", "no other pipeline running", f"{n} running", "" if n == 0 else "wait for it, or stop it: kill $(cat /workspace/orion_run.pid)")
    if ENV.get("ORION_DRIVE_INPUT_ID") or ENV.get("ORION_DRIVE_REMOTE"):
        py = VENV / "bin" / "python"
        rc, out = run([str(py if py.exists() else sys.executable), str(REPO / "deploy" / "drive_check.py")], timeout=300)
        tail = "; ".join(l.strip() for l in out.strip().splitlines()[-3:])
        add("PASS" if rc == 0 else "FAIL", "Google Drive read/write + videos", tail[:300],
            "" if rc == 0 else "token expired or no EDIT access: redo deploy/save_drive_token.py with the Google account that owns the folders")
        m = re.search(r"(\d+) bundles", out)
        if m and limit and int(m.group(1)) < limit:
            add("WARN", "videos in the Drive input folder", f"{m.group(1)} found but you asked for {limit}", "upload the missing videos to the input folder, or the run will process fewer")
    if shutil.which("nvidia-smi"):
        rc, out = run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
        try:
            used, total = [int(x) for x in out.strip().splitlines()[0].split(",")]
            add("PASS" if total - used > 2500 else "FAIL", "GPU memory left for Whisper", f"{(total-used)/1024:.1f} GB free (Whisper large-v3 needs ~4)",
                "" if total - used > 2500 else "lower vLLM's share: add VLLM_EXTRA_ARGS=\"--gpu-memory-utilization 0.75\" to orion.env, restart the server")
        except (ValueError, IndexError):
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["pre", "post", "run", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.stage in ("pre", "all"):
        check_pre()
    if a.stage in ("post", "all"):
        check_post()
    if a.stage in ("run", "all"):
        check_run(a.limit)
    sym = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}
    print(f"\nOrion doctor ({a.stage})")
    for level, name, detail, fix in results:
        print(f" {sym[level]} {level:<4} {name}" + (f" — {detail}" if detail else ""))
        if level != "PASS" and fix:
            print(f"        fix: {fix}")
    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    print(f"\n{len(results)} checks: {fails} failed, {warns} warnings")
    sys.exit(2 if fails else 0)


if __name__ == "__main__":
    main()
