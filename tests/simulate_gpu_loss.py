#!/usr/bin/env python3
"""
End-to-end resume simulation (no GPU, no network):

  phase 1  "pod A": mock model server dies after N calls mid-Stage-C
  phase 2  "pod B": brand-new empty disks, same Drive folder -> must resume
                    (Stage C continues from its cache, not from zero)
  phase 3  "pod C": everything finished -> must do ZERO model calls

Usage: python tests/simulate_gpu_loss.py [--backend folder|rclone|ids] [--fixture DIR]
  ids = your own three Drive folders by ID (input / stages / final SFT), with one video as a LOOSE file
        and one in a subfolder, through the rclone stand-in.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--backend", default="folder", choices=["folder", "rclone", "ids"])
ap.add_argument("--fixture", default="", help="folder of P1_A*.jsonl to include AND use (sets ORION_USE_AGENTS=1). Default: no agent files at all.")
ap.add_argument("--lang", default="en", help="spoken language the stub Whisper reports (ar = Arabic text through the whole pipeline)")
ap.add_argument("--fail-after", type=int, default=30)
args = ap.parse_args()

W = Path("/tmp/orion_sim"); shutil.rmtree(W, ignore_errors=True); W.mkdir()
DRIVE = W / "drive"            # what "Google Drive" holds
IDS = args.backend == "ids"
if IDS:
    # three separate Drive folders addressed by ID, like the user's real layout
    IN_DIR, STG_DIR, SFT_DIR = (W / "rcroot" / "IDIN"), (W / "rcroot" / "IDSTAGES"), (W / "rcroot" / "IDSFT")
else:
    IN_DIR, STG_DIR, SFT_DIR = DRIVE / "INPUT", DRIVE / "OUTPUT", DRIVE / "OUTPUT"
IN_DIR.mkdir(parents=True)
LOG = W / "mock_calls.jsonl"
PORT = 8977
VID_NAME = {"video_001": "video_001.mp4" if IDS else "video.mp4", "video_002": "video.mp4"}

# --- build 2 input bundles: real agent files + a synthetic video -------------
vid = W / "src.mp4"
dur = 1210
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=160x120:rate=2",
                "-f", "lavfi", "-i", f"sine=frequency=300:duration={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "32k", "-pix_fmt", "yuv420p", "-loglevel", "error", str(vid)], check=True)
for v in ("video_001", "video_002"):
    if IDS and v == "video_001":
        shutil.copy(vid, IN_DIR / "video_001.mp4")      # a LOOSE video file, no subfolder
        continue
    d = IN_DIR / v
    if args.fixture: shutil.copytree(args.fixture, d)
    else: d.mkdir(parents=True)
    shutil.copy(vid, d / "video.mp4")
print(f"built 2 inputs ({'1 loose file + 1 subfolder' if IDS else 'subfolders'})")

extra_env = {"STUB_LANG": args.lang}
remote = str(DRIVE)
if args.backend in ("rclone", "ids"):
    shim = W / "bin"; shim.mkdir()
    (shim / "rclone").symlink_to(REPO / "tests" / "fake_rclone.py")
    root = W / "rcroot"; root.mkdir(exist_ok=True)
    if not IDS:
        (root / "drive").symlink_to(DRIVE); remote = "gdrive:drive"
    else:
        STG_DIR.mkdir(parents=True, exist_ok=True); SFT_DIR.mkdir(parents=True, exist_ok=True)
        remote = ""
        extra_env.update({"ORION_DRIVE_INPUT_ID": "IDIN", "ORION_DRIVE_STAGES_ID": "IDSTAGES", "ORION_DRIVE_SFT_ID": "IDSFT"})
    extra_env.update({"PATH": f"{shim}:{os.environ['PATH']}", "FAKE_RCLONE_ROOT": str(root)})


def mock(fail_after):
    LOG.write_text("", encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(REPO / "tests/mock_vllm.py"), "--port", str(PORT),
                          "--log", str(LOG), "--fail-after", str(fail_after)])
    time.sleep(1.0); return p


def calls():
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_pod(name, fail_after):
    pod = W / name; shutil.rmtree(pod, ignore_errors=True); pod.mkdir()   # EMPTY disks = fresh GPU
    srv = mock(fail_after)
    env = {**os.environ, **extra_env, "ORION_OMNI_MODE": "live",
           "ORION_USE_AGENTS": "1" if args.fixture else "0",
           "PYTHONPATH": f"{REPO / 'tests/stub_whisper'}:{os.environ.get('PYTHONPATH', '')}", "ORION_DRIVE_REMOTE": remote,
           "ORION_OMNI_ENDPOINT": f"http://127.0.0.1:{PORT}/v1/chat/completions",
           "ORION_OMNI_RETRIES": "1", "ORION_CLIP_HEIGHT": "96", "ORION_FRAME_HEIGHT": "96"}
    r = subprocess.run([sys.executable, str(REPO / "orchestrator/run_pipeline.py"),
                        "--input", str(pod / "INPUT"), "--out", str(pod / "OUTPUT")],
                       env=env, capture_output=True, text=True)
    srv.terminate(); srv.wait()
    (W / f"{name}.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    return pod, r


def state(v):
    return json.loads((STG_DIR / v / "_state.json").read_text(encoding="utf-8"))


fails = []
def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond: fails.append(msg)

print(f"\n=== phase 1: pod A, model server dies after {args.fail_after} calls ===")
podA, r = run_pod("podA", args.fail_after)
n1 = len(calls())
s1 = state("video_001")
cache1 = list((STG_DIR / "video_001/stage_c_omni/_cache_live").glob("*.json"))
check(s1["stage_c"] == "failed", f"video_001 stage_c recorded as failed on Drive (got {s1['stage_c']})")
check(s1["stage_a"] == "done" and s1["stage_b"] == "done", "stages A,B already saved to Drive")
check(len(cache1) == args.fail_after, f"Drive holds the {args.fail_after} finished model calls (found {len(cache1)})")
check(not (STG_DIR / "video_001/stage_c_omni/_clips").exists(), "big _clips scratch was NOT uploaded")
check((STG_DIR / "video_001/_data_quality.json").exists(), "data-quality report saved")
if not args.fixture:
    check((STG_DIR / "video_001/stage_a_extract/self_extract.json").exists(), "self-extracted transcript/shots saved to Drive (no A-files used)")
    check(not list(IN_DIR.rglob("P1_*")), "input bundle contains NO agent files")

chunks = json.loads((STG_DIR / "video_001/stage_b_chunks/segment_chunks.json").read_text(encoding="utf-8"))
per_video = len(chunks) + 2   # Footage calls are off by default (rows are held back until retrieval exists)
print(f"  ({len(chunks)} chunks -> {per_video} model calls per video)")

print("\n=== phase 2: pod B, EMPTY disks, new healthy server ===")
podB, r = run_pod("podB", -1)
n2 = len(calls())
check(all(state(v)[s] == "done" for v in ("video_001", "video_002") for s in state(v)), "both videos fully done on Drive")
expected = 2 * per_video - args.fail_after
check(n2 == expected, f"pod B made {n2} model calls = {expected} (total {2*per_video} minus {args.fail_after} already cached)")
kinds = {}
for c in calls():
    kinds.setdefault(c["kind"], []).append(c["parts"])
check(all(p == ["video_url", "audio_url", "text"] for p in kinds["director"]), "Director calls carried video + audio + text")
check("footage" not in kinds, "no Footage calls are made while retrieval is not wired in (halves GPU time)")
check(all(p == ["image_url"] * 12 + ["text"] for p in kinds["style"] + kinds["story"]), "Style/Story calls carried 12 frames + text")
check(not list(podB.rglob("*.tmp")), "no torn temp files")

for name in ("director", "style", "footage", "story_architect"):
    f = SFT_DIR / f"{name}_sft.jsonl"
    rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []
    print(f"  final {name}_sft.jsonl on Drive: {len(rows)} rows")
dir_rows = len((SFT_DIR / "director_sft.jsonl").read_text(encoding="utf-8").splitlines())
check(dir_rows == 2 * len(chunks), f"director_sft has {dir_rows} rows = 2 videos x {len(chunks)} chunks")
check(len((SFT_DIR / "story_architect_sft.jsonl").read_text(encoding="utf-8").splitlines()) == 2, "story_architect_sft has 2 rows (grounded in real segment ids)")
first = json.loads((SFT_DIR / "director_sft.jsonl").read_text(encoding="utf-8").splitlines()[0])
sysmsg = first["messages"][0]["content"].lower()
check("video" not in sysmsg and "you have the actual" not in sysmsg, "stored training rows use the TEXT-ONLY student system prompt (no video mention)")
check("video clip" in subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,'.');from omni_client.prompts import DIRECTOR_SYSTEM as d;print(d)"], capture_output=True, text=True, cwd=REPO).stdout.lower(), "teacher (Omni) prompt still tells it about the video")
foot = (SFT_DIR / "footage_sft.jsonl").read_text(encoding="utf-8").strip()
check(foot == "", "footage_sft is empty (no Footage rows until the retrieval layer exists)")
check(json.loads((STG_DIR / "video_001/stage_c_omni/footage_raw.json").read_text(encoding="utf-8")) == [], "footage_raw is empty on purpose (calls disabled), not missing")
check((podB / "INPUT/video_001" / VID_NAME["video_001"]).exists(), "input videos are KEPT on the pod (nothing deleted)")
check(((IN_DIR / "video_001.mp4") if IDS else (IN_DIR / "video_001/video.mp4")).exists(), "Drive INPUT untouched")

# --- layout: video / stage folder / files, and per-video FINAL results saved before any batch aggregation ---
for v in ("video_001", "video_002"):
    vd = STG_DIR / v
    folders = sorted(x.name for x in vd.iterdir() if x.is_dir())
    check(folders == ["stage_a_extract", "stage_b_chunks", "stage_c_omni", "stage_d_judge", "stage_e_sft", "stage_f_final"],
          f"{v}: one folder per stage on Drive ({', '.join(folders)})")
    check(all((vd / "stage_f_final" / f"{n}_sft.jsonl").exists() for n in ("director", "style", "footage", "story_architect")),
          f"{v}: its 4 FINAL files are saved in its own stage_f_final folder")
check(not [x for x in (STG_DIR / "video_001").iterdir() if x.is_file() and not x.name.startswith("_")], "video folder holds only stage folders + _state/_timing/_errors/_data_quality bookkeeping files")

# --- offline re-aggregation from the per-video results gives exactly the same 4 files ---
off = W / "offline_final"
r2 = subprocess.run([sys.executable, str(REPO / "orchestrator/aggregate_offline.py"), "--stages", str(STG_DIR), "--out", str(off)],
                    capture_output=True, text=True)
check(r2.returncode == 0, "offline aggregation ran (no GPU, no model)")
for n in ("director", "style", "footage", "story_architect"):
    check((off / f"{n}_sft.jsonl").read_bytes() == (SFT_DIR / f"{n}_sft.jsonl").read_bytes(), f"offline {n}_sft.jsonl is identical to the batch result")

# --- language / Unicode ---
import json as _json
se = _json.loads((STG_DIR / "video_001/stage_a_extract/self_extract.json").read_text(encoding="utf-8"))
check(se["language"]["code"] == args.lang, f"detected spoken language recorded: {se['language']}")
dq = _json.loads((STG_DIR / "video_001/_data_quality.json").read_text(encoding="utf-8"))
check(dq.get("language") == args.lang, "language is in the data-quality report")
rows = (SFT_DIR / "director_sft.jsonl").read_text(encoding="utf-8")
check(f"spoken_language: {args.lang}" in rows, "prompts carry the spoken-language line")
check("language" in first["messages"][0]["content"].lower() and "any language" in first["messages"][0]["content"], "student system prompt explains multi-language input / English output")
if args.lang == "ar":
    has_ar = any("\u0600" <= ch <= "\u06ff" for ch in rows)
    check(has_ar, "Arabic text is present in the final training rows as real characters")
    check("\\u06" not in rows, "...and NOT as \\uXXXX escapes (neither in the prompt nor in the model's answer)")
    asst = [_json.loads(l)["messages"][2]["content"] for l in rows.splitlines() if l.strip()]
    check(all("\\u06" not in a for a in asst) and any("\u0600" <= ch <= "\u06ff" for a in asst for ch in a), "assistant JSON keeps Arabic quotes unescaped")

print("\n=== phase 3: pod C, everything already done ===")
podC, r = run_pod("podC", -1)
check(len(calls()) == 0, f"pod C made {len(calls())} model calls (expected 0)")
check("2 already done" in r.stdout, "orchestrator reported 2 videos already done")

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECK(S) FAILED:\n  - " + "\n  - ".join(fails)))
sys.exit(1 if fails else 0)
