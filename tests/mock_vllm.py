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
args = ap.parse_args()
calls = 0


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        global calls
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls += 1
        if args.fail_after >= 0 and calls > args.fail_after:
            self.send_response(500); self.end_headers(); self.wfile.write(b"gpu died"); return
        system = body["messages"][0]["content"]
        parts = body["messages"][1]["content"]
        types = [p["type"] for p in parts]
        text = parts[-1]["text"]
        if "documentary editor" in system:
            kind = "director"
            out = {"editorial_function": "context", "treatment": "b-roll over narration",
                   "footage_need": {"query": "wall street trading floor", "duration_sec": 4},
                   "audio_need": {"query": "tense low strings, no vocals", "duration_sec": 12, "type": "music"},
                   "reasoning": "narration introduces a new actor"}
        elif "footage researcher" in system:
            kind = "footage"
            out = {"decision": "reject_pool", "confirmed_asset_ids": [], "rejected_asset_ids": [],
                   "reasoning": "no candidates supplied"}
        elif "style analyst" in system:
            kind = "style"
            out = {k: "x" for k in ["pacing_target", "typography_system", "color_lut",
                                    "graphic_density", "sound_design_intensity", "map_motion_frequency"]}
        else:
            kind = "story"
            ids = re.findall(r"seg_(\S+?):", text)
            pt = lambda i: {"segment_id": i, "function": "f", "note": "n"}
            out = {"act_1_hook": pt(ids[0]), "act_1_anchor": pt(ids[1]), "act_2_escalation": [pt(ids[len(ids) // 2])],
                   "act_3_payoff": pt(ids[-2]), "act_3_closing": pt(ids[-1])}
        with open(args.log, "a") as f:
            f.write(json.dumps({"n": calls, "kind": kind, "parts": types}) + "\n")
        resp = json.dumps({"choices": [{"message": {"content": json.dumps(out)}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(resp)


HTTPServer(("127.0.0.1", args.port), H).serve_forever()
