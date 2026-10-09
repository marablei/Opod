"""
Self-extraction: everything the pipeline needs to know about a video, taken from
the video file itself. No P1_A*.jsonl agent files involved.

Produces (cached in <video_out>/stage_a_extract/self_extract.json so a resumed run never redoes it):
  source      duration / fps / resolution / codecs            (ffprobe)
  shots       cut-to-cut shots                                 (ffmpeg scene filter)
  transcript  speech segments with timestamps + word times     (faster-whisper)

Why a transcript at all if the LLM can hear? Chunk boundaries are placed at speech
PAUSES (Stage B), and the Story Architect cites transcript segments. Both need
reliable timestamps, which Whisper gives and an LLM's guesses do not.

Honest limits:
  * Scene detection is a simple threshold on frame change (ffmpeg `scene` score):
    it misses soft dissolves and can over-cut on flashes. It is a hint to the model,
    not ground truth.
  * Whisper can hallucinate text over music/silence. VAD filtering reduces this; the
    data-quality report flags transcripts that cover little of the video.
  * faster-whisper has not been run on real audio in the environment this was written
    in; deploy/smoke_test.py loads it on the pod before any batch.
"""
import json
import os
import re
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

SCENE_THRESHOLD = float(os.environ.get("ORION_SCENE_THRESHOLD", "0.30"))
MIN_SHOT_SEC = float(os.environ.get("ORION_MIN_SHOT_SEC", "0.5"))
# large-v3 (not -turbo): turbo is faster but is known to be weaker on several non-English
# languages; this corpus is multilingual (mostly Arabic), so accuracy comes first.
WHISPER_MODEL = os.environ.get("ORION_WHISPER_MODEL", "large-v3")
# Spoken language: "auto" (Whisper detects it) or a code like "ar" / "en" to force it for every video.
# A file named language.txt next to a video (containing just the code) overrides this for that video.
LANGUAGE = os.environ.get("ORION_LANGUAGE", "auto").strip().lower() or "auto"
# Videos that switch language mid-way (e.g. Arabic with English quotes): ask Whisper to re-detect per
# segment. Needs faster-whisper >= 1.1; unproven on our data, so OFF by default.
MULTILINGUAL = os.environ.get("ORION_WHISPER_MULTILINGUAL", "0") == "1"
LOW_LANG_CONFIDENCE = float(os.environ.get("ORION_LOW_LANG_CONFIDENCE", "0.80"))
# "whisper" (default) | "none" (skip speech-to-text; chunks become fixed windows)
TRANSCRIBE = os.environ.get("ORION_TRANSCRIBE", "whisper")


def find_video(video_input_dir: Path) -> Path:
    for p in sorted(video_input_dir.iterdir()):
        if p.suffix.lower() in VIDEO_EXTENSIONS:
            return p
    raise FileNotFoundError(f"No video file in {video_input_dir} (looked for {sorted(VIDEO_EXTENSIONS)})")


def probe(video: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True, check=True, timeout=120).stdout
    info = json.loads(out)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    fps = None
    if v.get("r_frame_rate") and "/" in v["r_frame_rate"]:
        n, d = v["r_frame_rate"].split("/")
        fps = round(float(n) / float(d), 3) if float(d) else None
    dur = info.get("format", {}).get("duration") or v.get("duration")
    return {
        "duration_sec": float(dur) if dur else None,
        "fps": fps,
        "width": v.get("width"),
        "height": v.get("height"),
        "codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "has_audio": bool(a),
    }


