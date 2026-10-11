"""The repair ledger must tell a model-exact answer from a code-repaired one, and must never hide a rejection."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_repair import beat, entry, chunk
from pipeline import stage_d_judge as D

chunks = {c["chunk_id"]: c for c in [chunk("CHUNK_001", 14.0), chunk("CHUNK_002", 14.6), chunk("CHUNK_003", 14.0), chunk("CHUNK_004", 14.0)]}
raw = [
    entry("CHUNK_001", [beat(0, 6, "talking_head"), beat(6, 14, "chart_or_data")]),                                  # clean: model exact
    entry("CHUNK_002", [beat(0, 14.6, "talking_head"), beat(2.8, 6.8, "chart_or_data")]),                             # overlap repaired
    entry("CHUNK_003", [beat(0, 6, "talking_head"), beat(6, 14, "chart_or_data")], an="upbeat background music"),     # generic audio -> neutral label
    entry("CHUNK_004", [beat(0, 14, "talking_head")]),                                                                # footage 'chart of ownership' with host only
]
raw[3]["reasoning"] = "He said \"BERT & GPT\" zzz qqq."                                                              # invented quote -> rejected
ledger = []
log = Path(tempfile.mkdtemp()) / "rej.log"
out = D._validate_director(raw, chunks, log, ledger)
st = {x["chunk_id"]: x for x in ledger}
print(ledger)
assert st["CHUNK_001"]["status"] == "raw" and st["CHUNK_001"]["repairs"] == [], st["CHUNK_001"]
assert st["CHUNK_002"]["status"] == "repaired" and any(r.startswith("beats_changed") for r in st["CHUNK_002"]["repairs"]), st["CHUNK_002"]
assert st["CHUNK_003"]["status"] == "repaired" and "audio_neutral_label" in st["CHUNK_003"]["repairs"], st["CHUNK_003"]
assert st["CHUNK_004"]["status"] == "rejected" and len(out) == 3, st["CHUNK_004"]
assert len(ledger) == 4 and sum(x["status"] == "rejected" for x in ledger) == 1
# quoting on-screen text that the model was shown is grounded; quoting something nowhere in the evidence is still rejected
c = dict(chunk("CHUNK_009", 14.0), transcript_text="so the average person scrolls fast", ocr_text_regions=[{"text": "Average Retention Span =< 47 Seconds"}])
e = entry("CHUNK_009", [beat(0, 6, "talking_head"), beat(6, 14, "chart_or_data")])
e["reasoning"] = 'The slide reads "Average Retention Span =< 47 Seconds" while he talks.'
from omni_client import validators as V
assert V.validate_director(V.normalize_director(e, c), c) == [], V.validate_director(V.normalize_director(e, c), c)
e["reasoning"] = 'He says "internal context does not read it" here.'
assert any("neither" in x for x in V.validate_director(V.normalize_director(e, c), c))
print("PASS ledger")
