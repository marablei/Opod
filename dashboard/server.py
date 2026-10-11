#!/usr/bin/env python3
"""
Orion live dashboard. Standard library only. Read-only: it never starts, stops or changes the pipeline.

    python dashboard/server.py                       # serves http://0.0.0.0:8000 , reads /workspace/OUTPUT
    python dashboard/server.py --out /path/OUTPUT --port 8000

Every number on the page comes from files the pipeline really wrote (see orchestrator/run_view.py).
If nothing has run yet the page says so; it never shows placeholder data.

Optional access key:  ORION_DASH_KEY=secret  ->  open  http://host:8000/?key=secret
(Tokens/secrets are redacted from everything it shows, but the page still lists your video names; set a key if the URL is public.)
"""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from orchestrator import run_view  # noqa: E402

OUT = Path(os.environ.get("ORION_OUTPUT_DIR", Path(os.environ.get("ORION_WORKSPACE", "/workspace")) / "OUTPUT"))
KEY = os.environ.get("ORION_DASH_KEY", "")


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def video_detail(name: str) -> dict | None:
    vdir = OUT / name
    # only names that really are video folders inside OUT (blocks ../ tricks)
    if not (vdir.is_dir() and vdir.parent.resolve() == OUT.resolve() and (vdir / "_state.json").exists()):
        return None
    chunks = _read(vdir / "stage_b_chunks" / "segment_chunks.json", []) or []
    raw = {d.get("chunk_id"): d for d in (_read(vdir / "stage_c_omni" / "director_raw.json", []) or [])}
    ok = {d.get("chunk_id"): d for d in (_read(vdir / "stage_d_judge" / "director_validated.json", []) or [])}
    rows = []
    for c in chunks:
        cid = c["chunk_id"]
        d = ok.get(cid) or raw.get(cid) or {}
        rows.append({
            "id": cid, "start": c["start_sec"], "end": c["end_sec"], "dur": c["duration_sec"],
            "boundary": c.get("boundary"), "shots": len(c.get("shots", [])),
            "text": c.get("transcript_text", "")[:300],
            "accepted": cid in ok,
            "answered": bool(d),
            "problems": (raw.get(cid) or {}).get("_problems", []) if cid not in ok else [],
            "treatment": d.get("treatment"), "function": d.get("editorial_function"),
            "beats": d.get("visual_beats"), "footage": d.get("footage_need"), "audio": d.get("audio_need"),
            "reasoning": d.get("reasoning"),
        })
    rej = run_view._last_run_section(vdir / "stage_d_judge" / "rejected_claims.log")
    return {
        "name": name,
        "chunks": rows,
        "style": _read(vdir / "stage_d_judge" / "style_validated.json"),
        "story": _read(vdir / "stage_d_judge" / "story_architect_validated.json"),
        "rejected_lines": rej[-80:],
        "errors": run_view._tail(vdir / "_errors.log", 60),
        "transcript_language": (_read(vdir / "stage_a_extract" / "self_extract.json", {}) or {}).get("language"),
    }


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if KEY and (q.get("key", [""])[0] != KEY and self.headers.get("X-Orion-Key", "") != KEY):
            return self._send(403, "forbidden: add ?key=... to the address", "text/plain")
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, (HERE / "index.html").read_text(encoding="utf-8"), "text/html")
            if u.path == "/api/state":
                return self._send(200, json.dumps(run_view.snapshot(OUT), ensure_ascii=False))
            if u.path == "/api/video":
                d = video_detail(q.get("name", [""])[0])
                return self._send(200 if d else 404, json.dumps(d or {"error": "unknown video"}, ensure_ascii=False))
            if u.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if u.path == "/healthz":
                return self._send(200, "ok", "text/plain")
        except Exception as e:  # noqa: BLE001 - a bad file must not kill the server
            return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))
        self._send(404, "not found", "text/plain")


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--port", type=int, default=int(os.environ.get("ORION_DASH_PORT", "8000")))
    ap.add_argument("--host", default=os.environ.get("ORION_DASH_HOST", "0.0.0.0"))
    a = ap.parse_args()
    OUT = Path(a.out)
    srv = ThreadingHTTPServer((a.host, a.port), H)
    pod = os.environ.get("RUNPOD_POD_ID")
    suffix = f"/?key={KEY}" if KEY else "/"
    print(f"[dashboard] reading {OUT}")
    print(f"[dashboard] on the pod:        http://127.0.0.1:{a.port}{suffix}")
    if pod:
        print(f"[dashboard] RunPod web address: https://{pod}-{a.port}.proxy.runpod.net{suffix}")
        print(f"            (works only if HTTP port {a.port} is listed under 'Expose HTTP ports' of the pod/template)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
