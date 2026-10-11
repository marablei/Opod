"""
Recovery round + per-video failure budget.

Policy (set by the project owner):
  pass 1   the video runs normally (Stage C answers, Stage D judge).
  recovery if ANYTHING failed in the judge (even one chunk, i.e. 1%): every rejected answer is asked again, with its exact
           failure reasons plus stricter rules chosen by code for that kind of failure. A raw answer is replaced only when the
           new one passes the same checks. The judge then runs again on the updated answers.
  pass 2   the percentage budget applies to what is STILL rejected:
             up to OK_PCT of the chunks          -> approved
             up to FLAG_PCT                      -> flagged (quick human look)
             more than FLAG_PCT                  -> quarantined (kept on disk, left out of the final training files)
           videos with fewer than SHORT_CHUNKS chunks: at most 1 rejected chunk is approved, more is quarantined.
           A missing Story/Style row, or more than REPAIR_FLAG_PCT of chunks repaired by code, flags the video.
Nothing is deleted. Recovery runs once per video (stage_c_omni/_recovery.json); it is re-armed by --redo-from stage_c.
"""
import json
import math
import os
import time
from pathlib import Path

from omni_client import validators

OK_PCT = float(os.environ.get("ORION_BUDGET_OK_PCT", "3"))
FLAG_PCT = float(os.environ.get("ORION_BUDGET_FLAG_PCT", "5"))
SHORT_CHUNKS = int(os.environ.get("ORION_BUDGET_SHORT_CHUNKS", "20"))
REPAIR_FLAG_PCT = float(os.environ.get("ORION_BUDGET_REPAIR_FLAG_PCT", "30"))
EXTRA_CALL_FRACTION = float(os.environ.get("ORION_RECOVERY_CALL_FRACTION", "0.25"))   # hard ceiling on extra model calls per video
VERDICT_FILE = "_video_verdict.json"
_STATE_ORDER = {"approved": 0, "flagged": 1, "quarantined": 2}


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _strip(entry):
    return {k: v for k, v in entry.items() if k not in ("_problems", "_attempts")} if isinstance(entry, dict) else entry


def _row_present(path: Path) -> bool:
    """A Story/Style row exists when its validated file exists and is not the literal 'null' the judge writes on rejection."""
    try:
        return path.read_text(encoding="utf-8").strip() not in ("", "null")
    except OSError:
        return False


def decide(total: int, rejected: int, repaired: int = 0, story_ok: bool = True, style_ok: bool = True) -> dict:
    """Pure budget rule (unit-tested). total = director chunks judged, rejected = still rejected after recovery."""
    reasons, state = [], "approved"

    def raise_to(new, why):
        nonlocal state
        reasons.append(why)
        if _STATE_ORDER[new] > _STATE_ORDER[state]:
            state = new

    pct = (100.0 * rejected / total) if total else 0.0
    if total < SHORT_CHUNKS:
        if rejected > 1:
            raise_to("quarantined", f"{rejected} of {total} chunks rejected (short video: at most 1 allowed)")
    elif pct > FLAG_PCT:
        raise_to("quarantined", f"{pct:.1f}% of chunks rejected (limit {FLAG_PCT:g}%)")
    elif pct > OK_PCT:
        raise_to("flagged", f"{pct:.1f}% of chunks rejected (approved up to {OK_PCT:g}%)")
    if not story_ok:
        raise_to("flagged", "no Story row")
    if not style_ok:
        raise_to("flagged", "no Style row")
    if total and 100.0 * repaired / total > REPAIR_FLAG_PCT:
        raise_to("flagged", f"{100.0 * repaired / total:.0f}% of chunks needed code repairs (limit {REPAIR_FLAG_PCT:g}%)")
    return {"state": state, "rejected": rejected, "total_chunks": total, "rejected_pct": round(pct, 1),
            "repaired": repaired, "story_row": story_ok, "style_row": style_ok, "reasons": reasons}


