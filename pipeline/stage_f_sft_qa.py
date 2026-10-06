"""
Stage F — SFT QA gate.

NOTE: checked your Pipeline 1 files directly — there is no existing,
working SFT QA stage to reuse. A01-A30 are built and tested; everything
downstream of them (including SFT QA) was never actually run. So this is
real new code, not a wrapper around something that already existed.

Deterministic checks only (Tier 3 LLM judge stays stubbed, same as
Pipeline 1's design — wired in later only for cases these can't resolve):
  - assistant message must be valid JSON (Stage E already guarantees this
    in practice, but don't trust a prior stage silently — verify here too)
  - assistant message must not be empty / must not contain the literal
    "FAKE_placeholder" marker (that marker only appears in fake-mode test
    data; a real pipeline run should never see it — this check also
    means fake-mode data can never accidentally leak into a real corpus)
  - exact-duplicate rows within the same shard are dropped
  - user content must be non-trivially short (catches a chunk/video that
    somehow produced almost no evidence context)
"""
import json
import os
import time
from pathlib import Path

MIN_USER_CONTENT_CHARS = 20
FAKE_MARKER = "FAKE_placeholder"

# A Footage row whose prompt shows an EMPTY candidate pool can only ever be answered
# "reject the pool" — there is nothing to confirm. Training the Footage adapter on
# those rows teaches it to reject everything. Until the Python retrieval step supplies
# real candidates, these rows are held back (logged, never deleted: the .pre_qa file
# keeps them). Set ORION_ALLOW_EMPTY_CANDIDATES=1 to keep them anyway.
ALLOW_EMPTY_CANDIDATES = os.environ.get("ORION_ALLOW_EMPTY_CANDIDATES", "0") == "1"
EMPTY_POOL_MARKER = "retrieved_candidates: []"


def _qa_check(row: dict, shard_name: str = "") -> tuple[bool, str]:
    messages = row.get("messages", [])
    if len(messages) != 3:
        return False, "expected exactly 3 messages (system/user/assistant)"

    user_content = messages[1].get("content", "")
    if len(user_content) < MIN_USER_CONTENT_CHARS:
        return False, f"user content too short ({len(user_content)} chars)"

    if shard_name == "footage_sft" and not ALLOW_EMPTY_CANDIDATES and EMPTY_POOL_MARKER in user_content:
        return False, "footage row has an empty candidate pool (nothing to confirm; retrieval not wired in yet)"

    assistant_content = messages[2].get("content", "")
    if not assistant_content:
        return False, "empty assistant content"
    if FAKE_MARKER in assistant_content:
        return False, f"contains '{FAKE_MARKER}' marker — fake-mode test data, not real output"

    try:
        json.loads(assistant_content)
    except json.JSONDecodeError:
        return False, "assistant content is not valid JSON"

    return True, ""


def _gate_shard(in_path: Path, out_path: Path, log_path: Path, shard_name: str) -> dict:
    if not in_path.exists():
        return {"kept": 0, "total": 0}

    rows = []
    with open(in_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    seen = set()
    kept = []
    for row in rows:
        ok, reason = _qa_check(row, shard_name)
        if not ok:
            with open(log_path, "a") as f:
                f.write(f"[{shard_name}] rejected: {reason}\n")
            continue
        key = json.dumps(row, sort_keys=True)
        if key in seen:
            with open(log_path, "a") as f:
                f.write(f"[{shard_name}] rejected: exact duplicate\n")
            continue
        seen.add(key)
        kept.append(row)

    with open(out_path, "w") as f:
        for row in kept:
            f.write(json.dumps(row) + "\n")

    return {"kept": len(kept), "total": len(rows)}


def run(video_input_dir: Path, video_out_dir: Path):
    sft_dir = video_out_dir / "05_sft"
    if not sft_dir.exists():
        raise FileNotFoundError(f"Stage E output not found: {sft_dir}. Run Stage E first.")

    log_path = sft_dir / "_qa_rejections.log"
    # Never delete an earlier log: append a run header so every run stays visible.
    with open(log_path, "a") as _f:
        _f.write(f"\n=== Stage F run {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    results = {}
    for shard in ["director_sft", "footage_sft", "style_sft", "story_architect_sft"]:
        in_path = sft_dir / f"{shard}.jsonl"
        # Gate in place: QA output replaces the pre-QA shard. Pre-QA content
        # is still visible in the rejection log for anyone debugging later.
        tmp_path = sft_dir / f"{shard}.pre_qa.jsonl"
        if tmp_path.exists():
            # Re-run after an interrupted Stage F: the .pre_qa file is the untouched
            # original; in_path may already be a gated copy. Always gate from the original.
            results[shard] = _gate_shard(tmp_path, in_path, log_path, shard)
        elif in_path.exists():
            in_path.rename(tmp_path)
            results[shard] = _gate_shard(tmp_path, in_path, log_path, shard)
        else:
            results[shard] = {"kept": 0, "total": 0}

    return results
