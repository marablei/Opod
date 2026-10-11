"""--only must narrow what is PROCESSED, never what goes into the 4 main training files."""
import sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator.run_pipeline import corpus_video_ids
from pipeline import stage_g_aggregate as G

run = Path(tempfile.mkdtemp())
for name, row in (("V1_legacy", '{"v":1}'), ("new_a", '{"v":2}'), ("new_b", '{"v":3}')):
    (run / name / "stage_f_final").mkdir(parents=True)
    (run / name / "stage_f_final" / "director_sft.jsonl").write_text(row + "\n")
(run / "_previous_runs" / "stage_f_final").mkdir(parents=True)          # internal folders starting with _ are never videos
(run / "failed_video").mkdir()                                            # a video that never reached Stage F has no rows: ignored
ids = corpus_video_ids(run, ["new_a", "new_b"])                           # the run was asked for two videos only (--only)
print(ids); assert set(ids) == {"new_a", "new_b", "V1_legacy"} and "failed_video" not in ids and "_previous_runs" not in ids
G.run(run_dir=run, video_ids=ids)
rows = sorted((run / "director_sft.jsonl").read_text().split())
assert rows == ['{"v":1}', '{"v":2}', '{"v":3}'], rows
print("PASS corpus ids")
