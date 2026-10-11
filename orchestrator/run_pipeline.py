"""
Orion Pipeline — 1-click orchestrator.

    python orchestrator/run_pipeline.py                  # local INPUT/ -> OUTPUT/
    python orchestrator/run_pipeline.py --limit 1        # the 1-video GPU test
    python orchestrator/run_pipeline.py --only video_003,video_004

With Drive sync on (ORION_DRIVE_REMOTE or --drive), it behaves like this:
  1. pull the stage outputs from Drive -> learn what's already done (new GPU resume)
  2. list the input folder on Drive    -> the videos to process (subfolder per video, or loose files)
  3. per video, only if unfinished:   pull its INPUT bundle, run stages A..F
     pushing that video's folder to Drive after EVERY stage change
  4. Stage G aggregates the 4 final SFT files, pushed at the end
Lose the GPU at any point: start another, run the same command, and it picks
up from the last stage (and, inside Stage C, the last model call) that
reached Drive.

Each video is a video file (in its own subfolder, or loose in the input folder). No other files are needed.
"""
import argparse
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.state import VideoState, STAGES
from orchestrator.progress import LiveProgress
from orchestrator import drive_sync, preflight, events, recovery
from pipeline import (
    stage_a_unified_dna, stage_b_chunking, stage_d_judge,
    stage_e_dataset_extractor, stage_f_sft_qa, stage_g_aggregate,
)
from omni_client import stage_c_omni

STAGE_FUNCS = {
    "stage_a": stage_a_unified_dna.run,
    "stage_b": stage_b_chunking.run,
    "stage_c": stage_c_omni.run,
    "stage_d": stage_d_judge.run,
    "stage_e": stage_e_dataset_extractor.run,
    "stage_f": stage_f_sft_qa.run,
}
# stage_g runs once at the end, across all videos, not per-video.

MAX_CONSECUTIVE_FAILURES = int(os.environ.get("ORION_MAX_CONSECUTIVE_FAILURES", "3"))


def discover_local_videos(videos_root: Path) -> list[str]:
    return sorted(p.name for p in videos_root.iterdir() if p.is_dir()) if videos_root.exists() else []