def detect_shots(video: Path, duration_sec: float) -> list[dict]:
    """Cut times from ffmpeg's scene-change score, on a small 5 fps copy (fast)."""
    cmd = ["ffmpeg", "-nostats", "-i", str(video), "-an",
           "-vf", f"fps=5,scale=-2:180,select='gt(scene,{SCENE_THRESHOLD})',showinfo",
           "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    cuts = sorted({float(m) for m in re.findall(r"pts_time:([0-9.]+)", res.stderr)})
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= MIN_SHOT_SEC and (duration_sec - c) >= MIN_SHOT_SEC:
            bounds.append(c)
    bounds.append(duration_sec)
    return [
        {"shot_id": f"SHOT_{i:04d}", "start_sec": round(s, 3), "end_sec": round(e, 3),
         "duration_sec": round(e - s, 3)}
        for i, (s, e) in enumerate(zip(bounds, bounds[1:]), start=1)
    ]


def pacing_from_shots(shots: list[dict], duration_sec: float) -> dict:
    """Cheap pacing numbers for the Style prompt, derived from our own shot list."""
    if not shots or not duration_sec:
        return {}
    lens = sorted(s["duration_sec"] for s in shots)
    return {
        "shots_total": len(shots),
        "cuts_per_minute": round(len(shots) / (duration_sec / 60.0), 2),
        "avg_shot_sec": round(sum(lens) / len(lens), 2),
        "median_shot_sec": round(lens[len(lens) // 2], 2),
        "shortest_shot_sec": lens[0],
        "longest_shot_sec": lens[-1],
    }


def pick_language(video_input_dir: Path) -> tuple[str | None, str]:
    """(language code or None for auto-detect, where it came from)."""
    side = video_input_dir / "language.txt"
    if side.exists():
        code = side.read_text(encoding="utf-8").strip().lower()
        if code and code != "auto":
            return code, "language.txt"
    if LANGUAGE != "auto":
        return LANGUAGE, "ORION_LANGUAGE"
    return None, "auto-detect"


def transcribe(video: Path, language: str | None = None) -> tuple[list[dict], dict]:
    """Returns (segments, language_info).
    segments: [{id, start_sec, end_sec, text, words:[{word,start_sec,end_sec}]}] in the ORIGINAL spoken language.
    language_info: {"code", "probability", "multilingual"}.
    Fails loudly if faster-whisper is missing, instead of silently returning nothing."""
    if TRANSCRIBE == "none":
        return [], {"code": language, "probability": None, "multilingual": False}
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper is not installed (pip install 'faster-whisper>=1.1.0'), "
            "or set ORION_TRANSCRIBE=none to skip speech-to-text.") from e

    model = _load_whisper(WhisperModel)
    kwargs = dict(
        word_timestamps=True, vad_filter=True, beam_size=5,
        condition_on_previous_text=False,  # long-form: avoids repetition loops / early stop
        language=language,                 # None = auto-detect
    )
    if language is None:
        # look at several windows, not just the first 30 s (videos often open with music)
        kwargs.update(language_detection_segments=4, language_detection_threshold=0.5)
    if MULTILINGUAL:
        kwargs["multilingual"] = True
    try:
        segments, info = model.transcribe(str(video), **kwargs)
    except TypeError:  # older faster-whisper without those options
        for k in ("language_detection_segments", "language_detection_threshold", "multilingual"):
            kwargs.pop(k, None)
        segments, info = model.transcribe(str(video), **kwargs)
    out = []
    for i, seg in enumerate(segments, start=1):
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append({
            "id": i,
            "start_sec": round(float(seg.start), 3),
            "end_sec": round(float(seg.end), 3),
            "text": text,
            "words": [{"word": w.word, "start_sec": round(float(w.start), 3), "end_sec": round(float(w.end), 3)}
                      for w in (getattr(seg, "words", None) or [])],
        })
    lang_info = {
        "code": language or getattr(info, "language", None),
        "probability": None if language else (round(float(info.language_probability), 3)
                                              if getattr(info, "language_probability", None) is not None else None),
        "multilingual": MULTILINGUAL,
    }
    return out, lang_info


_whisper_cache = {}


def _load_whisper(WhisperModel):
    """GPU first (fast; ~2 GB for turbo), CPU int8 as a fallback so a missing CUDA
    library slows things down instead of failing the video."""
    if "m" in _whisper_cache:
        return _whisper_cache["m"]
    forced = os.environ.get("ORION_WHISPER_DEVICE", "auto")
    attempts = [("cuda", "float16"), ("cpu", "int8")] if forced == "auto" else [(forced, "float16" if forced == "cuda" else "int8")]
    last = None
    for device, ctype in attempts:
        try:
            m = WhisperModel(WHISPER_MODEL, device=device, compute_type=ctype)
            print(f"[self_extract] whisper '{WHISPER_MODEL}' loaded on {device} ({ctype})")
            _whisper_cache["m"] = m
            return m
        except Exception as e:  # CUDA/cuDNN missing, OOM, ...
            last = e
            print(f"[self_extract] whisper on {device} failed ({type(e).__name__}: {e}); trying next option")
    raise RuntimeError(f"could not load Whisper model '{WHISPER_MODEL}': {last}")


def run(video_input_dir: Path, video_out_dir: Path) -> dict:
    """Returns {"source", "shots", "transcript", "pacing"}; cached on disk."""
    (video_out_dir / "stage_a_extract").mkdir(parents=True, exist_ok=True)
    cache = video_out_dir / "stage_a_extract" / "self_extract.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if {"source", "shots", "transcript", "language"} <= set(data):
                return data
        except json.JSONDecodeError:
            pass  # half-written: redo

    video = find_video(video_input_dir)
    source = probe(video)
    duration = source.get("duration_sec") or 0.0
    shots = detect_shots(video, duration) if duration else []
    forced, lang_source = pick_language(video_input_dir)
    if source.get("has_audio", True):
        transcript, lang = transcribe(video, forced)
    else:
        transcript, lang = [], {"code": forced, "probability": None, "multilingual": False}
    lang["source"] = lang_source
    data = {
        "language": lang,
        "source": source,
        "shots": shots,
        "transcript": transcript,
        "pacing": pacing_from_shots(shots, duration),
        "settings": {"scene_threshold": SCENE_THRESHOLD, "min_shot_sec": MIN_SHOT_SEC,
                     "whisper_model": WHISPER_MODEL if TRANSCRIBE != "none" else None},
    }
    tmp = cache.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache)
    return data
