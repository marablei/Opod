"""
Stage C — Qwen3-Omni reasoning client.

Two modes, controlled by env var ORION_OMNI_MODE (default "fake"):
  - "fake": no network call, no ffmpeg, no video file required at all.
    Returns deterministic, schema-valid placeholder JSON. Lets the whole
    pipeline (C -> D -> E -> F -> G) be tested on a laptop at zero GPU cost.
  - "live": real requests to ORION_OMNI_ENDPOINT (vLLM OpenAI-compatible
    /v1/chat/completions) with the actual video + audio attached.

The LLM gets BOTH the agent-evidence text AND the real media, every call:
  - Director/Footage (per chunk): a short ffmpeg clip of that chunk (video
    track only, downscaled) PLUS that chunk's audio as a separate 16 kHz wav.
    Audio is sent separately because vLLM's server doesn't support
    use_audio_in_video; without this the model would never actually hear the
    music/SFX the Director is supposed to reason about.
  - Style/Story Architect (per video): N evenly-sampled frames.

RESUME / DURABILITY (live mode only):
  Every finished model call is written to stage_c_omni/_cache_live/<key>.json
  immediately. If the GPU dies mid-stage, the next run (on any pod, after the
  output folder is pulled back from Drive) skips every call already cached
  and continues from the first missing one. CHECKPOINT_HOOK (set by the
  orchestrator) pushes the cache to Drive every ~90s. Fake mode never caches,
  so fake placeholders can never be reused by a later live run.

UNVERIFIED until the 1-video GPU test: the exact multimodal payload shape
Qwen3-Omni's vLLM server accepts. We use the documented OpenAI-style parts
(video_url / audio_url / image_url as base64 data URLs). `deploy/smoke_test.py`
sends exactly this payload to the live server with a synthetic clip — run it
(setup_runpod.sh does) BEFORE launching a real batch.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from omni_client.prompts import (
    build_director_prompt, build_footage_prompt,
    build_style_prompt, build_story_architect_prompt,
)
from omni_client import validators
from orchestrator import events

MODE = os.environ.get("ORION_OMNI_MODE", "fake")
ENDPOINT = os.environ.get("ORION_OMNI_ENDPOINT", "http://127.0.0.1:8901/v1/chat/completions")
MODEL_NAME = os.environ.get("ORION_OMNI_MODEL", "qwen3-omni-30b-a3b-instruct")
TIMEOUT_SEC = int(os.environ.get("ORION_OMNI_TIMEOUT", "600"))
MAX_TOKENS = int(os.environ.get("ORION_OMNI_MAX_TOKENS", "2048"))
TEMPERATURE = float(os.environ.get("ORION_OMNI_TEMPERATURE", "0.2"))
JSON_MODE = os.environ.get("ORION_OMNI_JSON_MODE", "1") == "1"
SEND_MODALITIES = os.environ.get("ORION_OMNI_SEND_MODALITIES", "0") == "1"  # only for the vllm-omni server
MAX_RETRIES = int(os.environ.get("ORION_OMNI_RETRIES", "3"))

# Keep video tokens bounded so a 75 s chunk fits in a 32k context.
# Rough budget (unverified): ~75 s * 2 fps at 360p is on the order of 15-20k tokens.
CLIP_FPS = float(os.environ.get("ORION_CLIP_FPS", "2"))
CLIP_HEIGHT = int(os.environ.get("ORION_CLIP_HEIGHT", "360"))
FRAME_HEIGHT = int(os.environ.get("ORION_FRAME_HEIGHT", "480"))
STYLE_STORY_FRAME_COUNT = int(os.environ.get("ORION_STYLE_FRAMES", "12"))
# An invalid answer (see omni_client/validators.py) is sent back to the model with the reasons, this many extra times.
VALIDATE_RETRIES = int(os.environ.get("ORION_VALIDATE_RETRIES", "2"))
# Recovery round (after the judge): every rejected answer gets this many further attempts, with the exact reasons and stricter rules.
RECOVER_ATTEMPTS = int(os.environ.get("ORION_RECOVER_ATTEMPTS", "2"))
# Footage calls produce rows that Stage F holds back until the retrieval layer exists (empty candidate pool),
# so by default they are not made at all: it halves the GPU time per video. Set to 1 once retrieval is wired in.
FOOTAGE_CALLS = os.environ.get("ORION_FOOTAGE_CALLS", "0") == "1"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

# Set by the orchestrator: called after cached calls so progress reaches Drive.
CHECKPOINT_HOOK = None


# --------------------------------------------------------------------- media
def find_source_video(video_input_dir: Path) -> Path:
    for p in sorted(video_input_dir.iterdir()):
        if p.suffix.lower() in VIDEO_EXTENSIONS:
            return p
    raise FileNotFoundError(
        f"No video file found in {video_input_dir} (looked for {VIDEO_EXTENSIONS}). "
        f"Each INPUT/<video>/ folder must contain the video file alongside its P1_A01-A30.jsonl."
    )


def extract_clip(source_video: Path, start_sec: float, end_sec: float, out_path: Path):
    """Video-only clip for one chunk's time range, downscaled + fps-capped.
    Re-encodes (not stream-copy) so the cut lands exactly on start/end."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, end_sec - start_sec)
    cmd = [
        "ffmpeg", "-y", "-ss", str(start_sec), "-i", str(source_video), "-t", str(duration),
        "-an", "-vf", f"fps={CLIP_FPS},scale=-2:{CLIP_HEIGHT}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-loglevel", "error", str(out_path),
    ]
    subprocess.run(cmd, check=True)


