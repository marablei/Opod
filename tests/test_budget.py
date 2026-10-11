"""Recovery round + failure budget: any failure triggers one stricter retry; the percentage budget then decides the verdict."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_repair import beat, entry, chunk
from omni_client import validators as V, stage_c_omni as C
from orchestrator import recovery as R
from pipeline import stage_d_judge as D, stage_g_aggregate as G

# 1) the budget rule itself
dd = R.decide
assert dd(90, 0)["state"] == "approved" and dd(100, 3)["state"] == "approved"
assert dd(100, 4)["state"] == "flagged" and dd(100, 5)["state"] == "flagged" and dd(100, 6)["state"] == "quarantined"
assert dd(4, 1)["state"] == "approved" and dd(4, 2)["state"] == "quarantined" and dd(19, 2)["state"] == "quarantined"
assert dd(90, 0, story_ok=False)["state"] == "flagged" and dd(90, 0, style_ok=False)["state"] == "flagged"
assert dd(90, 0, repaired=40)["state"] == "flagged" and dd(90, 0, repaired=20)["state"] == "approved"
assert dd(90, 0, story_ok=False)["reasons"] == ["no Story row"]

# 2) stricter rules are chosen from the exact failure reasons
h = V.escalation_hints(["reasoning quotes words that are neither in this chunk's transcript nor in its on-screen text: ['x y z']", "visual_beats has 9 items; at most 8"])
assert "quotation marks" in h and "at most 5 visual_beats" in h
assert "late in the video" in V.escalation_hints(["act_3_payoff: segment 13 starts at 85s of a 863s video; pick a segment between 388s and 863s for this act point"])
assert V.escalation_hints([]) == ""

# 3) whole flow on a temp video: pass 1 judge -> recovery -> judge again -> verdict
vo = Path(tempfile.mkdtemp()) / "vid"; (vo / "stage_b_chunks").mkdir(parents=True); (vo / "stage_c_omni").mkdir(parents=True)
chunks = [dict(chunk(f"CHUNK_{i:03d}", 14.0), transcript_text="so the average person scrolls fast", ocr_text_regions=[], objects=[], shots=[], transcript_segments=[]) for i in range(1, 5)]
(vo / "stage_b_chunks/segment_chunks.json").write_text(json.dumps(chunks))
(vo / "stage_b_chunks/style_rollup.json").write_text(json.dumps({"duration_sec": 56}))
(vo / "stage_b_chunks/story_input.json").write_text(json.dumps({"video_id": "vid", "duration_sec": 56, "transcript": []}))
good = lambda cid: entry(cid, [beat(0, 6, "talking_head"), beat(6, 14, "chart_or_data")])
bad = good("CHUNK_003"); bad["reasoning"] = 'He says "internal context does not read it" here.'
raw = [good("CHUNK_001"), good("CHUNK_002"), bad, good("CHUNK_004")]
(vo / "stage_c_omni/director_raw.json").write_text(json.dumps(raw)); (vo / "stage_c_omni/footage_raw.json").write_text("[]")
(vo / "stage_c_omni/style_raw.json").write_text(json.dumps({"video_id": "vid"})); (vo / "stage_c_omni/story_architect_raw.json").write_text(json.dumps({"video_id": "vid"}))
D.run(video_input_dir=vo, video_out_dir=vo)
fails, other = R.judge_failures(vo)
print(fails, list(other)); assert list(fails) == ["CHUNK_003"] and "neither" in fails["CHUNK_003"][0] and set(other) == {"style", "story"}

calls = []
def fake_ask(kind, sys_p, user_p, ctx, **kw):
    calls.append((kind, kw.get("hint", ""), kw.get("initial_problems")))
    if kind == "director":
        e = good(ctx["chunk_id"]); e["reasoning"] = "The host talks, then a chart appears."; e["_attempts"] = 2; return e, []
    return {"video_id": "vid", "_attempts": 2}, ["still bad"]
C.MODE = "live"; C.find_source_video = lambda p: Path("x.mp4"); C.extract_clip = lambda *a, **k: None
C.extract_audio = lambda *a, **k: False; C.sample_frames = lambda *a, **k: []; C._ask_validated = fake_ask
rec = R.recover_failures(vo, vo)
assert rec["changed"] and rec["director"]["CHUNK_003"]["outcome"] == "recovered" and rec["single"]["story"]["outcome"] == "still failing", rec
dcall = [c for c in calls if c[0] == "director"][0]
assert "quotation marks" in dcall[1] and dcall[2] and "neither" in dcall[2][0], dcall          # exact reasons + stricter rule were sent
D.run(video_input_dir=vo, video_out_dir=vo)
v = R.write_verdict(vo, rec)
print(v); assert v["rejected"] == 0 and v["state"] == "flagged" and "no Story row" in v["reasons"] and v["recovery"]["recovered"] == 1
assert R.recover_failures(vo, vo).get("skipped"), "recovery must run only once per video"
# the raw file keeps its provenance: the recovered answer replaced only the failing chunk
newraw = json.loads((vo / "stage_c_omni/director_raw.json").read_text()); assert newraw[2]["reasoning"].startswith("The host talks") and newraw[0] == raw[0]

# 4) a failure that cannot be recovered stays rejected and the budget applies; quarantine leaves the video out of the corpus
vq = Path(tempfile.mkdtemp()) / "q"; vq.mkdir()
(vq / "_video_verdict.json").write_text(json.dumps({"state": "quarantined", "reasons": ["x"]}))
(vq / "stage_f_final").mkdir(); (vq / "stage_f_final/director_sft.jsonl").write_text('{"a":1}\n')
vk = vq.parent / "ok"; (vk / "stage_f_final").mkdir(parents=True); (vk / "stage_f_final/director_sft.jsonl").write_text('{"b":2}\n')
G.run(run_dir=vq.parent, video_ids=["q", "ok"])
assert (vq.parent / "director_sft.jsonl").read_text() == '{"b":2}\n', "quarantined video must be left out"
assert (vq / "stage_f_final/director_sft.jsonl").exists(), "and must stay on disk"
print("PASS budget")
