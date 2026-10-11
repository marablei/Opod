"""
Read-only view of a run, built ONLY from files the pipeline really wrote (nothing is estimated or invented):

    <OUT>/_batch_status.json        orchestrator: which video/stage is current, pid
    <OUT>/_events.jsonl             orchestrator + Stage C: every stage start/finish/failure, every model call
    <OUT>/<video>/_state.json       per-stage state        <video>/_timing.json   seconds per finished stage
    <OUT>/<video>/_errors.log       full error text         <video>/_data_quality.json
    <OUT>/<video>/stage_c_omni/_progress.json   model calls done / total, retries, gave-up
    <OUT>/<video>/stage_*/...       outputs (counts of what is really on disk)
    <WORKSPACE>/vllm_server.log, orion_run.log   tails;  nvidia-smi;  GET /v1/models on the model server

Used by dashboard/server.py (live web page), deploy/status.py (terminal) and orchestrator/report.py (end-of-run report).
Standard library only.
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

STAGES = ["stage_a", "stage_b", "stage_c", "stage_d", "stage_e", "stage_f"]
STAGE_NAMES = {
    "stage_a": "A  Extract (shots, transcript)",
    "stage_b": "B  Chunk",
    "stage_c": "C  Model reasoning",
    "stage_d": "D  Judge",
    "stage_e": "E  SFT rows",
    "stage_f": "F  QA gate",
}
SFT_FILES = ["director_sft.jsonl", "style_sft.jsonl", "footage_sft.jsonl", "story_architect_sft.jsonl"]
_SECRET = re.compile(r"(ya29\.[\w\-.]+|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[\w]{20,}|hf_[A-Za-z0-9]{20,}|"
                     r"1//[\w\-]{20,}|\"(?:access|refresh)_token\"\s*:\s*\"[^\"]+\")")


def redact(text: str) -> str:
    """Tokens must never reach a web page, even by accident inside a log line."""
    return _SECRET.sub("[redacted]", text)


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _tail(path: Path, n: int = 40, max_bytes: int = 60000) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
        return [redact(l) for l in data.splitlines()[-n:]]
    except OSError:
        return []


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_events(out: Path, limit: int = 200) -> list[dict]:
    events = []
    for line in _tail(out / "_events.jsonl", n=limit, max_bytes=400000):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ------------------------------------------------------------------ per video
def _last_run_section(path: Path) -> list[str]:
    """Lines of a rejection log after its last '=== ... run ...' header (older runs stay on disk, not counted)."""
    lines = _tail(path, n=2000, max_bytes=400000)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("==="):
            return lines[i + 1:]
    return lines


def video_summary(vdir: Path) -> dict:
    st = _json(vdir / "_state.json", {}) or {}
    tm = _json(vdir / "_timing.json", {}) or {}
    q = _json(vdir / "_data_quality.json", {}) or {}
    prog = _json(vdir / "stage_c_omni" / "_progress.json", None)
    qa = _json(vdir / "stage_f_final" / "_qa_report.json", None)
    chunks = _json(vdir / "stage_b_chunks" / "segment_chunks.json", None)
    errors = _tail(vdir / "_errors.log", n=12)
    stages = {s: st.get(s, "pending") for s in STAGES}
    rej = _last_run_section(vdir / "stage_d_judge" / "rejected_claims.log")
    rej_counts: dict = {}
    kept_counts: dict = {}                            # "kept" = passed, with a neutral label instead of a generic guess
    for l in rej:
        m = re.match(r"\[(\w+)\]", l)
        if not m:
            continue
        bucket = kept_counts if re.search(r"\bkept:", l) else rej_counts
        bucket[m.group(1)] = bucket.get(m.group(1), 0) + 1
    director = _json(vdir / "stage_d_judge" / "director_validated.json", None)
    treat: dict = {}
    beats_total = 0
    queries = []
    for d in director or []:
        treat[d.get("treatment", "?")] = treat.get(d.get("treatment", "?"), 0) + 1
        beats_total += len(d.get("visual_beats") or [])
        q_ = (d.get("footage_need") or {}).get("query")
        if q_ and q_ != "none":
            queries.append(q_.lower())
    titles = _json(vdir.parent / "_video_names.json", {}) or {}
    out = {
        "name": vdir.name,
        "title": titles.get(vdir.name),
        "stages": stages,
        "timing_sec": {k[:-4]: v for k, v in tm.items() if k.endswith("_sec")},
        "total_sec": round(sum(v for k, v in tm.items() if k.endswith("_sec")), 1),
        "fully_done": all(v == "done" for v in stages.values()),
        "failed_stage": next((s for s, v in stages.items() if v == "failed"), None),
        "stage_c_progress": prog,
        "quality": {k: q.get(k) for k in ("duration_sec", "language", "language_probability", "transcript_segments",
                                           "transcript_coverage", "shots")},
        "warnings": q.get("warnings", []),
        "chunks": len(chunks) if chunks else None,
        "chunk_sec": ({"min": round(min(c["duration_sec"] for c in chunks), 1),
                       "max": round(max(c["duration_sec"] for c in chunks), 1)} if chunks else None),
        "qa": (qa or {}).get("shards"),
        "rejected": rej_counts,
        "kept_with_fallback": kept_counts,
        "repairs": {k: v for k, v in (_json(vdir / "stage_d_judge" / "repairs_report.json", {}) or {}).items() if k != "chunks"},
        "rejected_lines": rej[-6:],
        "treatments": treat,
        "beats_per_chunk": round(beats_total / len(director), 1) if director else None,
        "distinct_footage_queries": (len(set(queries)), len(queries)) if queries else None,
        "errors_tail": errors,
        "has_errors": bool(errors),
    }
    return out


# ------------------------------------------------------------------ machine
_gpu_cache = {"t": 0.0, "v": None}


def gpu_info() -> dict | None:
    if time.time() - _gpu_cache["t"] < 3:
        return _gpu_cache["v"]
    val = None
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            val = []
            for line in r.stdout.strip().splitlines():
                n, u, mu, mt, tmp = [x.strip() for x in line.split(",")]
                val.append({"name": n, "util_pct": int(u), "mem_used_mb": int(mu), "mem_total_mb": int(mt), "temp_c": int(tmp)})
    except (OSError, ValueError, subprocess.SubprocessError):
        val = None
    _gpu_cache.update(t=time.time(), v=val)
    return val


def model_server(port: int | None = None) -> dict:
    port = port or int(os.environ.get("ORION_OMNI_PORT", "8901"))
    t0 = time.time()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=1.5) as r:
            ok = r.status == 200
        return {"up": ok, "port": port, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001 - any failure means "not reachable"
        return {"up": False, "port": port, "error": type(e).__name__}


def setup_info(workspace: Path) -> dict:
    """Steps deploy/*.sh really reached (each writes one line to <workspace>/_setup.jsonl) + the tail of the setup log."""
    phases = []
    for line in _tail(workspace / "_setup.jsonl", n=60, max_bytes=50000):
        try:
            phases.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"phases": phases[-40:], "log": _tail(workspace / "orion_setup.log", 12)}


# ------------------------------------------------------------------ whole run
def snapshot(out: Path, workspace: Path | None = None) -> dict:
    out = Path(out)
    workspace = Path(workspace or os.environ.get("ORION_WORKSPACE", "/workspace"))
    now = time.time()
    batch = _json(out / "_batch_status.json", {}) or {}
    events = read_events(out, 250)
    vids = []
    if out.exists():
        for d in sorted(p for p in out.iterdir() if p.is_dir() and (p / "_state.json").exists()):
            vids.append(video_summary(d))

    last_t = events[-1]["t"] if events else None
    run_start = next((e for e in reversed(events) if e.get("kind") == "run_start"), None)
    run_end = next((e for e in reversed(events) if e.get("kind") == "run_end"), None)
    running = bool(run_start) and (not run_end or run_end["t"] < run_start["t"]) and _pid_alive(batch.get("pid"))
    current = batch.get("current_video")
    for v in vids:
        v["is_current"] = bool(running and v["name"] == current)
        if v["is_current"]:
            starts = [e for e in events if e.get("video") == v["name"] and e.get("kind") == "stage_start"]
            if starts:
                v["current_stage"] = starts[-1].get("stage")
                v["current_stage_running_sec"] = round(now - starts[-1]["t"], 1)

    rate = os.environ.get("ORION_POD_PRICE_PER_HR")
    elapsed = (now - run_start["t"]) if run_start and running else (
        (run_end["t"] - run_start["t"]) if run_start and run_end and run_end["t"] >= run_start["t"] else None)

    sync_log = out / "_sync_errors.log"
    corpus = {n: _count_lines(out / n) for n in SFT_FILES}
    try:
        du = shutil.disk_usage(str(out if out.exists() else workspace))
        disk = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
    except OSError:
        disk = None
    # real failures only. A rejected model answer that was then re-asked is NOT an error: it shows in the event feed.
    errors_events = [e for e in events if e.get("kind") in ("stage_failed", "video_failed", "video_failed_final", "error")][-30:]
    return {
        "now": now,
        "out_dir": str(out),
        "run": {
            "running": running,
            "pid": batch.get("pid"),
            "started_at": run_start["t"] if run_start else None,
            "finished_at": run_end["t"] if run_end and (not run_start or run_end["t"] >= run_start["t"]) else None,
            "elapsed_sec": round(elapsed, 1) if elapsed is not None else None,
            "args": (run_start or {}).get("args"),
            "mode": (run_start or {}).get("mode"),
            "last_event_age_sec": round(now - last_t, 1) if last_t else None,
            "total_videos": batch.get("total_videos"),
            "completed": batch.get("completed"),
            "current_video": current,
            "current_stage": batch.get("current_stage"),
            "avg_video_sec": batch.get("avg_video_sec"),
            "eta_sec": batch.get("eta_sec_remaining"),
            "price_per_hr": float(rate) if rate else None,
            "cost_so_far": round(elapsed / 3600 * float(rate), 2) if rate and elapsed is not None else None,
        },
        "videos": vids,
        "events": events[-120:],
        "recent_problems": errors_events,
        "corpus_rows": corpus,
        "machine": {
            "gpu": gpu_info(),
            "model_server": model_server(),
            "disk": disk,
            "sync_errors": {"count": _count_lines(sync_log), "tail": _tail(sync_log, 6)},
        },
        "setup": setup_info(workspace),
        "logs": {
            "pipeline": _tail(workspace / "orion_run.log", 40),
            "model_server": _tail(workspace / "vllm_server.log", 25),
        },
    }
