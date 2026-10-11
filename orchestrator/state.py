"""
Per-video checkpoint state. Tracks which stages are done so a crashed or
interrupted batch run resumes from the next incomplete stage instead of
starting over.
"""
import json
import time
from pathlib import Path
from typing import Optional

# stage_g (Corpus Aggregation) is NOT here — it's a batch-level step that
# runs once across all videos after the per-video loop finishes, not a
# per-video checkpoint stage. Including it here caused every video to
# "fail" looking for a stage_g function that only exists at the batch level
# (caught by the first real end-to-end orchestrator test run).
STAGES = ["stage_a", "stage_b", "stage_c", "stage_d", "stage_e", "stage_f"]


class VideoState:
    def __init__(self, video_dir: Path):
        self.video_dir = video_dir
        self.state_path = video_dir / "_state.json"
        self.timing_path = video_dir / "_timing.json"
        self.errors_path = video_dir / "_errors.log"
        self._state = self._load(self.state_path, default={s: "pending" for s in STAGES})
        self._timing = self._load(self.timing_path, default={})

    @staticmethod
    def _load(path: Path, default: dict) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return dict(default)

    def _save(self):
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")
        self.timing_path.write_text(json.dumps(self._timing, indent=2, ensure_ascii=False), encoding="utf-8")

    def status(self, stage: str) -> str:
        return self._state.get(stage, "pending")

    def is_done(self, stage: str) -> bool:
        return self.status(stage) == "done"

    def next_incomplete_stage(self) -> Optional[str]:
        for s in STAGES:
            if not self.is_done(s):
                return s
        return None

    def mark_in_progress(self, stage: str):
        self._state[stage] = "in_progress"
        self._save()

    def mark_done(self, stage: str, elapsed_sec: float):
        self._state[stage] = "done"
        self._timing[f"{stage}_sec"] = round(elapsed_sec, 2)
        self._save()

    def mark_failed(self, stage: str, error: str):
        self._state[stage] = "failed"
        self._save()
        self.video_dir.mkdir(parents=True, exist_ok=True)
        with open(self.errors_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {stage}: {error}\n")

    def total_elapsed_sec(self) -> float:
        return sum(v for k, v in self._timing.items() if k.endswith("_sec"))

    def reset_from(self, stage: str):
        """Mark `stage` and every later stage pending again (used by --redo-from)."""
        for s in STAGES[STAGES.index(stage):]:
            self._state[s] = "pending"
        self._save()

    def is_fully_done(self) -> bool:
        return all(self.is_done(s) for s in STAGES)
