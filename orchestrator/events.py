"""
Append-only event log: <OUTPUT>/_events.jsonl, one JSON object per line.

This is the single source of truth for "what is the pipeline doing right now". The orchestrator and the
stages write to it as things REALLY happen (video started, stage finished, model call answered, answer
rejected, error). The live dashboard (dashboard/server.py) and deploy/status.py only ever read it, together with
the per-video state files. Nothing in the dashboard is estimated or made up.

emit() is a no-op until configure() has been called, so the stages stay usable on their own and in tests.
It never raises: a logging problem must never kill a run.
"""
import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_path: Path | None = None
_video: str | None = None


def configure(run_dir: Path) -> Path:
    global _path
    _path = Path(run_dir) / "_events.jsonl"
    _path.parent.mkdir(parents=True, exist_ok=True)
    return _path


def set_video(name: str | None):
    """Events emitted from now on carry this video name (set by the orchestrator per video)."""
    global _video
    _video = name


def emit(event: str, **fields):
    if _path is None:
        return
    rec = {"t": round(time.time(), 2), "kind": event, "video": _video, **fields}
    try:
        with _lock, open(_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
