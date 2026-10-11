"""Odd video titles (spaces, quotes, ¿ ¦ ’, long download tags) must work end to end; safe names must stay unchanged."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator.drive_sync import safe_video_id, DriveSync

TITLES = [
 "$15 Trillion Money-Making Machine ¦ How BlackRock Became Too Powerful to Ignore (1080p_25fps_H264-128kbit_AAC-English)",
 "2020, in 7 minutes (1080p_24fps_H264-128kbit_AAC)",
 "AI Just Crossed the Terrifying Line - Now What¿ (1080p_60fps_H264-128kbit_AAC-English)",
 "Brazil’s Lula da Silva, explained (1080p_24fps_H264-128kbit_AAC-English)",
 "Can $55,000 Solve The Population Crisis¿ (1080p_30fps_H264-128kbit_AAC-English)",
 "Defunctland; The History of Worlds of Fun's Destroyed Classic, The Orient Express (1080p_24fps_H264-128kbit_AAC-English)",
 "مرحبا بالعالم (1080p_24fps_H264-128kbit_AAC)",
]
ids = [safe_video_id(t) for t in TITLES]
for t, i in zip(TITLES, ids): print(f"{i:45s} <- {t[:60]}")
assert len(set(ids)) == len(ids)
assert all(__import__("re").fullmatch(r"[A-Za-z0-9._-]+", i) and len(i) <= 60 for i in ids)
assert [safe_video_id(t) for t in TITLES] == ids                       # stable between runs
for keep in ("V1", "video_003", "my-video.2"): assert safe_video_id(keep) == keep   # existing ids never change
assert safe_video_id("same title (1080p_24fps)") != safe_video_id("same title (720p_24fps)")

# full pull through the local backend: id is safe, the real file keeps its real name inside
with tempfile.TemporaryDirectory() as td:
    td = Path(td); src = td / "drive_in"; src.mkdir()
    for t in TITLES[:3]: (src / f"{t}.mp4").write_bytes(b"x" * 10)
    (src / "4zxG_oUxBg4.dashVideo.part").write_bytes(b"x")              # not a video extension: must be ignored
    import os
    os.environ.update(ORION_DRIVE_REMOTE=f"local:{td}/drive_root")
    (td / "drive_root" / "INPUT").mkdir(parents=True)
    for f in src.iterdir(): f.rename(td / "drive_root" / "INPUT" / f.name)
    d = DriveSync(f"local:{td}/drive_root", input_dir=td / "in", output_dir=td / "out")
    vids = d.list_input_videos()
    assert len(vids) == 3, vids
    for v in vids:
        assert d.pull_input(v), v
        assert any(p.suffix == ".mp4" for p in (td / "in" / v).iterdir())
    names = json.loads((td / "out" / "_video_names.json").read_text(encoding="utf-8"))
    assert set(names) == set(vids) and TITLES[2] in names.values()
    # legacy: a title that already has a results folder keeps its old (raw) id
    legacy = Path(td / "drive_root" / "OUTPUT"); 
    raw = TITLES[0]
    (td / "drive_root" / "INPUT" / f"{raw}.mp4").write_bytes(b"x")
    sr = Path(d.stage_root); (sr / raw).mkdir(parents=True, exist_ok=True)
    vids2 = d.list_input_videos()
    assert raw in vids2, vids2
    assert safe_video_id(raw) not in vids2
print("PASS names")
