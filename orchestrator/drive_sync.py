"""
Drive sync — makes every stage's output survive the GPU dying.

The pod's disk is treated as a scratch cache. Google Drive (via rclone) is
the source of truth:

    <remote>/INPUT/<video_id>/...      you upload: video + P1_A01..A30 jsonl
    <remote>/OUTPUT/<video_id>/...     pipeline pushes after EVERY stage
    <remote>/OUTPUT/*.jsonl, *.csv     final corpus + batch status

On a brand-new GPU: pull OUTPUT (so _state.json files say which stages are
already done), then pull each video's INPUT only when it's about to be
processed. Finished videos are skipped; a half-finished video resumes at its
first incomplete stage, and Stage C resumes at its first incomplete model
call (see _cache_live/ in stage_c_omni.py).

`ORION_DRIVE_REMOTE` picks the backend:
  - "gdrive:Orion"  -> rclone remote:path (needs the `rclone` binary + creds)
  - "/some/path"    -> plain folder (e.g. a RunPod network volume, or a
                       mounted drive). No rclone needed. Also what the local
                       tests use.
  - unset/empty     -> sync disabled, local-only run.

Sync failures never crash a run (that would waste GPU money): they are
retried, logged to OUTPUT/_sync_errors.log, and the failed video is retried
again at the end of the batch.

Big regenerable scratch (_clips/, _frames/, _audio/) is never uploaded.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

EXCLUDE_DIRS = ("_clips", "_frames", "_audio")


class DriveSync:
    def __init__(self, remote: str | None, input_dir: Path, output_dir: Path):
        self.remote = (remote or "").strip().rstrip("/") or None
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.local_backend = bool(self.remote) and (
            os.path.isabs(self.remote) or self.remote.startswith("local:")
        )
        if self.local_backend and self.remote.startswith("local:"):
            self.remote = self.remote[len("local:"):]
        self._last_push: dict[str, float] = {}
        self.failed_pushes: set[str] = set()

    # ------------------------------------------------------------------ basics
    @property
    def enabled(self) -> bool:
        return self.remote is not None

    def describe(self) -> str:
        if not self.enabled:
            return "disabled (local-only run)"
        kind = "folder" if self.local_backend else "rclone"
        return f"{kind}: {self.remote}"

    def _r(self, *parts: str) -> str:
        """Remote path for INPUT/OUTPUT/... pieces."""
        tail = "/".join(p.strip("/") for p in parts if p)
        return f"{self.remote}/{tail}" if tail else self.remote

    def _log_error(self, msg: str):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with open(self.output_dir / "_sync_errors.log", "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except OSError:
            pass
        print(f"[drive-sync] {msg}")

    # ------------------------------------------------------------- rclone calls
    def _rclone(self, args: list[str], retries: int = 3) -> subprocess.CompletedProcess | None:
        cmd = ["rclone", *args]
        last = None
        for attempt in range(1, retries + 1):
            try:
                last = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            except FileNotFoundError:
                self._log_error("rclone binary not found (run deploy/setup_runpod.sh, or install rclone)")
                return None
            except subprocess.TimeoutExpired:
                self._log_error(f"rclone timed out: {' '.join(args[:3])}")
                last = None
            else:
                if last.returncode == 0:
                    return last
            if attempt < retries:
                time.sleep(5 * attempt)
        err = (last.stderr.strip()[-300:] if last is not None else "timeout")
        self._log_error(f"rclone failed after {retries} tries: {' '.join(args[:3])} :: {err}")
        return None

    def _rclone_filters(self) -> list[str]:
        out = []
        for d in EXCLUDE_DIRS:
            out += ["--exclude", f"{d}/**", "--exclude", f"**/{d}/**"]
        return out

    # ------------------------------------------------------------ local backend
    @staticmethod
    def _local_copy_tree(src: Path, dst: Path, max_depth_one: bool = False, skip_newer_dst: bool = False) -> bool:
        if not src.exists():
            return True  # nothing to copy is not an error (first run)
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.is_dir():
                if max_depth_one or item.name in EXCLUDE_DIRS:
                    continue
                DriveSync._local_copy_tree(item, dst / item.name, skip_newer_dst=skip_newer_dst)
            else:
                target = dst / item.name
                if target.exists():
                    s, t = item.stat(), target.stat()
                    if s.st_size == t.st_size and int(s.st_mtime) == int(t.st_mtime):
                        continue
                    if skip_newer_dst and t.st_mtime > s.st_mtime:
                        continue
                shutil.copy2(item, target)
        return True

    # ----------------------------------------------------------------- generic
    def _copy(self, src, dst, *, depth_one=False, update=False) -> bool:
        """src/dst: Path for local side, str for remote side. One of them is remote."""
        if self.local_backend:
            try:
                return self._local_copy_tree(Path(src), Path(dst), max_depth_one=depth_one, skip_newer_dst=update)
            except OSError as e:
                self._log_error(f"copy failed {src} -> {dst}: {e}")
                return False
        args = ["copy", str(src), str(dst), "--transfers", "8", "--checkers", "8", "--stats", "0", "-q"]
        args += self._rclone_filters()
        if depth_one:
            args += ["--max-depth", "1"]
        if update:
            args += ["--update"]
        return self._rclone(args) is not None

    # --------------------------------------------------------------- public API
    def ensure_remote_dirs(self) -> bool:
        if not self.enabled:
            return True
        if self.local_backend:
            Path(self._r("INPUT")).mkdir(parents=True, exist_ok=True)
            Path(self._r("OUTPUT")).mkdir(parents=True, exist_ok=True)
            return True
        ok1 = self._rclone(["mkdir", self._r("INPUT")]) is not None
        ok2 = self._rclone(["mkdir", self._r("OUTPUT")]) is not None
        return ok1 and ok2

    def list_input_videos(self) -> list[str]:
        if self.local_backend:
            root = Path(self._r("INPUT"))
            return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
        res = self._rclone(["lsf", "--dirs-only", self._r("INPUT")])
        if res is None:
            return []
        return sorted(line.strip().rstrip("/") for line in res.stdout.splitlines() if line.strip())

    def list_input_files(self, video_id: str) -> list[str] | None:
        """File names in one video's INPUT bundle, without downloading anything."""
        if self.local_backend:
            d = Path(self._r("INPUT", video_id))
            return sorted(p.name for p in d.iterdir() if p.is_file()) if d.exists() else None
        res = self._rclone(["lsf", "--files-only", self._r("INPUT", video_id)])
        if res is None:
            return None
        return sorted(line.strip() for line in res.stdout.splitlines() if line.strip())

    def pull_output(self) -> bool:
        """Bring back previous progress (state files, stage outputs, caches)."""
        if not self.enabled:
            return True
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self._copy(self._r("OUTPUT"), self.output_dir, update=True)

    def pull_input(self, video_id: str) -> bool:
        if not self.enabled:
            return True
        dst = self.input_dir / video_id
        ok = self._copy(self._r("INPUT", video_id), dst, update=True)
        return ok and dst.exists() and any(dst.iterdir())

    def push_video(self, video_id: str) -> bool:
        if not self.enabled:
            return True
        src = self.output_dir / video_id
        if not src.exists():
            return True
        ok = self._copy(src, self._r("OUTPUT", video_id))
        if ok:
            self.failed_pushes.discard(video_id)
            self._last_push[video_id] = time.time()
        else:
            self.failed_pushes.add(video_id)
        return ok

    def push_video_throttled(self, video_id: str, min_interval_sec: float = 90.0) -> bool:
        """For use inside long stages (Stage C): push at most once per interval."""
        if time.time() - self._last_push.get(video_id, 0.0) < min_interval_sec:
            return True
        return self.push_video(video_id)

    def push_batch_files(self) -> bool:
        """Top-level files only: _batch_status.json, _batch_summary.csv, the 4 final SFT files."""
        if not self.enabled:
            return True
        return self._copy(self.output_dir, self._r("OUTPUT"), depth_one=True)

    def retry_failed_pushes(self) -> list[str]:
        """Last-chance retry at end of batch. Returns video_ids still failing."""
        for vid in sorted(self.failed_pushes):
            self.push_video(vid)
        return sorted(self.failed_pushes)

    def clean_input(self, video_id: str):
        """Free pod disk after a video is fully done (Drive keeps the original)."""
        shutil.rmtree(self.input_dir / video_id, ignore_errors=True)


# Module-level handle so deep code (Stage C) can trigger a checkpoint push
# without threading the object through every call.
_default: DriveSync | None = None


def set_default(d: DriveSync | None):
    global _default
    _default = d


def get_default() -> DriveSync | None:
    return _default
