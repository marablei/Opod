"""
Live terminal progress + ETA. Reads/writes _batch_status.json so the same
data could later feed a Control Center WebSocket instead of this table.
"""
import csv
import json
import os
import sys
import time
from pathlib import Path

try:
    from rich.live import Live
    from rich.table import Table
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False


class LiveProgress:
    def __init__(self, total_videos: int, run_dir: Path, queue: list | None = None):
        self.total_videos = total_videos
        self.queue = list(queue or [])
        self.started_at = time.time()
        self.run_dir = run_dir
        self.status_path = run_dir / "_batch_status.json"
        self.completed_video_times = []  # seconds, for rolling ETA
        self.current_video = None
        self.current_stage = None
        self.video_start_time = None
        self.results = []  # [(video_id, status, total_sec)]
        self._live = None

    def __enter__(self):
        # rich's live table needs a real terminal. In a background/nohup run
        # (stdout is a file) fall back to one plain line per update instead.
        if HAVE_RICH and sys.stdout.isatty():
            self._live = Live(self._render(), refresh_per_second=2)
            self._live.__enter__()
        return self

    def __exit__(self, *exc):
        if self._live:
            self._live.__exit__(*exc)

    def start_video(self, video_id: str):
        self.current_video = video_id
        self.current_stage = None
        self.video_start_time = time.time()
        self._write_status()
        self._refresh()

    def set_stage(self, video_id: str, stage: str):
        self.current_stage = stage
        self._write_status()
        self._refresh()

    def record_stage_time(self, stage: str, elapsed: float):
        # kept for future per-stage ETA breakdown; batch ETA uses per-video totals
        pass

    def finish_video(self, video_id: str, ok: bool, skipped: bool = False):
        elapsed = time.time() - self.video_start_time
        if skipped:
            # finished on an earlier run/pod: don't let ~0s skews the ETA average
            self.results.append((video_id, "already_done", 0.0))
        elif ok:
            self.completed_video_times.append(elapsed)
            self.results.append((video_id, "done", round(elapsed, 1)))
        else:
            self.results.append((video_id, "failed", round(elapsed, 1)))
        self._write_status()
        self._refresh()

    def mark_video_failed(self, video_id: str, stage: str, error: str):
        print(f"\n[FAILED] {video_id} at {stage}: {error}\n")

    def _eta_seconds(self) -> float:
        if not self.completed_video_times:
            return 0.0
        avg = sum(self.completed_video_times) / len(self.completed_video_times)
        remaining = self.total_videos - len(self.results)
        return avg * max(0, remaining)

    def _write_status(self):
        data = {
            "total_videos": self.total_videos,
            "completed": len(self.results),
            "current_video": self.current_video,
            "current_stage": self.current_stage,
            "avg_video_sec": (
                sum(self.completed_video_times) / len(self.completed_video_times)
                if self.completed_video_times else None
            ),
            "eta_sec_remaining": self._eta_seconds(),
            "queue": self.queue,
            "results": [{"video": v, "status": st, "sec": sec} for v, st, sec in self.results],
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": time.time(),
        }
        self.status_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _render(self):
        if not HAVE_RICH:
            return None
        table = Table(title="Orion Pipeline — Batch Progress")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Video", f"{len(self.results) + 1}/{self.total_videos}: {self.current_video or '-'}")
        table.add_row("Stage", self.current_stage or "-")
        if self.completed_video_times:
            avg = sum(self.completed_video_times) / len(self.completed_video_times)
            eta = self._eta_seconds()
            table.add_row("Avg time/video", f"{avg/60:.1f} min")
            table.add_row("ETA remaining", f"{eta/60:.1f} min ({eta/3600:.2f} hr)")
        else:
            table.add_row("Avg time/video", "collecting data...")
        return table

    def _refresh(self):
        if self._live:
            self._live.update(self._render())
        else:
            # fallback: plain print when rich isn't installed
            print(f"[{len(self.results)+1}/{self.total_videos}] {self.current_video} :: {self.current_stage}", flush=True)

    def write_summary_csv(self):
        path = self.run_dir / "_batch_summary.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["video_id", "status", "total_seconds"])
            for row in self.results:
                w.writerow(row)