def _fail_note(state: VideoState, msg: str):
    state.video_dir.mkdir(parents=True, exist_ok=True)
    with open(state.errors_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def _recover_and_decide(video_name: str, local_in: Path, video_out_dir: Path):
    """After the judge: any rejected answer (even 1%) is asked again once with stricter rules; the judge then re-runs;
    finally the failure budget decides approved / flagged / quarantined. Must never fail the video itself."""
    try:
        info = recovery.recover_failures(local_in, video_out_dir)
        if info.get("changed"):
            events.emit("recovery_done", director=len(info.get("director", {})), single=list(info.get("single", {})),
                        extra_calls=info.get("calls", 0))
            STAGE_FUNCS["stage_d"](video_input_dir=local_in, video_out_dir=video_out_dir)   # re-judge the updated answers
        verdict = recovery.write_verdict(video_out_dir, info if "skipped" not in info else None)
        events.emit("video_verdict", state=verdict["state"], rejected=verdict["rejected"], total=verdict["total_chunks"],
                    reasons=verdict["reasons"])
        print(f"[orion] {video_name}: {verdict['state'].upper()} - {verdict['rejected']} of {verdict['total_chunks']} chunks rejected"
              + (f" ({'; '.join(verdict['reasons'])})" if verdict["reasons"] else ""))
    except Exception as e:                                  # noqa: BLE001 - recovery is best-effort, the judge result stands
        _fail_note(VideoState(video_out_dir), f"recovery/verdict step failed: {e}")
        events.emit("recovery_failed", error=str(e)[:400])
        try:
            recovery.write_verdict(video_out_dir)
        except Exception:                                   # noqa: BLE001
            pass


def process_video(video_name: str, input_root: Path, run_dir: Path, progress: LiveProgress,
                  drive: drive_sync.DriveSync):
    """Returns (ok, skipped)."""
    video_out_dir = run_dir / video_name
    state = VideoState(video_out_dir)
    events.set_video(video_name)

    if state.is_fully_done():
        events.emit("video_skipped", reason="already done on an earlier run")
        return True, True  # finished on a previous run/pod
    events.emit("video_start")

    if drive.enabled and not drive.pull_input(video_name):
        _fail_note(state, "could not pull INPUT bundle from Drive")
        events.emit("video_failed", stage="pull_input", error="could not pull INPUT bundle from Drive")
        progress.mark_video_failed(video_name, "pull_input", "could not pull INPUT bundle from Drive")
        drive.push_video(video_name)
        return False, False

    local_in = input_root / video_name
    problems, warnings = preflight.check_input_dir(local_in)
    for w in warnings:
        print(f"[preflight] {video_name}: {w}")
    if problems:
        for p in problems:
            _fail_note(state, f"preflight: {p}")
        events.emit("video_failed", stage="preflight", error="; ".join(problems))
        progress.mark_video_failed(video_name, "preflight", "; ".join(problems))
        drive.push_video(video_name)
        return False, False

    # Let Stage C push its per-call cache to Drive while it runs.
    stage_c_omni.CHECKPOINT_HOOK = (
        (lambda: drive.push_video_throttled(video_name, 90.0)) if drive.enabled else None
    )

    ok = True
    while True:
        stage = state.next_incomplete_stage()
        if stage is None:
            break
        progress.set_stage(video_name, stage)
        state.mark_in_progress(stage)
        events.emit("stage_start", stage=stage)
        drive.push_video(video_name)
        t0 = time.time()
        try:
            STAGE_FUNCS[stage](video_input_dir=local_in, video_out_dir=video_out_dir)
            elapsed = time.time() - t0
            state.mark_done(stage, elapsed)
            events.emit("stage_done", stage=stage, sec=round(elapsed, 1))
            progress.record_stage_time(stage, elapsed)
            if stage == "stage_d":
                _recover_and_decide(video_name, local_in, video_out_dir)
            if stage == "stage_a":
                report = preflight.quality_report(video_out_dir)
                for w in report["warnings"]:
                    print(f"[data-quality] {video_name}: {w}")
            drive.push_video(video_name)
        except Exception as e:
            state.mark_failed(stage, f"{e}\n{traceback.format_exc()}")
            events.emit("stage_failed", stage=stage, error=str(e)[:600])
            progress.mark_video_failed(video_name, stage, str(e))
            drive.push_video(video_name)
            ok = False
            break

    stage_c_omni.CHECKPOINT_HOOK = None
    events.emit("video_done" if ok else "video_failed_final", ok=ok)
    if ok and drive.enabled and os.environ.get("ORION_CLEAN_INPUT", "0") == "1":
        # OFF by default: nothing is ever deleted. Opt in only if the pod disk is too small.
        drive.clean_input(video_name)
    return ok, False


def _archive_and_reset(video_out_dir: Path, from_stage: str):
    """Copy the outputs of from_stage and later into _previous_runs/<time>/, then mark them pending.
    Big re-creatable folders (_clips, _frames, _audio) are not copied."""
    import shutil
    letters = "abcdef"
    first = letters.index(from_stage[-1])
    dest = video_out_dir / "_previous_runs" / time.strftime("%Y%m%d_%H%M%S")
    for d in sorted(video_out_dir.iterdir()):
        if d.is_dir() and d.name.startswith("stage_") and len(d.name) > 7 and d.name[6] in letters \
                and letters.index(d.name[6]) >= first:
            shutil.copytree(d, dest / d.name, ignore=shutil.ignore_patterns("_clips", "_frames", "_audio"))
    VideoState(video_out_dir).reset_from(from_stage)
    print(f"[orion] {video_out_dir.name}: old outputs from {from_stage} saved to {dest}; will re-run from {from_stage}")


def main():
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(repo_root / "INPUT"),
                    help="Local folder with one subfolder per video (video + P1_A01-A30.jsonl). "
                         "With Drive sync on, bundles are pulled here on demand.")
    ap.add_argument("--out", default=str(repo_root / "OUTPUT"),
                    help="Local folder for all outputs (mirrored to Drive when sync is on).")
    ap.add_argument("--drive", default=os.environ.get("ORION_DRIVE_REMOTE", ""),
                    help="rclone remote:path (e.g. gdrive:Orion) or a plain folder. "
                         "Layout inside: INPUT/<video>/ and OUTPUT/. Default: $ORION_DRIVE_REMOTE.")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N unfinished videos (use 1 for the first GPU test).")
    ap.add_argument("--only", default="", help="Comma-separated video folder names to process.")
    ap.add_argument("--redo-from", default="", choices=["", *STAGES],
                    help="Re-run this stage and the later ones for the selected videos (e.g. after a prompt change). "
                         "The old outputs are COPIED to <video>/_previous_runs/<time>/ first; nothing is deleted.")
    args = ap.parse_args()

    input_root, run_dir = Path(args.input), Path(args.out)
    input_root.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    events.configure(run_dir)
    events.emit("run_start", args=sys.argv[1:], mode=stage_c_omni.MODE, pid=os.getpid())
    drive = drive_sync.DriveSync.from_env(args.drive, input_root, run_dir)
    drive_sync.set_default(drive)
    print(f"[orion] mode={stage_c_omni.MODE}  drive={drive.describe()}")
    if stage_c_omni.MODE == "live":
        print(f"[orion] model endpoint={stage_c_omni.ENDPOINT}  model={stage_c_omni.MODEL_NAME}")

    if drive.enabled:
        if not drive.ensure_remote_dirs():
            print("[orion] ERROR: cannot reach Drive remote — fix credentials before spending GPU time.")
            sys.exit(2)
        print("[orion] pulling previous progress from Drive ...")
        drive.pull_output()
        videos = drive.list_input_videos()
        if not videos:
            print(f"[orion] No videos found in the input folder ({drive.in_root}). Put each video in its own subfolder, or loose in the folder.")
            sys.exit(2)
    else:
        videos = discover_local_videos(input_root)
        if not videos:
            print(f"No video subfolders found under {input_root}")
            return

    if args.only:
        wanted = {v.strip() for v in args.only.split(",") if v.strip()}
        videos = [v for v in videos if v in wanted]

    if args.redo_from:
        for v in videos:
            if (run_dir / v).exists():
                _archive_and_reset(run_dir / v, args.redo_from)

    # --limit counts UNFINISHED videos, so repeated runs make steady progress.
    todo = [v for v in videos if not VideoState(run_dir / v).is_fully_done()]
    already_done = len(videos) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"[orion] {len(videos)} videos found: {already_done} already done, {len(todo)} to process this run")

    progress = LiveProgress(total_videos=len(todo), run_dir=run_dir, queue=todo)
    events.emit("queue", videos=todo, already_done=already_done)
    consecutive_failures = 0
    with progress:
        for name in todo:
            progress.start_video(name)
            ok, skipped = process_video(name, input_root, run_dir, progress, drive)
            progress.finish_video(name, ok, skipped=skipped)
            drive.push_batch_files()  # live _batch_status.json for anyone watching Drive
            consecutive_failures = 0 if ok else consecutive_failures + 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n[orion] {consecutive_failures} videos failed in a row — stopping so the GPU "
                      f"isn't wasted. Check the model server log and each video's _errors.log, then "
                      f"re-run the same command; finished work is kept.")
                break

    events.set_video(None)
    # Stage G covers every video that has outputs (this run's AND earlier runs').
    stage_g_aggregate.run(run_dir=run_dir, video_ids=videos)
    progress.write_summary_csv()
    drive.push_batch_files()
    still_failing = drive.retry_failed_pushes()
    if still_failing:
        print(f"[orion] WARNING: these videos could not be synced to Drive: {still_failing}. "
              f"Copy {run_dir} out of the pod manually before terminating it.")
    try:
        from orchestrator import report
        rp = report.write(run_dir, videos)
        print(f"[orion] run report: {rp}")
    except Exception as e:  # the report must never fail the run
        print(f"[orion] could not write run report: {e}")
    events.emit("run_end", processed=len(progress.results))
    drive.push_batch_files()
    print(f"\nBatch complete. Summary: {run_dir / '_batch_summary.csv'}")


if __name__ == "__main__":
    main()