def extract_audio(source_video: Path, start_sec: float, end_sec: float, out_path: Path) -> bool:
    """16 kHz mono wav for the chunk. Returns False if the source has no audio."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, end_sec - start_sec)
    cmd = [
        "ffmpeg", "-y", "-ss", str(start_sec), "-i", str(source_video), "-t", str(duration),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-loglevel", "error", str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True)
        return out_path.exists() and out_path.stat().st_size > 0
    except subprocess.CalledProcessError:
        return False


def sample_frames(source_video: Path, n: int, out_dir: Path, duration_sec: float) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_paths = []
    for i in range(n):
        t = (duration_sec * i) / max(1, n)
        out_path = out_dir / f"frame_{i:02d}.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", str(t), "-i", str(source_video), "-frames:v", "1",
            "-vf", f"scale=-2:{FRAME_HEIGHT}", "-q:v", "4", "-loglevel", "error", str(out_path),
        ]
        subprocess.run(cmd, check=True)
        frame_paths.append(out_path)
    return frame_paths


def _b64_data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


# ------------------------------------------------------------- model calls
def _call_model(
    system_prompt: str,
    user_content: str,
    video_clip_path: Path | None = None,
    audio_path: Path | None = None,
    frame_paths: list[Path] | None = None,
) -> dict:
    if MODE == "fake":
        return _fake_response(system_prompt, user_content)
    return _live_call(system_prompt, user_content, video_clip_path, audio_path, frame_paths)


def _fake_response(system_prompt: str, user_content: str) -> dict:
    """Deterministic placeholders in each schema's real shape. Every one
    carries the literal 'FAKE_placeholder' marker, which Stage F rejects."""
    if "documentary editor" in system_prompt:  # Director
        return {
            "editorial_function": "FAKE_placeholder",
            "treatment": "direct_broll",
            "footage_need": {"query": "FAKE_placeholder", "duration_sec": 0},
            "audio_need": {"query": "FAKE_placeholder", "duration_sec": 0, "type": "music"},
            "reasoning": "FAKE: no model call made (ORION_OMNI_MODE=fake)",
        }
    if "footage researcher" in system_prompt:  # Footage
        return {
            "decision": "FAKE_placeholder",
            "confirmed_asset_ids": [],
            "rejected_asset_ids": [],
            "reasoning": "FAKE: no model call made (ORION_OMNI_MODE=fake)",
        }
    if "style analyst" in system_prompt:  # Style
        return {
            "pacing_target": "FAKE_placeholder",
            "typography_system": "FAKE_placeholder",
            "color_lut": "FAKE_placeholder",
            "graphic_density": "FAKE_placeholder",
            "sound_design_intensity": "FAKE_placeholder",
            "map_motion_frequency": "FAKE_placeholder",
        }
    if "story architect" in system_prompt:  # Story Architect
        return {
            "act_1_hook": {"segment_id": "FAKE", "function": "FAKE_placeholder", "note": "FAKE"},
            "act_1_anchor": {"segment_id": "FAKE", "function": "FAKE_placeholder", "note": "FAKE"},
            "act_2_escalation": [],
            "act_3_payoff": {"segment_id": "FAKE", "function": "FAKE_placeholder", "note": "FAKE"},
            "act_3_closing": {"segment_id": "FAKE", "function": "FAKE_placeholder", "note": "FAKE"},
        }
    raise ValueError("Unrecognized prompt type for fake mode")


def _ph(system_prompt: str, user_prompt: str) -> str:
    """Short fingerprint of the exact prompt. Part of every cache key, so changing a prompt can never
    silently reuse answers that were written for the old one."""
    return hashlib.sha1((validators.VERSION + "\x00" + system_prompt + "\x00" + user_prompt).encode("utf-8")).hexdigest()[:8]


def _parse_json_loose(text: str) -> dict:
    """Models sometimes wrap JSON in ```json fences or add a sentence around it."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.DOTALL)
        if not m:
            raise
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):  # a bare list/string would crash later; retry instead
        raise json.JSONDecodeError("model returned JSON that is not an object", t, 0)
    return obj


def build_payload(
    system_prompt: str,
    user_content: str,
    video_clip_path: Path | None,
    audio_path: Path | None,
    frame_paths: list[Path] | None,
) -> dict:
    parts = []
    if video_clip_path is not None:
        parts.append({"type": "video_url", "video_url": {"url": _b64_data_url(video_clip_path, "video/mp4")}})
    if audio_path is not None:
        parts.append({"type": "audio_url", "audio_url": {"url": _b64_data_url(audio_path, "audio/wav")}})
    for fp in frame_paths or []:
        parts.append({"type": "image_url", "image_url": {"url": _b64_data_url(fp, "image/jpeg")}})
    parts.append({"type": "text", "text": user_content})  # text last, as in the model card examples

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": parts},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    if JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    if SEND_MODALITIES:
        payload["modalities"] = ["text"]
    return payload


def _live_call(system_prompt, user_content, video_clip_path, audio_path, frame_paths) -> dict:
    payload = build_payload(system_prompt, user_content, video_clip_path, audio_path, frame_paths)
    data = json.dumps(payload, ensure_ascii=False).encode()
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(ENDPOINT, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                body = json.loads(resp.read())
            content = body["choices"][0]["message"]["content"]
            return _parse_json_loose(content)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code in (400, 422):
                break  # request itself is wrong; retrying won't help
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, KeyError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < MAX_RETRIES:
            time.sleep(5 * attempt)
    raise RuntimeError(f"Qwen3-Omni call failed after {MAX_RETRIES} tries: {last_err}")


def _feedback(user_prompt: str, problems: list[str]) -> str:
    return (user_prompt + "\n\nYour previous answer was rejected by an automatic check for these reasons:\n- "
            + "\n- ".join(problems) + "\nAnswer again with ONE corrected JSON object that fixes every point.")


def _ask_validated(kind, sys_p, user_p, ctx, *, clip=None, audio=None, frames=None, label="",
                   initial_problems=None, hint="", retries=None):
    """Call the model; check the answer with omni_client/validators.py; if it is invalid, ask again with the
    reasons (VALIDATE_RETRIES times). Returns (answer, problems_left). In fake mode there is no check.
    Recovery rounds pass the earlier failure reasons (initial_problems) and stricter extra rules (hint)."""
    problems: list[str] = list(initial_problems or [])
    extra = f"\n\nExtra rules for this answer:\n{hint}" if hint else ""
    result = None
    attempts = 0
    for attempt in range(1, (VALIDATE_RETRIES if retries is None else retries) + 2):
        attempts = attempt
        t0 = time.time()
        raw = _call_model(sys_p, _feedback(user_p + extra, problems) if problems else user_p + extra,
                          video_clip_path=clip, audio_path=audio, frame_paths=frames)
        if MODE == "fake":
            return raw, []
        result = validators.normalize(kind, raw, ctx)
        problems = validators.check(kind, result, ctx)
        events.emit("model_call", call=kind, label=label, attempt=attempt, sec=round(time.time() - t0, 1),
                    valid=not problems, problems=problems[:4])
        if not problems:
            break
    if attempts > 1:
        result["_attempts"] = attempts      # kept in the raw file as provenance; stripped before the training rows
    if problems:
        result["_problems"] = problems
    return result, problems


class _Progress:
    """stage_c_omni/_progress.json: real counters the dashboard reads (never estimated)."""

    def __init__(self, path: Path, total: int):
        self.path, self.d = path, {"total": total, "done": 0, "cache_hits": 0, "retried": 0, "gave_up": 0,
                                   "current": None, "started_at": time.time()}
        self._write()

    def _write(self):
        self.d["updated_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d), encoding="utf-8")
        tmp.replace(self.path)

    def start(self, label):
        self.d["current"] = label; self._write()

    def finish(self, *, cached=False, attempts=1, gave_up=False):
        self.d["done"] += 1
        self.d["cache_hits"] += int(cached)
        self.d["retried"] += int(attempts > 1)
        self.d["gave_up"] += int(gave_up)
        self.d["current"] = None
        self._write()


# --------------------------------------------------------------- checkpointing
class _Cache:
    """One json file per finished model call. Live mode only."""

    def __init__(self, directory: Path, enabled: bool):
        self.dir = directory
        self.enabled = enabled
        if enabled:
            directory.mkdir(parents=True, exist_ok=True)

    def get(self, key: str):
        if not self.enabled:
            return None
        p = self.dir / f"{key}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None  # half-written file from a crash: just redo that call
        return None

    def put(self, key: str, value: dict):
        if not self.enabled:
            return
        tmp = self.dir / f"{key}.json.tmp"
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / f"{key}.json")  # atomic: never leaves a torn file
        if CHECKPOINT_HOOK is not None:
            try:
                CHECKPOINT_HOOK()
            except Exception as e:  # sync trouble must never kill the run
                print(f"[stage_c] checkpoint hook failed: {e}")


def run(video_input_dir: Path, video_out_dir: Path):
    chunks_dir = video_out_dir / "stage_b_chunks"
    if not chunks_dir.exists():
        raise FileNotFoundError(f"Stage B output not found: {chunks_dir}. Run Stage B first.")

    segment_chunks = json.loads((chunks_dir / "segment_chunks.json").read_text(encoding="utf-8"))
    style_rollup = json.loads((chunks_dir / "style_rollup.json").read_text(encoding="utf-8"))
    story_input = json.loads((chunks_dir / "story_input.json").read_text(encoding="utf-8"))

    out_dir = video_out_dir / "stage_c_omni"
    out_dir.mkdir(parents=True, exist_ok=True)
    live = MODE == "live"
    cache = _Cache(out_dir / "_cache_live", enabled=live)
    prog = _Progress(out_dir / "_progress.json", total=len(segment_chunks) * (2 if FOOTAGE_CALLS else 1) + 2)

    source_video = find_source_video(video_input_dir) if live else None
    clips_dir = out_dir / "_clips"
    frames_dir = out_dir / "_frames"
    duration_sec = style_rollup.get("duration_sec") or story_input.get("duration_sec")

    # Director (+ Footage only when enabled): one call per chunk, same clip+audio for both.
    director_results, footage_results = [], []
    steps = [("director", build_director_prompt, director_results)]
    if FOOTAGE_CALLS:
        steps.append(("footage", build_footage_prompt, footage_results))
    for chunk in segment_chunks:
        cid = chunk["chunk_id"]
        media = {}

        def get_media():
            """Extract this chunk's clip+audio once, only if a call actually needs it."""
            if not media:
                clip = clips_dir / f"{cid}.mp4"
                wav = clips_dir / f"{cid}.wav"
                extract_clip(source_video, chunk["start_sec"], chunk["end_sec"], clip)
                media["clip"] = clip
                media["audio"] = wav if extract_audio(source_video, chunk["start_sec"], chunk["end_sec"], wav) else None
            return media["clip"], media["audio"]

        for label, builder, bucket in steps:
            # key includes the chunk's time range and the prompt fingerprint: if chunking or prompts ever change,
            # stale answers are NOT reused
            sys_p, user_p = builder(chunk)
            key = f"{label}__{cid}__{int(chunk['start_sec'] * 1000)}_{int(chunk['end_sec'] * 1000)}__{_ph(sys_p, user_p)}"
            tag = f"{label} {cid}"
            prog.start(tag)
            result = cache.get(key)
            if result is not None:
                prog.finish(cached=True, gave_up=bool(result.get("_problems")))
                events.emit("model_call_cached", call=label, label=tag)
            else:
                clip, audio = get_media() if live else (None, None)
                result, problems = _ask_validated(label, sys_p, user_p, chunk, clip=clip, audio=audio, label=tag)
                result["chunk_id"] = cid
                cache.put(key, result)
                prog.finish(attempts=result.get("_attempts", 1), gave_up=bool(problems))
            bucket.append(result)

    # Style + Story Architect: one call each, sampled frames of the whole video.
    frames: list[Path] = []

    def get_frames():
        if live and not frames:
            frames.extend(sample_frames(source_video, STYLE_STORY_FRAME_COUNT, frames_dir, duration_sec))
        return frames

    singles = {}
    for kind, builder, ctx_obj in (("style", build_style_prompt, style_rollup),
                                   ("story", build_story_architect_prompt, story_input)):
        sys_p, user_p = builder(ctx_obj)
        key = f"{kind}__{_ph(sys_p, user_p)}"
        prog.start(kind)
        result = cache.get(key)
        if result is not None:
            prog.finish(cached=True, gave_up=bool(result.get("_problems")))
            events.emit("model_call_cached", call=kind, label=kind)
        else:
            result, problems = _ask_validated(kind, sys_p, user_p, ctx_obj, frames=get_frames() or None, label=kind)
            result["video_id"] = ctx_obj["video_id"]
            cache.put(key, result)
            prog.finish(attempts=result.get("_attempts", 1), gave_up=bool(problems))
        singles[kind] = result
    style_result, story_result = singles["style"], singles["story"]

    (out_dir / "director_raw.json").write_text(json.dumps(director_results, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "footage_raw.json").write_text(json.dumps(footage_results, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "style_raw.json").write_text(json.dumps(style_result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "story_architect_raw.json").write_text(json.dumps(story_result, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "mode": MODE,
        "director_calls": len(director_results),
        "footage_calls": len(footage_results),
        "style_calls": 1,
        "story_calls": 1,
        "clips_extracted": live,
        "resumed_from_cache": prog.d["cache_hits"],
        "retried": prog.d["retried"],
        "gave_up": prog.d["gave_up"],
    }


def recover(video_input_dir: Path, video_out_dir: Path, director_failures: dict, other_failures: dict, max_calls: int) -> dict:
    """Recovery round. director_failures = {chunk_id: [reasons]}; other_failures = {"style"|"story": [reasons]}.
    Re-asks only those answers (clip/audio/frames re-extracted), replaces the raw answer ONLY when the new one passes
    the checks, and returns a provenance record. Never touches answers that already passed."""
    if MODE != "live":
        return {"skipped": "not a live run", "changed": False}
    out_dir = video_out_dir / "stage_c_omni"
    chunks = {c["chunk_id"]: c for c in json.loads((video_out_dir / "stage_b_chunks" / "segment_chunks.json").read_text(encoding="utf-8"))}
    style_rollup = json.loads((video_out_dir / "stage_b_chunks" / "style_rollup.json").read_text(encoding="utf-8"))
    story_input = json.loads((video_out_dir / "stage_b_chunks" / "story_input.json").read_text(encoding="utf-8"))
    source_video = find_source_video(video_input_dir)
    clips_dir, frames_dir = out_dir / "_clips", out_dir / "_frames"
    record = {"director": {}, "single": {}, "calls": 0, "changed": False, "max_calls": max_calls}

    director_raw = json.loads((out_dir / "director_raw.json").read_text(encoding="utf-8"))
    for cid, probs in director_failures.items():
        chunk = chunks.get(cid)
        if chunk is None:
            continue
        if record["calls"] >= max_calls:
            record["director"][cid] = {"before": probs, "outcome": "not attempted: call budget used up"}
            continue
        clip = clips_dir / f"{cid}.mp4"; wav = clips_dir / f"{cid}.wav"
        extract_clip(source_video, chunk["start_sec"], chunk["end_sec"], clip)
        audio = wav if extract_audio(source_video, chunk["start_sec"], chunk["end_sec"], wav) else None
        sys_p, user_p = build_director_prompt(chunk)
        result, left = _ask_validated("director", sys_p, user_p, chunk, clip=clip, audio=audio, label=f"recover director {cid}",
                                      initial_problems=probs, hint=validators.escalation_hints(probs), retries=RECOVER_ATTEMPTS - 1)
        record["calls"] += result.get("_attempts", 1)
        ok = not left
        record["director"][cid] = {"before": probs, "attempts": result.get("_attempts", 1),
                                   "outcome": "recovered" if ok else "still failing", "after": left}
        if ok:
            result["chunk_id"] = cid
            director_raw = [result if e.get("chunk_id") == cid else e for e in director_raw]
            record["changed"] = True
    (out_dir / "director_raw.json").write_text(json.dumps(director_raw, indent=2, ensure_ascii=False), encoding="utf-8")

    frames: list[Path] = []
    for kind, builder, ctx_obj, fname in (("style", build_style_prompt, style_rollup, "style_raw.json"),
                                          ("story", build_story_architect_prompt, story_input, "story_architect_raw.json")):
        probs = other_failures.get(kind)
        if not probs:
            continue
        if record["calls"] >= max_calls:
            record["single"][kind] = {"before": probs, "outcome": "not attempted: call budget used up"}
            continue
        if not frames:
            frames.extend(sample_frames(source_video, STYLE_STORY_FRAME_COUNT, frames_dir, story_input.get("duration_sec") or 0))
        sys_p, user_p = builder(ctx_obj)
        result, left = _ask_validated(kind, sys_p, user_p, ctx_obj, frames=frames or None, label=f"recover {kind}",
                                      initial_problems=probs, hint=validators.escalation_hints(probs), retries=RECOVER_ATTEMPTS - 1)
        record["calls"] += result.get("_attempts", 1)
        ok = not left
        record["single"][kind] = {"before": probs, "attempts": result.get("_attempts", 1),
                                  "outcome": "recovered" if ok else "still failing", "after": left}
        if ok:
            result["video_id"] = ctx_obj["video_id"]
            (out_dir / fname).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            record["changed"] = True
    return record
