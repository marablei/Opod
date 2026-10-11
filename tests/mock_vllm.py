#!/usr/bin/env python3
"""Stand-in for the vLLM server, for tests only. Answers each Orion prompt type with
schema-valid, NON-fake JSON, records what media parts each request carried, and can
start failing after N calls to simulate the GPU/server dying."""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--log", required=True)
ap.add_argument("--fail-after", type=int, default=-1)
ap.add_argument("--delay", type=float, default=0.0, help="seconds each answer takes (to watch the dashboard live)")
ap.add_argument("--invalid-every", type=int, default=0, help="every Nth Director call answers INVALID first (tests the re-ask loop)")
args = ap.parse_args()
calls = 0
director_calls = 0


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        global calls
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls += 1
        import time as _t; _t.sleep(args.delay)
        if args.fail_after >= 0 and calls > args.fail_after:
            self.send_response(500); self.end_headers(); self.wfile.write(b"gpu died"); return
        system = body["messages"][0]["content"]
        parts = body["messages"][1]["content"]
        types = [p["type"] for p in parts]
        text = parts[-1]["text"]
        if "describe media" in system:  # deploy/smoke_test.py
            kind = "smoke"
            out = {"video": "test pattern", "audio": "tone", "frames_seen": types.count("image_url")}
        elif "documentary editor" in system:
            global director_calls
            kind = "director"
            director_calls += 1
            cid = re.search(r"chunk_id: (\S+)", text).group(1)
            dur = float(re.search(r"chunk_duration_sec: ([\d.]+)", text).group(1))
            retry = "rejected by an automatic check" in text
            if args.invalid_every and director_calls % args.invalid_every == 0 and not retry:
                out = {"chunk_id": cid, "editorial_function": "context", "treatment": "talking_head", "visual_beats": [],
                       "footage_need": {"query": "none", "duration_sec": 99}, "audio_need": {"query": "upbeat music", "duration_sec": 1, "type": "music"},
                       "reasoning": "x"}
            else:
                out = {"chunk_id": cid, "editorial_function": "introduce the topic", "treatment": "mixed",
                       "visual_beats": [{"start_sec": 0, "end_sec": round(dur * 0.6, 1), "kind": "talking_head", "what": "host speaking to camera"},
                                        {"start_sec": round(dur * 0.6, 1), "end_sec": dur, "kind": "document_or_screenshot", "what": "document close-up with highlighted text"}],
                       "footage_need": {"query": "official circular document close-up", "duration_sec": 0},
                       "audio_need": {"query": "none", "duration_sec": 0, "type": "none"},
                       "reasoning": "narration introduces a new actor (\u0627\u0644\u0631\u0627\u0648\u064a)"}
        elif "footage researcher" in system:
            kind = "footage"
            out = {"decision": "reject", "confirmed_asset_ids": [], "rejected_asset_ids": [],
                   "reasoning": "no candidates supplied"}
        elif "style analyst" in system:
            kind = "style"
            out = {k: "bold white sans-serif captions" for k in ["pacing_target", "typography_system", "color_lut",
                                    "graphic_density", "sound_design_intensity"]}
            out["map_motion_frequency"] = "not_present"
        else:
            kind = "story"
            ids = re.findall(r"seg_(\S+?):", text)
            pt = lambda i: {"segment_id": i, "function": "f", "note": "n"}
            out = {"act_1_hook": pt(ids[0]), "act_1_anchor": pt(ids[1]), "act_2_escalation": [pt(ids[len(ids) // 2])],
                   "act_3_payoff": pt(ids[-2]), "act_3_closing": pt(ids[-1])}
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"n": calls, "kind": kind, "parts": types}, ensure_ascii=False) + "\n")
        resp = json.dumps({"choices": [{"message": {"content": json.dumps(out, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(resp)


HTTPServer(("127.0.0.1", args.port), H).serve_forever()
