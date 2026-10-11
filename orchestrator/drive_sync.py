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

THREE-FOLDER MODE (your own Drive layout, by folder ID, no fixed names needed):
    ORION_DRIVE_INPUT_ID   -> folder with the input videos (one subfolder per video, or loose video files)
    ORION_DRIVE_STAGES_ID  -> folder where every video's stage outputs go (<video_id>/...)
    ORION_DRIVE_SFT_ID     -> folder for the final *_sft.jsonl files + batch status
  They are opened through the rclone remote named by ORION_DRIVE_REMOTE_NAME (default "gdrive")
  using rclone connection strings: "gdrive,root_folder_id=<ID>:".

`ORION_DRIVE_REMOTE` picks the single-root backend:
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
import re
import shutil
import subprocess
import time
from pathlib import Path

EXCLUDE_DIRS = ("_clips", "_frames", "_audio")


class DriveSync:
    def __init__(self, remote: str | None, input_dir: Path, output_dir: Path, roots: dict | None = None):
        """remote: single root ("gdrive:Orion" or a folder) with INPUT/ and OUTPUT/ inside.
        roots: {"input":..., "stages":..., "sft":...} to use three separate locations instead."""
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.remote = (remote or "").strip().rstrip("/") or None
        if roots:
            self.in_root = roots["input"].rstrip("/") if roots["input"].endswith("/") else roots["input"]
            self.stage_root = roots["stages"]
            self.sft_root = roots["sft"]
            self.remote = self.remote or self.in_root
        elif self.remote:
            local_form = os.path.isabs(self.remote) or self.remote.startswith("local:")
            if local_form and self.remote.startswith("local:"):
                self.remote = self.remote[len("local:"):]
            self.in_root = f"{self.remote}/INPUT"
            self.stage_root = f"{self.remote}/OUTPUT"
            self.sft_root = f"{self.remote}/OUTPUT"
        else:
            self.in_root = self.stage_root = self.sft_root = None
        self.local_backend = bool(self.in_root) and all(
            os.path.isabs(r) for r in (self.in_root, self.stage_root, self.sft_root))
        self._last_push: dict[str, float] = {}
        self.failed_pushes: set[str] = set()
        self._input_index: dict[str, tuple[str, str]] = {}  # video_id -> ("dir"|"file", remote name)

    @classmethod
    def from_env(cls, remote_arg: str, input_dir: Path, output_dir: Path) -> "DriveSync":
        """Pick the layout: explicit *_ROOT vars > folder-ID trio > single remote (--drive / ORION_DRIVE_REMOTE)."""
        e = os.environ
        if e.get("ORION_DRIVE_INPUT_ROOT") and e.get("ORION_DRIVE_STAGES_ROOT") and e.get("ORION_DRIVE_SFT_ROOT"):
            return cls(None, input_dir, output_dir, roots={
                "input": e["ORION_DRIVE_INPUT_ROOT"], "stages": e["ORION_DRIVE_STAGES_ROOT"],
                "sft": e["ORION_DRIVE_SFT_ROOT"]})
        ids = [e.get("ORION_DRIVE_INPUT_ID", "").strip(), e.get("ORION_DRIVE_STAGES_ID", "").strip(),
               e.get("ORION_DRIVE_SFT_ID", "").strip()]
        if any(ids):
            if not all(ids):
                raise SystemExit("Set all three of ORION_DRIVE_INPUT_ID, ORION_DRIVE_STAGES_ID, ORION_DRIVE_SFT_ID (or none).")
            name = e.get("ORION_DRIVE_REMOTE_NAME", "gdrive").strip().rstrip(":")
            mk = lambda i: f"{name},root_folder_id={i}:"
            return cls(None, input_dir, output_dir, roots={"input": mk(ids[0]), "stages": mk(ids[1]), "sft": mk(ids[2])})
        return cls(remote_arg, input_dir, output_dir)

    # ------------------------------------------------------------------ basics
    @property
    def enabled(self) -> bool:
        return self.in_root is not None

    def describe(self) -> str:
        if not self.enabled:
            return "disabled (local-only run)"
        kind = "folder" if self.local_backend else "rclone"
        if self.stage_root == self.sft_root and self.in_root == f"{self.remote}/INPUT":
            return f"{kind}: {self.remote}"
        return f"{kind}: input={self.in_root} stages={self.stage_root} sft={self.sft_root}"

    @staticmethod
    def _j(root: str, *parts: str) -> str:
        """Join a path under a root. Roots like "gdrive,root_folder_id=X:" take the path right after the colon."""
        tail = "/".join(p.strip("/") for p in parts if p)
        if not tail:
            return root
        if root.endswith(":"):
            return f"{root}{tail}"
        return f"{root}/{tail}"

    def _log_error(self, msg: str):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with open(self.output_dir / "_sync_errors.log", "a", encoding="utf-8") as f:
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
        """Legacy single-root layout: create INPUT/ and OUTPUT/. Folder-ID layout: the folders already
        exist, so check that each one can be listed (this is the first real test of the Drive token)."""
        if not self.enabled:
            return True
        if self.local_backend:
            for r in {self.in_root, self.stage_root, self.sft_root}:
                Path(r).mkdir(parents=True, exist_ok=True)
            return True
        legacy = self.in_root == f"{self.remote}/INPUT"
        ok = True
        for r in dict.fromkeys([self.in_root, self.stage_root, self.sft_root]):
            res = self._rclone(["mkdir", r]) if legacy else self._rclone(["lsf", "--max-depth", "1", r], retries=2)
            ok = ok and res is not None
        return ok

    def _video_ext(self, name: str) -> bool:
        return Path(name).suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}

    def _list_input_entries(self) -> list[tuple[str, str, str]]:
        """[(video_id, "dir"|"file", remote_name)] for everything in the input folder: a subfolder per
        video, or video files lying directly in the folder (id = file name without extension)."""
        names: list[tuple[str, bool]] = []  # (name, is_dir)
        if self.local_backend:
            root = Path(self.in_root)
            if root.exists():
                names = [(p.name, p.is_dir()) for p in sorted(root.iterdir())]
        else:
            res = self._rclone(["lsf", "--max-depth", "1", self.in_root])
            if res is None:
                return []
            for line in res.stdout.splitlines():
                line = line.rstrip("\r\n")  # keep spaces: Drive allows names like " video.mp4"
                if line.strip():
                    names.append((line.rstrip("/"), line.endswith("/")))
        entries, seen = [], set()
        for name, is_dir in names:
            if is_dir:
                entries.append((name, "dir", name)); seen.add(name)
        for name, is_dir in names:
            if not is_dir and self._video_ext(name):
                vid = Path(name).stem.strip()
                if vid not in seen:
                    entries.append((vid, "file", name)); seen.add(vid)
        return sorted(entries)

    def list_input_videos(self) -> list[str]:
        entries = self._list_input_entries()
        self._input_index = {vid: (kind, name) for vid, kind, name in entries}
        return [vid for vid, _, _ in entries]

    def list_input_files(self, video_id: str) -> list[str] | None:
        """File names in one video's INPUT bundle, without downloading anything."""
        if not self._input_index:
            self.list_input_videos()
        kind, name = self._input_index.get(video_id, ("dir", video_id))
        if kind == "file":
            return [name]
        if self.local_backend:
            d = Path(self._j(self.in_root, name))
            return sorted(p.name for p in d.iterdir() if p.is_file()) if d.exists() else None
        res = self._rclone(["lsf", "--files-only", self._j(self.in_root, name)])
        if res is None:
            return None
        return sorted(line.rstrip("\r\n") for line in res.stdout.splitlines() if line.strip())

    def pull_output(self) -> bool:
        """Bring back previous progress (state files, stage outputs, caches)."""
        if not self.enabled:
            return True
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self._copy(self.stage_root, self.output_dir, update=True)

    def pull_input(self, video_id: str) -> bool:
        if not self.enabled:
            return True
        if not self._input_index:
            self.list_input_videos()
        kind, name = self._input_index.get(video_id, ("dir", video_id))
        dst = self.input_dir / video_id
        if kind == "file":
            dst.mkdir(parents=True, exist_ok=True)
            if self.local_backend:
                try:
                    shutil.copy2(Path(self._j(self.in_root, name)), dst / name)
                    ok = True
                except OSError as e:
                    self._log_error(f"copy failed {name}: {e}"); ok = False
            else:
                # Copy from the folder ROOT, selecting the file by exact name via --files-from-raw (no glob
                # parsing, spaces kept). `copyto <root>:<file>` fails on rclone 1.58 with a root_folder_id.
                import tempfile  # outside the output dir, so it never gets synced to Drive
                ff = Path(tempfile.gettempdir()) / "orion_pull_one.txt"
                ff.write_text(name + "\n", encoding="utf-8")
                ok = self._rclone(["copy", self.in_root, str(dst), "--files-from-raw", str(ff),
                                   "--stats", "0", "-q"]) is not None
        else:
            ok = self._copy(self._j(self.in_root, name), dst, update=True)
        return ok and dst.exists() and any(dst.iterdir())

    def push_video(self, video_id: str) -> bool:
        if not self.enabled:
            return True
        src = self.output_dir / video_id
        if not src.exists():
            return True
        ok = self._copy(src, self._j(self.stage_root, video_id))
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
        return self._copy(self.output_dir, self.sft_root, depth_one=True)

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
