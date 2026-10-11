#!/usr/bin/env python3
"""
Offline tests for the quality layer (no GPU, no network): chunking on the REAL first test video's transcript,
the answer validators, the re-ask loop in Stage C, the events log, the dashboard API and the run report.

    python tests/test_quality.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
FIX = REPO / "tests" / "fixtures"
fails = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def has(problems, needle):
    return any(needle in p for p in problems)


# ------------------------------------------------------------------ 1. chunking on the real V1 data
print("\n=== chunking (real transcript + shots of the reference video V1) ===")
from pipeline import stage_b_chunking as B
v1 = json.loads((FIX / "v1_self_extract.json").read_text(encoding="utf-8"))
dur = v1["source"]["duration_sec"]
ch = B._build_chunks(v1["transcript"], v1["shots"], dur)
lens = [e - s for s, e, _, _ in ch]
check(14 <= len(ch) <= 24, f"{len(ch)} chunks for a {dur:.0f}s video (old rule: 4)")
check(max(lens) <= B.MAX_CHUNK_SEC + B.MIN_CHUNK_SEC, f"longest chunk {max(lens):.1f}s (old rule: 71s)")
check(min(lens) >= 5.5, f"shortest chunk {min(lens):.1f}s (no scraps)")
check(ch[0][0] == 0.0 and abs(ch[-1][1] - dur) < 1e-6, "chunks cover the whole video from 0.0 to the end")
check(all(abs(a[1] - b[0]) < 1e-9 for a, b in zip(ch, ch[1:])), "no gaps and no overlaps between chunks")
cuts = [s["start_sec"] for s in v1["shots"][1:]]
on_cut = sum(1 for s, _e, _t, k in ch[1:] if any(abs(s - c) < 0.05 for c in cuts))
check(on_cut >= len(ch) // 3, f"{on_cut}/{len(ch)-1} chunk starts were snapped onto a real shot cut")
check(len(B.build_chunk_ranges([], [], 100.0)) >= 6, "no speech at all -> fixed windows, not one 100 s chunk")
long_seg = [{"id": 1, "start_sec": 0.0, "end_sec": 90.0, "text": "one very long segment without any sentence end"}]
check(max(e - s for s, e, _ in B.build_chunk_ranges(long_seg, [], 90.0)) <= B.MAX_CHUNK_SEC + B.MIN_CHUNK_SEC,
      "one 90 s segment is force-split")

# ------------------------------------------------------------------ 2. validators
print("\n=== validators ===")
from omni_client import validators as V
from omni_client import prompts as P
chunk = {"chunk_id": "CHUNK_003", "start_sec": 27.6, "end_sec": 38.5, "duration_sec": 10.9,
         "transcript_text": "And it's not a small change. It's actually going to benefit millions of users.",
         "shots": [{"start_sec": 27.6}, {"start_sec": 30.0}, {"start_sec": 33.1}], "transcript_segments": [],
         "ocr_text_regions": [], "objects": [], "evidence_available": {"shots": True, "ocr": False, "objects": False}, "language": "en"}
good = {"chunk_id": "CHUNK_003", "editorial_function": "announce the scale of the change", "treatment": "mixed",
        "visual_beats": [{"start_sec": 0, "end_sec": 5.0, "kind": "talking_head", "what": "host speaking to camera"},
                         {"start_sec": 5.0, "end_sec": 10.9, "kind": "document_or_screenshot", "what": "highlighted regulation text close-up"}],
        "footage_need": {"query": "regulation document highlighted text", "duration_sec": 0},
        "audio_need": {"query": "none", "duration_sec": 0, "type": "none"}, "reasoning": "The host stresses scale, then the document appears."}
check(V.validate_director(V.normalize_director(good, chunk), chunk) == [], "a good Director answer passes")
n = V.normalize_director(good, chunk)
check(n["footage_need"]["duration_sec"] == 5.9 and n["audio_need"]["duration_sec"] == 0,
      f"footage duration is computed by code from the beats ({n['footage_need']['duration_sec']}s), not guessed by the model")
old_style = {"chunk_id": "CHUNK_003", "editorial_function": "Explaining a new regulation", "treatment": "talking_head",
             "footage_need": {"query": "man in white t-shirt explaining something", "duration_sec": 10},
             "audio_need": {"query": "upbeat background music for explainer video", "duration_sec": 10, "type": "music"}, "reasoning": "x"}
check(has(V.validate_director(old_style, chunk), "missing keys"), "the OLD generic answer format (no beats) is rejected")
bad = json.loads(json.dumps(good)); bad["treatment"] = "talking_head"
check(has(V.validate_director(bad, chunk), "less than 70%"), "validator still rejects treatment=talking_head with mostly-document beats")
check(V.normalize_director(bad, chunk)["treatment"] == "mixed" and V.validate_director(V.normalize_director(bad, chunk), chunk) == [],
      "normalize corrects that contradictory treatment to 'mixed' (derived from the beats by code)")
bad = json.loads(json.dumps(good)); bad["audio_need"] = {"query": "upbeat background music", "duration_sec": 1, "type": "music"}
check(has(V.validate_director(V.normalize_director(bad, chunk), chunk), "too generic"), "generic audio query is rejected")
bad = json.loads(json.dumps(good)); bad["visual_beats"] = bad["visual_beats"][:1]
check(has(V.validate_director(V.normalize_director(bad, chunk), chunk), "cover only"), "beats covering <60% of the chunk are rejected")
bad = json.loads(json.dumps(good)); bad["visual_beats"][1]["end_sec"] = 40
check(has(V.validate_director(bad, chunk), "times must satisfy") and V.normalize_director(bad, chunk)["visual_beats"][1]["end_sec"] == 10.9,
      "a beat that runs past the chunk is caught raw, and clamped by normalization")
bad = json.loads(json.dumps(good)); bad["reasoning"] = 'The host says "this will destroy every telecom company tomorrow".'
check(has(V.validate_director(bad, chunk), "not in this chunk"), "a quote nobody said is rejected")
bad = json.loads(json.dumps(good)); bad["reasoning"] = 'The host says "it\'s not a small change".'
check(V.validate_director(V.normalize_director(bad, chunk), chunk) == [], "a real quote from the transcript is accepted")
bad = json.loads(json.dumps(good)); bad["footage_need"]["query"] = "none"
check(has(V.validate_director(bad, chunk), "non-host visuals"), "footage 'none' while beats show documents is rejected")

dp = P.build_director_prompt(chunk)[1]
check("chunk_duration_sec: 10.9" in dp and "shot_cuts_sec: [0.0, 2.4, 5.5]" in dp, "Director prompt gives relative duration and shot cuts")

style_rollup = {"video_id": "V", "pacing": {"cuts_per_minute": 20.92}}
st = {"pacing_target": "fast", "typography_system": "bold white captions", "color_lut": "dark with warm accents",
      "graphic_density": "high", "sound_design_intensity": "moderate", "map_motion_frequency": "not_present"}
ns = V.normalize_style(st, style_rollup)
check(ns["pacing_target"] == "fast, about 21 cuts per minute", f"pacing_target computed from measured cuts/min: '{ns['pacing_target']}'")
check(V.validate_style(ns, style_rollup) == [], "'not_present' is a valid Style value")
check(has(V.validate_style({**ns, "color_lut": "1080p h264 look"}, style_rollup), "technical"), "codec/resolution facts in Style are rejected")
check(has(V.validate_style({k: v for k, v in ns.items() if k != "color_lut"}, style_rollup), "missing keys"), "missing Style key is rejected")

story_input = {"video_id": "V1", "transcript": v1["transcript"]}
real_story = json.loads((FIX / "v1_story_validated.json").read_text(encoding="utf-8"))
check(V.validate_story(V.normalize_story(real_story), story_input) == [], "the real V1 Story answer (checked by hand against the transcript) still passes")
bad = json.loads(json.dumps(real_story)); bad["act_2_escalation"][0]["note"] = 'He says "Jio will give every user free data forever".'
check(has(V.validate_story(bad, story_input), "nobody says"), "an invented quote in a Story note is rejected")
bad = json.loads(json.dumps(real_story)); bad["act_1_hook"]["segment_id"] = "seg_60"
check(has(V.validate_story(bad, story_input), "earlier act point"), "acts out of video order are rejected")
bad = json.loads(json.dumps(real_story)); bad["act_1_hook"]["segment_id"] = "seg_999"
check(has(V.validate_story(bad, story_input), "not a segment"), "unknown segment id is rejected")

# ------------------------------------------------------------------ 3. glossary
print("\n=== Whisper glossary ===")
from pipeline import self_extract as SE
g = SE.load_glossary(None)
check("TRAI" in g and len(g) == len(set(x.lower() for x in g)), f"global glossary loaded: {g}")
tmpv = Path("/tmp/orion_q_glossary"); shutil.rmtree(tmpv, ignore_errors=True); tmpv.mkdir()
(tmpv / "glossary.txt").write_text("# mine\nSebi\nTRAI\n", encoding="utf-8")
g2 = SE.load_glossary(tmpv)
check(g2[-1] == "Sebi" and g2.count("TRAI") == 1, "a per-video glossary.txt is added, duplicates removed")

# ------------------------------------------------------------------ 4. full run with a model that is invalid every 2nd time
print("\n=== Stage C re-ask loop + events + dashboard + report (mock model, synthetic video) ===")
W = Path("/tmp/orion_q"); shutil.rmtree(W, ignore_errors=True); W.mkdir()
vid = W / "src.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=130:size=160x120:rate=2", "-f", "lavfi", "-i", "sine=frequency=300:duration=130",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "32k", "-pix_fmt", "yuv420p", "-loglevel", "error", str(vid)], check=True)
INP, OUT = W / "INPUT", W / "OUTPUT"
(INP / "video_q1").mkdir(parents=True); shutil.copy(vid, INP / "video_q1" / "video.mp4")
PORT, DPORT = 8978, 8979
log = W / "mock.jsonl"; log.write_text("")
mock = subprocess.Popen([sys.executable, str(REPO / "tests/mock_vllm.py"), "--port", str(PORT), "--log", str(log), "--invalid-every", "2"])
time.sleep(1.0)
env = {**os.environ, "ORION_OMNI_MODE": "live", "ORION_OMNI_ENDPOINT": f"http://127.0.0.1:{PORT}/v1/chat/completions",
       "ORION_CLIP_HEIGHT": "96", "ORION_FRAME_HEIGHT": "96", "ORION_OMNI_RETRIES": "1", "ORION_POD_PRICE_PER_HR": "0.60",
       "PYTHONPATH": f"{REPO / 'tests/stub_whisper'}:{os.environ.get('PYTHONPATH', '')}", "ORION_DRIVE_REMOTE": "", "ORION_WORKSPACE": str(W)}
dash = None
try:
    r = subprocess.run([sys.executable, str(REPO / "orchestrator/run_pipeline.py"), "--input", str(INP), "--out", str(OUT)],
                       env=env, capture_output=True, text=True, timeout=160)
    (W / "run.log").write_text(r.stdout + r.stderr, encoding="utf-8")
    vo = OUT / "video_q1"
    st_ = json.loads((vo / "_state.json").read_text(encoding="utf-8"))
    check(all(v == "done" for v in st_.values()), f"video went through all stages ({st_})")
    ev = [json.loads(l) for l in (OUT / "_events.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = [e for e in ev if e["kind"] == "model_call"]
    check(any(not c["valid"] for c in calls) and any(c["valid"] and c["attempt"] > 1 for c in calls),
          "an invalid answer was sent back with the reasons and then fixed (events show attempt 2 valid)")
    check({"run_start", "queue", "video_start", "stage_start", "stage_done", "video_done", "run_end"} <= {e["kind"] for e in ev},
          "events log has run/video/stage lifecycle entries")
    prog = json.loads((vo / "stage_c_omni/_progress.json").read_text(encoding="utf-8"))
    check(prog["done"] == prog["total"] and prog["retried"] > 0 and prog["gave_up"] == 0, f"real Stage C counters: {prog}")
    chunks = json.loads((vo / "stage_b_chunks/segment_chunks.json").read_text(encoding="utf-8"))
    dv = json.loads((vo / "stage_d_judge/director_validated.json").read_text(encoding="utf-8"))
    check(len(dv) == len(chunks), f"all {len(chunks)} Director answers accepted by the judge after re-asks")
    check(all(d["footage_need"]["duration_sec"] == round(sum(b["end_sec"] - b["start_sec"] for b in d["visual_beats"] if b["kind"] != "talking_head"), 1) for d in dv),
          "every footage duration equals the sum of its non-host beats")
    rows = [json.loads(l) for l in (OUT / "director_sft.jsonl").read_text(encoding="utf-8").splitlines()]
    tgt = json.loads(rows[0]["messages"][2]["content"])
    check("video_id" not in tgt and "_problems" not in tgt, "training targets carry no bookkeeping keys")
    srow = json.loads((OUT / "style_sft.jsonl").read_text(encoding="utf-8").splitlines()[0])
    check("video_id" not in json.loads(srow["messages"][2]["content"]), "Style target has no video_id")
    check((OUT / "_run_report.md").exists() and "Director variety" in (OUT / "_run_report.md").read_text(encoding="utf-8"), "run report written")

    # dashboard: real server, real files
    dash = subprocess.Popen([sys.executable, str(REPO / "dashboard/server.py"), "--out", str(OUT), "--port", str(DPORT), "--host", "127.0.0.1"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.2)
    get = lambda path: urllib.request.urlopen(f"http://127.0.0.1:{DPORT}{path}", timeout=5).read().decode("utf-8")
    html = get("/")
    check("Orion" in html and "api/state" in html, "dashboard page is served")
    s = json.loads(get("/api/state"))
    check(len(s["videos"]) == 1 and s["videos"][0]["fully_done"], "dashboard state lists the real video as done")
    check(s["videos"][0]["stage_c_progress"]["total"] == prog["total"], "dashboard Stage C counters equal the file on disk")
    check(s["run"]["running"] is False and s["run"]["finished_at"] is not None and s["run"]["cost_so_far"] is not None,
          "after the run the dashboard says finished (not running) and shows a computed cost")
    check(len(s["events"]) > 5 and s["corpus_rows"]["director_sft.jsonl"] == len(rows), "events and corpus row counts come from disk")
    d = json.loads(get("/api/video?name=video_q1"))
    check(len(d["chunks"]) == len(chunks) and all(c["accepted"] for c in d["chunks"]), "video detail lists every chunk with its accepted answer")
    try:
        get("/api/video?name=../../etc"); trav = False
    except Exception:
        trav = True
    check(trav, "path traversal in the video name is refused")
    check("ya29" not in json.dumps(s), "no token-shaped text in the dashboard data")
    from orchestrator import run_view
    check(run_view.redact('x "refresh_token":"1//0abc" ya29.a0AfH6SMBxxxxxxxxxxxxxxxxxxx') .count("[redacted]") >= 2, "secrets are redacted from log text")
finally:
    mock.terminate()
    if dash:
        dash.terminate()

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECK(S) FAILED:\n  - " + "\n  - ".join(fails)))
sys.exit(1 if fails else 0)