def judge_failures(video_out_dir: Path):
    """What the judge rejected, with the exact reasons, recomputed from the raw answers.
    Returns (director {chunk_id: [reasons]}, other {"style"|"story": [reasons]})."""
    s_dir, d_dir, b_dir = video_out_dir / "stage_c_omni", video_out_dir / "stage_d_judge", video_out_dir / "stage_b_chunks"
    chunks = {c["chunk_id"]: c for c in _json(b_dir / "segment_chunks.json", [])}
    director_raw = {e.get("chunk_id"): e for e in _json(s_dir / "director_raw.json", [])}
    report = _json(d_dir / "repairs_report.json", {}) or {}
    rejected_ids = [x["chunk_id"] for x in report.get("chunks", []) if x.get("status") == "rejected"]
    director = {}
    for cid in rejected_ids:
        chunk, raw = chunks.get(cid), director_raw.get(cid)
        if chunk is None or raw is None:
            continue
        probs = validators.check("director", validators.normalize("director", _strip(raw), chunk), chunk)
        director[cid] = probs or ["answer rejected by the judge"]
    other = {}
    story_input, rollup = _json(b_dir / "story_input.json", {}), _json(b_dir / "style_rollup.json", {})
    for kind, raw_name, ok_name, ctx in (("story", "story_architect_raw.json", "story_architect_validated.json", story_input),
                                         ("style", "style_raw.json", "style_validated.json", rollup)):
        if _row_present(d_dir / ok_name):
            continue
        raw = _json(s_dir / raw_name)
        if isinstance(raw, dict):
            try:
                other[kind] = validators.check(kind, validators.normalize(kind, _strip(raw), ctx), ctx) or ["answer rejected by the judge"]
            except Exception:                              # noqa: BLE001 - a malformed answer is still a failure to retry
                other[kind] = ["answer rejected by the judge"]
    return director, other


def recover_failures(video_input_dir: Path, video_out_dir: Path) -> dict:
    """Run the recovery round once. Returns a record ({"changed": bool, ...}); never raises for 'nothing to do'."""
    from omni_client import stage_c_omni
    marker = video_out_dir / "stage_c_omni" / "_recovery.json"
    if marker.exists():
        return {"changed": False, "skipped": "recovery already ran for this video"}
    director, other = judge_failures(video_out_dir)
    if not director and not other:
        return {"changed": False, "skipped": "nothing failed"}
    n_calls = len(_json(video_out_dir / "stage_c_omni" / "director_raw.json", []) or []) + 2
    max_calls = max(8, math.ceil(EXTRA_CALL_FRACTION * n_calls))
    record = stage_c_omni.recover(video_input_dir, video_out_dir, director, other, max_calls)
    record["ran_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if "skipped" not in record:                       # a real attempt was made: do not repeat it on every re-judge
        marker.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def write_verdict(video_out_dir: Path, recovery: dict | None = None) -> dict:
    d_dir = video_out_dir / "stage_d_judge"
    rep = _json(d_dir / "repairs_report.json", None)
    if rep:
        total, rejected, repaired = rep.get("director_chunks", 0), rep.get("rejected", 0), rep.get("repaired", 0)
    else:                                              # an older run without the ledger
        raw = _json(video_out_dir / "stage_c_omni" / "director_raw.json", []) or []
        kept = _json(d_dir / "director_validated.json", []) or []
        total, rejected, repaired = len(raw), max(0, len(raw) - len(kept)), 0

    v = decide(total, rejected, repaired, _row_present(d_dir / "story_architect_validated.json"),
               _row_present(d_dir / "style_validated.json"))
    rec = recovery or _json(video_out_dir / "stage_c_omni" / "_recovery.json", None)
    if rec:
        outcomes = [x.get("outcome") for x in list((rec.get("director") or {}).values()) + list((rec.get("single") or {}).values())]
        v["recovery"] = {"attempted": len(outcomes), "recovered": outcomes.count("recovered"), "extra_model_calls": rec.get("calls", 0)}
    v["decided_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (video_out_dir / VERDICT_FILE).write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")
    return v
