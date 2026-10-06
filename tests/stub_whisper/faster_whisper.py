"""Test stand-in for faster-whisper (the real one isn't installable where tests run).
Pretends the video is speech in 20 s sentences separated by 1 s pauses."""
import subprocess
from types import SimpleNamespace as NS


class WhisperModel:
    def __init__(self, name, device="cpu", compute_type="int8"):
        self.name, self.device = name, device

    def transcribe(self, path, **kw):
        assert kw.get("word_timestamps") and kw.get("vad_filter"), "pipeline must ask for words + VAD"
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "default=nw=1:nk=1", path], capture_output=True, text=True).stdout)
        segs, t, i = [], 0.0, 0
        while t + 20 <= dur:
            i += 1
            words = [NS(word=f" w{i}_{k}", start=t + k * 4, end=t + k * 4 + 3.5, probability=0.9) for k in range(5)]
            segs.append(NS(start=t, end=t + 20, text=f" Sentence number {i} about the topic.", words=words))
            t += 21
        return iter(segs), NS(language="en")
