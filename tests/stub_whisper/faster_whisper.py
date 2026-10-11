"""Test stand-in for faster-whisper (the real one isn't installable where tests run).
Pretends the video is speech in 20 s sentences separated by 1 s pauses."""
import os
import subprocess
from types import SimpleNamespace as NS


class WhisperModel:
    def __init__(self, name, device="cpu", compute_type="int8"):
        self.name, self.device = name, device

    def transcribe(self, path, **kw):
        assert kw.get("word_timestamps") and kw.get("vad_filter"), "pipeline must ask for words + VAD"
        if hasattr(path, "size"):  # decoded 16 kHz audio array (what the real pipeline now passes)
            dur = path.size / 16000.0
        else:
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                        "default=nw=1:nk=1", path], capture_output=True, text=True).stdout)
        # Pretend to detect Arabic when STUB_LANG=ar (or when the caller forces language="ar").
        lang = kw.get("language") or os.environ.get("STUB_LANG", "en")
        segs, t, i = [], 0.0, 0
        while t + 20 <= dur:
            i += 1
            words = [NS(word=f" w{i}_{k}", start=t + k * 4, end=t + k * 4 + 3.5, probability=0.9) for k in range(5)]
            text = f" الجملة رقم {i} عن الموضوع." if lang == "ar" else f" Sentence number {i} about the topic."
            segs.append(NS(start=t, end=t + 20, text=text, words=words))
            t += 21
        return iter(segs), NS(language=lang, language_probability=0.97)
