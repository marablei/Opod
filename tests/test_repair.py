"""Beat overlaps that the model produced on the real 20-minute test video (chunks 006, 013, 064, 079) must be repaired by code."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from omni_client import validators as V

def beat(s, e, k, w="x thing on screen"): return {"start_sec": s, "end_sec": e, "kind": k, "what": w}
def entry(cid, beats, q="chart of ownership", an="calm piano, slow tempo"):
    return {"chunk_id": cid, "editorial_function": "Explain a point", "treatment": "mixed", "visual_beats": beats,
            "footage_need": {"query": q, "duration_sec": 5}, "audio_need": {"query": an, "duration_sec": 14, "type": "music" if an != "none" else "none"},
            "reasoning": "The host speaks and a chart appears."}
def chunk(cid, dur): return {"chunk_id": cid, "duration_sec": dur, "start_sec": 0, "end_sec": dur}

def run(cid, dur, beats, **kw):
    c = chunk(cid, dur); e = V.normalize_director(entry(cid, beats, **kw), c)
    return e, V.validate_director(e, c)

# 006: host the whole time, chart cut in the middle  (was REJECTED, 3 tries)
e, p = run("CHUNK_006", 14.6, [beat(0, 14.6, "talking_head"), beat(2.8, 6.8, "chart_or_data")])
print([(b["start_sec"], b["end_sec"], b["kind"]) for b in e["visual_beats"]]); assert not p, p
assert [(b["start_sec"], b["end_sec"]) for b in e["visual_beats"]] == [(0.0, 2.8), (2.8, 6.8), (6.8, 14.6)]
# 013: host + screenshot + text card that covers the end of the host beat
e, p = run("CHUNK_013", 14.6, [beat(0, 14.6, "talking_head"), beat(7.2, 9.2, "document_or_screenshot"), beat(9.2, 14.6, "graphic_overlay")])
print([(b["start_sec"], b["end_sec"], b["kind"]) for b in e["visual_beats"]]); assert not p, p
assert [b["kind"] for b in e["visual_beats"]] == ["talking_head", "document_or_screenshot", "graphic_overlay"]
# 064: host + three 0.8-1 s logo flashes
e, p = run("CHUNK_064", 14.0, [beat(0, 14, "talking_head"), beat(8, 8.8, "broll"), beat(8.8, 9.6, "broll"), beat(9.6, 10.6, "broll")])
print([(b["start_sec"], b["end_sec"], b["kind"]) for b in e["visual_beats"]]); assert not p, p
# nothing overlaps -> untouched
b0 = [beat(0, 5, "talking_head"), beat(5, 14, "chart_or_data")]
e, p = run("CHUNK_X", 14.0, b0); assert [(b["start_sec"], b["end_sec"]) for b in e["visual_beats"]] == [(0.0, 5.0), (5.0, 14.0)] and not p, p
# host only but the model wrote a footage query about the host -> "none"
c = chunk("CHUNK_011", 12.0); en = entry("CHUNK_011", [beat(0, 12, "talking_head")], q="man speaking at a desk with a microphone", an="none"); en["treatment"] = "talking_head"
e = V.normalize_director(en, c); p = V.validate_director(e, c)
assert e["footage_need"] == {"query": "none", "duration_sec": 0}, e["footage_need"]; assert not p, p
# a real problem is still caught
c = chunk("CHUNK_Y", 12.0); en = entry("CHUNK_Y", [beat(0, 12, "talking_head")], an="upbeat background music"); en["treatment"] = "talking_head"
e = V.normalize_director(en, c); p = V.validate_director(e, c); assert any("too generic" in x for x in p), p
# model said footage "none" but showed a chart: query now comes from its own beat text
c = chunk("CHUNK_009", 14.5); en = entry("CHUNK_009", [beat(0, 14.5, "talking_head", "Host speaking"), beat(10.1, 12.1, "graphic_overlay", "Company logos appear on screen")], q="none", an="calm piano, slow tempo")
e = V.normalize_director(en, c); p = V.validate_director(e, c)
assert e["footage_need"]["query"] == "Company logos appear on screen" and not p, (e["footage_need"], p)
# generic audio left after retries: judge keeps the chunk with an honest placeholder
c = chunk("CHUNK_077", 13.6); en = entry("CHUNK_077", [beat(0, 4, "document_or_screenshot"), beat(4, 13.6, "talking_head")], an="upbeat background music")
e = V.normalize_director(en, c); p = V.validate_director(e, c); assert p and all("too generic" in x for x in p), p
f = V.salvage_audio(e, p); assert f and not V.validate_director(f, c) and f["audio_need"]["query"] == V.MUSIC_UNSPECIFIED
# but a genuinely broken answer is NOT salvaged
en2 = entry("CHUNK_077", [beat(0, 4, "document_or_screenshot"), beat(4, 99, "talking_head")], an="upbeat background music")
e2 = V.normalize_director(en2, c); p2 = V.validate_director(e2, c); assert V.salvage_audio(e2, p2) is None or any("too generic" not in x for x in p2) is False
# BETRAYAL CHUNK_041: 9 beats (was REJECTED "at most 8")
B = [beat(0,1.4,"archival_clip","Soldier holding Saudi flag"), beat(1.4,2.4,"archival_clip","Military tanks on desert road"), beat(2.4,3.2,"archival_clip","F-15 fighter jets on runway"),
     beat(3.2,4,"archival_clip","F-35 fighter jet on aircraft carrier"), beat(4,4.6,"talking_head","Host speaking to camera"), beat(4.6,7,"archival_clip","Men in combat gear raising fists"),
     beat(7,9.6,"archival_clip","Men with rifles on rocky hill"), beat(9.6,11.6,"archival_clip","Men on boat with machine gun"), beat(11.6,14.6,"talking_head","Host speaking to camera")]
c = chunk("CHUNK_041", 14.6); en = entry("CHUNK_041", B, q="Saudi military parade"); e = V.normalize_director(en, c); p = V.validate_director(e, c)
print(len(e["visual_beats"]), [(b["start_sec"], b["end_sec"], b["kind"]) for b in e["visual_beats"]]); assert len(e["visual_beats"]) <= 8 and not p, p
assert e["visual_beats"][0]["start_sec"] == 0 and e["visual_beats"][-1]["end_sec"] == 14.6
# BETRAYAL CHUNK_028: host over 7 cutaways
B = [beat(0,16.6,"talking_head","Host speaking at a desk"), beat(0.5,2.3,"graphic_overlay","Map of the Red Sea"), beat(2.3,3.5,"broll","Military trucks"), beat(3.5,4.7,"archival_clip","Houthi rebels posing"),
     beat(4.7,6.1,"broll","Damaged boats"), beat(6.1,7.5,"broll","Houthi rebels on a tank"), beat(7.5,12.3,"graphic_overlay","Map showing Houthi ships"), beat(12.3,16.6,"graphic_overlay","Map highlighting the strait")]
c = chunk("CHUNK_028", 16.6); e = V.normalize_director(entry("CHUNK_028", B), c); p = V.validate_director(e, c); print(len(e["visual_beats"])); assert not p, p
print("PASS repair")
