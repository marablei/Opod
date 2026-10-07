#!/usr/bin/env python3
"""Sends ONE real multimodal request (3 s synthetic video + tone + text) to the
live model server using the exact same payload builder as Stage C.
Proves: server up, video_url + audio_url parts accepted, JSON comes back.
Cheap (seconds). Run before any real batch."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ORION_OMNI_MODE", "live")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from omni_client import stage_c_omni as c

tmp = Path(tempfile.mkdtemp())
src = tmp / "src.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=640x360:rate=10",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-c:a", "aac",
                "-pix_fmt", "yuv420p", "-loglevel", "error", str(src)], check=True)
clip, wav = tmp / "clip.mp4", tmp / "clip.wav"
c.extract_clip(src, 0, 3, clip)
has_audio = c.extract_audio(src, 0, 3, wav)
print(f"clip {clip.stat().st_size/1024:.0f} KB, audio={'yes' if has_audio else 'NO'}")

# --- self-extraction pieces (these replace the A-file agents) ---
from pipeline import self_extract as se
try:
    info = se.probe(src)
    shots = se.detect_shots(src, info["duration_sec"])
    segs = se.transcribe(src)  # synthetic tone has no speech: zero segments is the expected, healthy answer
    print(f"self-extract OK: duration {info['duration_sec']}s, {len(shots)} shot(s), {len(segs)} speech segment(s)")
except Exception as e:
    print(f"✗ SELF-EXTRACT FAILED (Whisper/ffmpeg): {e}")
    sys.exit(1)

system = "You describe media. Respond with valid JSON only."
user = 'Describe what you see and what you hear. Respond as {"video": "<one sentence>", "audio": "<one sentence>"}'
try:
    out = c._live_call(system, user, clip, wav if has_audio else None, None)
except Exception as e:
    print(f"✗ SMOKE TEST FAILED: {e}")
    sys.exit(1)
print("model answered:", out)
if not isinstance(out, dict) or not out:
    print("✗ empty/non-dict answer"); sys.exit(1)
print("✓ multimodal request works (video + audio + JSON)")

# Style/Story calls send several frames in ONE request - confirm the server allows that many images.
frames = c.sample_frames(src, c.STYLE_STORY_FRAME_COUNT, tmp / "frames", 4.0)
try:
    out2 = c._live_call(system, 'Respond as {"frames_seen": <number of images you received>}', None, None, frames)
except Exception as e:
    print(f"✗ MULTI-IMAGE REQUEST FAILED ({len(frames)} images): {e}")
    print("  Likely vLLM's per-prompt media limit. Restart with VLLM_EXTRA_ARGS set per `vllm serve --help | grep limit-mm`.")
    sys.exit(1)
print(f"✓ {len(frames)}-image request works:", out2)
