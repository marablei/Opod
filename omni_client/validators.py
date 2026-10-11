"""
Deterministic checks and fix-ups for what the teacher model answers. ONE place, used twice:

  * Stage C  — right after every model call. If the answer is invalid, the model is asked again
               with the exact reasons (ORION_VALIDATE_RETRIES times, default 2).
  * Stage D  — the judge re-runs the same checks, so nothing invalid can reach the training rows,
               even if it came from an old cache or from a different pod.

Why this exists (first real video): the model answered with generic text, a 10 s footage need for a
71 s chunk, a map-motion style value for a video that has no maps, and quotes nobody had said.
Instructions in a prompt reduce those; only code can GUARANTEE them gone.

Two kinds of function per answer type:
  validate_*  -> list of human-readable problems (empty list = valid). Never changes the answer.
  normalize_* -> returns a copy where values that code knows better than the model are overwritten
                 (durations from the chunk's real length, pacing from the real cut rate, id formats).
                 "Deterministic first, AI second": the model is not asked to guess what code can compute.
"""
import re
import unicodedata

# One vocabulary for the whole video: what is on screen. Used for the chunk's overall treatment and for
# each visual beat, so every row in the corpus is labelled the same way.
VISUAL_KINDS = [
    "talking_head",            # host/speaker on camera
    "broll",                   # cutaway footage illustrating the words
    "document_or_screenshot",  # document, article, web page, social post, screenshot
    "graphic_overlay",         # animated text/graphic/lower-third over footage
    "screen_recording",        # recorded screen/app
    "map",
    "chart_or_data",
    "product_closeup",         # a physical object shown in close-up (phone, package, prop)
    "archival_clip",           # news/archive/other-source clip
    "text_card",               # full-screen text/title card
    "other",
]
TREATMENTS = VISUAL_KINDS + ["mixed"]
AUDIO_TYPES = ["music", "sfx", "none"]
STYLE_KEYS = ["pacing_target", "typography_system", "color_lut", "graphic_density",
              "sound_design_intensity", "map_motion_frequency"]
NOT_PRESENT = "not_present"
STORY_SINGLE = ["act_1_hook", "act_1_anchor", "act_3_payoff", "act_3_closing"]

VERSION = "2"                  # part of every cache key: change a rule -> old cached answers are not reused
META_KEYS = {"video_id", "_problems", "_attempts"}   # bookkeeping added by Stage C; ignored by the checks
MAX_BEATS = 8
MIN_BEAT_COVERAGE = 0.60       # beats must account for at least this share of the chunk's length
TIME_TOLERANCE_SEC = 0.6
GENERIC_AUDIO = {"background music", "upbeat background music", "upbeat music", "music", "soft music",
                 "calm music", "background track", "instrumental music", "neutral background music"}
TECH_WORDS = re.compile(r"\b(codec|bitrate|resolution|h\.?264|mp4|fps|aac|kbps|1080p?|720p?|886p?|pixels?)\b", re.I)


# ----------------------------------------------------------------------------- helpers
def _words(s) -> int:
    return len(str(s).split())


def _norm_text(s: str) -> str:
    """Lowercase, drop accents/diacritics (incl. Arabic tashkeel) and punctuation, collapse spaces."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ـ", "")  # tatweel
    s = re.sub(r"[^\w\s]", " ", s.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


_QUOTE_RE = re.compile(r"\"([^\"]+)\"|“([^”]+)”|(?<![\w])'(.+?)'(?![\w])")


def quoted_phrases(text: str, min_words: int = 3) -> list[str]:
    out = []
    for m in _QUOTE_RE.finditer(str(text)):
        q = next(g for g in m.groups() if g)
        if _words(q) >= min_words:
            out.append(q)
    return out


def ungrounded_quotes(text: str, source_text: str) -> list[str]:
    """Quoted phrases (3+ words) in `text` that do not occur in `source_text` (the real transcript)."""
    src = _norm_text(source_text)
    return [q for q in quoted_phrases(text) if _norm_text(q) not in src]


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _is_str(x) -> bool:
    return isinstance(x, str) and x.strip() != ""


# ----------------------------------------------------------------------------- Director
DIRECTOR_KEYS = {"chunk_id", "editorial_function", "treatment", "visual_beats", "footage_need", "audio_need", "reasoning"}


def validate_director(entry: dict, chunk: dict) -> list[str]:
    p = []
    if not isinstance(entry, dict):
        return ["answer is not a JSON object"]
    if entry.get("chunk_id") != chunk["chunk_id"]:
        p.append(f"chunk_id must be exactly '{chunk['chunk_id']}'")
    extra = set(entry) - DIRECTOR_KEYS - META_KEYS
    missing = DIRECTOR_KEYS - set(entry)
    if missing:
        p.append(f"missing keys: {sorted(missing)}")
    if extra:
        p.append(f"unexpected keys: {sorted(extra)}")
    if missing:
        return p

    dur = float(chunk["duration_sec"])
    if not _is_str(entry["editorial_function"]) or _words(entry["editorial_function"]) > 14:
        p.append("editorial_function must be a short phrase (1-14 words)")
    if entry["treatment"] not in TREATMENTS:
        p.append(f"treatment must be one of {TREATMENTS}")

    beats = entry["visual_beats"]
    kinds = []
    if not isinstance(beats, list) or not beats:
        p.append("visual_beats must be a non-empty list")
    else:
        if len(beats) > MAX_BEATS:
            p.append(f"visual_beats has {len(beats)} items; at most {MAX_BEATS} (group neighbouring shots)")
        prev_end, covered = 0.0, 0.0
        for i, b in enumerate(beats[:MAX_BEATS + 4], 1):
            if not isinstance(b, dict) or set(b) != {"start_sec", "end_sec", "kind", "what"}:
                p.append(f"beat {i} must have exactly start_sec, end_sec, kind, what"); continue
            s, e = _num(b["start_sec"]), _num(b["end_sec"])
            if s is None or e is None:
                p.append(f"beat {i}: start_sec/end_sec must be numbers"); continue
            if not (-TIME_TOLERANCE_SEC <= s < e <= dur + TIME_TOLERANCE_SEC):
                p.append(f"beat {i}: times must satisfy 0 <= start < end <= {dur:.1f} (seconds from the START OF THIS CHUNK)")
            if s < prev_end - TIME_TOLERANCE_SEC:
                p.append(f"beat {i}: beats must be in order and not overlap")
            prev_end = max(prev_end, e)
            covered += max(0.0, min(e, dur) - max(s, 0.0))
            if b["kind"] not in VISUAL_KINDS:
                p.append(f"beat {i}: kind must be one of {VISUAL_KINDS}")
            else:
                kinds.append((b["kind"], max(0.0, min(e, dur) - max(s, 0.0))))
            if not _is_str(b["what"]) or not (2 <= _words(b["what"]) <= 14):
                p.append(f"beat {i}: 'what' must be 2-14 words saying what is on screen")
        if covered < MIN_BEAT_COVERAGE * dur:
            p.append(f"beats cover only {covered:.1f}s of the {dur:.1f}s chunk; list every visual change so they cover at least {MIN_BEAT_COVERAGE:.0%}")

    if kinds and entry["treatment"] in TREATMENTS:
        total = sum(d for _k, d in kinds) or 1.0
        host_share = sum(d for k, d in kinds if k == "talking_head") / total
        if entry["treatment"] == "talking_head" and host_share < 0.70:
            p.append("treatment is talking_head but the beats show the host for less than 70% of the chunk; use 'mixed' or the dominant visual kind")
        if entry["treatment"] != "talking_head" and entry["treatment"] != "mixed" and \
                not any(k == entry["treatment"] for k, _d in kinds):
            p.append(f"treatment '{entry['treatment']}' does not appear among the beats")
        if entry["treatment"] != "talking_head" and host_share == 1.0:
            p.append("every beat is talking_head, so treatment must be talking_head")

    fn, an = entry["footage_need"], entry["audio_need"]
    if not isinstance(fn, dict) or set(fn) != {"query", "duration_sec"}:
        p.append("footage_need must have exactly query and duration_sec")
    else:
        q = fn["query"]
        only_host = bool(kinds) and all(k == "talking_head" for k, _d in kinds)
        if not _is_str(q):
            p.append("footage_need.query must be a search phrase, or \"none\" when only the host is on screen")
        elif q.strip().lower() == "none":
            if kinds and not only_host:
                p.append("footage_need.query is \"none\" but the beats include non-host visuals; name what to search for")
        elif _words(q) > 12:
            p.append("footage_need.query must be at most 12 words")
    if not isinstance(an, dict) or set(an) != {"query", "duration_sec", "type"}:
        p.append("audio_need must have exactly query, duration_sec, type")
    else:
        if an["type"] not in AUDIO_TYPES:
            p.append(f"audio_need.type must be one of {AUDIO_TYPES}")
        q = an["query"]
        if not _is_str(q):
            p.append("audio_need.query must be a search phrase, or \"none\"")
        else:
            ql = q.strip().lower()
            if (an["type"] == "none") != (ql == "none"):
                p.append("audio_need.type and query must both be \"none\" when no music/SFX is needed")
            if _words(q) > 12:
                p.append("audio_need.query must be at most 12 words")
            if ql in GENERIC_AUDIO:
                p.append(f"audio_need.query '{q}' is too generic; name mood + instrument/texture + tempo that you actually HEAR")

    if not _is_str(entry["reasoning"]) or len(entry["reasoning"]) > 500:
        p.append("reasoning must be 1-3 sentences (max 500 characters)")
    else:
        # evidence = what the model was shown for this chunk: the spoken transcript plus the on-screen text (OCR)
        ocr = " ".join(str(o.get("text", "")) for o in (chunk.get("ocr_text_regions") or []) if isinstance(o, dict))
        bad = ungrounded_quotes(entry["reasoning"], f"{chunk.get('transcript_text', '')} {ocr}")
        if bad:
            p.append(f"reasoning quotes words that are neither in this chunk's transcript nor in its on-screen text: {bad}")
    return p


def _query_from_beats(beats) -> str:
    """Search phrase taken from the model's own description of the longest non-host beat (at most 12 words)."""
    best = None
    for b in beats or []:
        if isinstance(b, dict) and b.get("kind") != "talking_head" and isinstance(b.get("what"), str):
            d = (_num(b.get("end_sec")) or 0) - (_num(b.get("start_sec")) or 0)
            if best is None or d > best[0]:
                best = (d, b["what"])
    return " ".join(best[1].split()[:12]) if best else ""


MUSIC_UNSPECIFIED = "music bed present; character not specified"


def salvage_audio(entry: dict, problems: list[str]) -> dict | None:
    """Last resort for the judge: when the ONLY remaining problem is a generic audio query after all re-asks, keep the chunk
    (its beats are fine) and record honestly that music is present but was not described. Returns None when not applicable."""
    if problems and all("too generic" in x for x in problems) and isinstance(entry.get("audio_need"), dict):
        e = dict(entry); an = dict(e["audio_need"]); an["query"] = MUSIC_UNSPECIFIED; e["audio_need"] = an
        return e
    return None


def _cap_beats(beats, max_beats: int):
    """More than MAX_BEATS beats: merge neighbouring beats of the SAME kind (shortest pair first) so no information about
    what kind of visual appeared is lost - exactly what the prompt asks the model to do ("group neighbouring shots")."""
    if not isinstance(beats, list) or len(beats) <= max_beats:
        return beats
    out = [dict(b) for b in beats]
    while len(out) > max_beats:
        best = None
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            if a.get("kind") == b.get("kind"):
                span = b["end_sec"] - a["start_sec"]
                if best is None or span < best[0]:
                    best = (span, i)
        if best is None:
            return beats                                # nothing safe to merge: leave it for the validator
        i = best[1]
        a, b = out[i], out[i + 1]
        words = f"{a.get('what', '')}; {b.get('what', '')}".split()
        out[i:i + 2] = [{"start_sec": a["start_sec"], "end_sec": b["end_sec"], "kind": a["kind"], "what": " ".join(words[:14])}]
    return out


def describe_repairs(raw: dict, final: dict) -> list[str]:
    """What code changed between the model's raw Director answer and what is stored. Pure comparison, no model.
    Durations are always recomputed by code, so they are not counted as repairs."""
    out = []
    rb, fb = raw.get("visual_beats"), final.get("visual_beats")
    if isinstance(rb, list) and isinstance(fb, list):
        if len(rb) != len(fb):
            out.append(f"beats_changed:{len(rb)}->{len(fb)}")
        else:
            for a, b in zip(rb, fb):
                try:
                    if a.get("kind") != b.get("kind") or abs(float(a["start_sec"]) - float(b["start_sec"])) > 0.15 \
                            or abs(float(a["end_sec"]) - float(b["end_sec"])) > 0.15:
                        out.append("beats_adjusted"); break
                except (KeyError, TypeError, ValueError, AttributeError):
                    out.append("beats_adjusted"); break
    if raw.get("treatment") != final.get("treatment"):
        out.append(f"treatment_rederived:{raw.get('treatment')}->{final.get('treatment')}")
    rq, fq = (raw.get("footage_need") or {}).get("query"), (final.get("footage_need") or {}).get("query")
    if isinstance(rq, str) and rq.strip() != fq:
        out.append("footage_query_derived_from_beats" if rq.strip().lower() == "none" else "footage_query_set_none")
    ra, fa = (raw.get("audio_need") or {}).get("query"), (final.get("audio_need") or {}).get("query")
    if ra != fa:
        out.append("audio_neutral_label")
    return out


def escalation_hints(problems) -> str:
    """Stricter extra rules for a recovery attempt, chosen by code from the exact failure reasons (no model involved)."""
    t = " ".join(str(x) for x in (problems or [])).lower()
    h = []
    if "quotes words" in t or "neither in this chunk" in t or "nobody says" in t:
        h.append("Do not use quotation marks anywhere in your answer; paraphrase instead of quoting.")
    if "at most 8" in t or "visual_beats has" in t:
        h.append("Use at most 5 visual_beats: merge neighbouring shots of the same kind.")
    if "in order and not overlap" in t:
        h.append("List visual_beats strictly in time order; each beat must start at or after the end of the previous beat; no overlapping times.")
    if "times must satisfy" in t:
        h.append("Every beat time is in seconds from the START OF THIS CHUNK and must lie between 0 and the chunk length.")
    if "too generic" in t:
        h.append('For audio_need.query name a concrete mood, an instrument and a tempo you actually hear, for example "tense low strings, slow pulse".')
    if "treatment" in t:
        h.append("Set treatment to 'mixed' unless one visual kind clearly dominates the beats.")
    if "footage_need.query" in t:
        h.append("footage_need.query must name the main non-host visual as a short search phrase, never 'none'.")
    if "act point" in t:
        h.append('Choose act_3_payoff and act_3_closing from segments late in the video, using the "% into the video" markers on each line.')
    return "\n".join(f"- {x}" for x in h)


def _repair_overlaps(beats, dur: float, min_len: float = 0.4):
    """A cutaway shown OVER the host (host 0-14.6, chart 2.8-6.8) is a normal editing pattern, not a mistake. Rebuild it as
    ordered, non-overlapping beats: later/shorter beats are painted over earlier/longer ones, leftovers of the host become
    their own beats (host 0-2.8, chart 2.8-6.8, host 6.8-14.6). Pure code, no model call. Untouched when nothing overlaps."""
    if not isinstance(beats, list):
        return beats
    items = []
    for b in beats:
        if isinstance(b, dict) and _num(b.get("start_sec")) is not None and _num(b.get("end_sec")) is not None:
            s, e = max(0.0, min(float(b["start_sec"]), dur)), max(0.0, min(float(b["end_sec"]), dur))
            if e > s:
                items.append((s, e, b))
            continue
        return beats                                   # malformed beat: leave for the validator to report
    items.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    if all(items[i][0] >= items[i - 1][1] - 0.05 for i in range(1, len(items))):
        return beats                                   # nothing overlaps: keep exactly what the model said
    placed: list = []                                  # (start, end, beat dict)
    for s, e, b in items:
        nxt = []
        for ps, pe, pb in placed:
            if pe <= s or ps >= e:
                nxt.append((ps, pe, pb)); continue
            if ps < s:
                nxt.append((ps, s, pb))
            if pe > e:
                nxt.append((e, pe, pb))
        nxt.append((s, e, b))
        placed = nxt
    out = []
    for s, e, b in sorted(placed, key=lambda t: t[0]):
        if e - s >= min_len:
            nb = dict(b); nb["start_sec"], nb["end_sec"] = round(s, 1), round(e, 1)
            out.append(nb)
    return out or beats


def normalize_director(entry: dict, chunk: dict) -> dict:
    """Overwrite what code knows exactly: chunk_id, rounding, and the durations."""
    e = dict(entry)
    e["chunk_id"] = chunk["chunk_id"]
    dur = float(chunk["duration_sec"])
    e["visual_beats"] = _cap_beats(_repair_overlaps(e.get("visual_beats"), dur), MAX_BEATS)
    beats = []
    host_free = 0.0
    for b in e.get("visual_beats") or []:
        if isinstance(b, dict) and _num(b.get("start_sec")) is not None and _num(b.get("end_sec")) is not None:
            nb = dict(b)
            nb["start_sec"] = round(max(0.0, min(float(b["start_sec"]), dur)), 1)
            nb["end_sec"] = round(max(0.0, min(float(b["end_sec"]), dur)), 1)
            if nb["end_sec"] - nb["start_sec"] < 0.1:
                continue                              # beat lies outside the chunk (clamped to zero length): drop it
            beats.append(nb)
            if nb.get("kind") != "talking_head":
                host_free += max(0.0, nb["end_sec"] - nb["start_sec"])
    if beats:
        e["visual_beats"] = beats
        # treatment must agree with the beats: derive it with code when the model contradicted itself
        tot = sum(b["end_sec"] - b["start_sec"] for b in beats) or 1.0
        host_share = sum(b["end_sec"] - b["start_sec"] for b in beats if b.get("kind") == "talking_head") / tot
        kinds_present = {b.get("kind") for b in beats}
        t = e.get("treatment")
        if t in TREATMENTS:
            if host_share >= 0.999:
                e["treatment"] = "talking_head"
            elif t == "talking_head" and host_share < 0.70:
                e["treatment"] = "mixed"
            elif t not in ("talking_head", "mixed") and t not in kinds_present:
                e["treatment"] = "mixed"
    fn = dict(e.get("footage_need") or {})
    if isinstance(fn.get("query"), str):
        fn["query"] = fn["query"].strip()
        if beats and host_free == 0 and all(b.get("kind") == "talking_head" for b in beats):
            fn["query"] = "none"                      # only the host is on screen: there is nothing to source
        elif fn["query"].lower() == "none" and host_free > 0:
            q = _query_from_beats(beats)              # model said "none" but showed other visuals: use ITS OWN longest beat text
            if q:
                fn["query"] = q
        none_q = fn["query"].lower() == "none"
        fn["duration_sec"] = 0 if none_q else round(host_free if host_free > 0 else dur, 1)
        if none_q:
            fn["query"] = "none"
        e["footage_need"] = fn
    an = dict(e.get("audio_need") or {})
    if isinstance(an.get("type"), str):
        an["duration_sec"] = 0 if an["type"] == "none" else round(dur, 1)
        e["audio_need"] = an
    return e


# ----------------------------------------------------------------------------- Style
def pacing_label(cuts_per_minute: float) -> str:
    if cuts_per_minute < 8:
        bucket = "slow"
    elif cuts_per_minute < 16:
        bucket = "moderate"
    elif cuts_per_minute < 30:
        bucket = "fast"
    else:
        bucket = "very fast"
    return f"{bucket}, about {round(cuts_per_minute)} cuts per minute"


def validate_style(entry: dict, rollup: dict | None = None) -> list[str]:
    p = []
    if not isinstance(entry, dict):
        return ["answer is not a JSON object"]
    keys = set(entry) - META_KEYS
    if keys != set(STYLE_KEYS):
        if set(STYLE_KEYS) - keys:
            p.append(f"missing keys: {sorted(set(STYLE_KEYS) - keys)}")
        if keys - set(STYLE_KEYS):
            p.append(f"unexpected keys: {sorted(keys - set(STYLE_KEYS))}")
        return p
    for k in STYLE_KEYS:
        v = entry[k]
        if not _is_str(v):
            p.append(f"{k} must be a non-empty string"); continue
        if v.strip().lower() != NOT_PRESENT and _words(v) > 14:
            p.append(f"{k} must be at most 14 words")
        if TECH_WORDS.search(v):
            p.append(f"{k} contains a technical file fact (codec/resolution/fps); describe the visual style instead")
    return p


def normalize_style(entry: dict, rollup: dict | None = None) -> dict:
    e = dict(entry)
    for k in STYLE_KEYS:
        if isinstance(e.get(k), str) and e[k].strip().lower() == NOT_PRESENT:
            e[k] = NOT_PRESENT
    pacing = (rollup or {}).get("pacing") or ((rollup or {}).get("dna") or {}).get("pacing") or {}
    cpm = pacing.get("cuts_per_minute")
    if isinstance(cpm, (int, float)) and cpm > 0:
        e["pacing_target"] = pacing_label(float(cpm))  # measured, so it is computed, not guessed
    return e


# ----------------------------------------------------------------------------- Story Architect
def segment_number(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    m = re.fullmatch(r"\s*(?:seg[_\-\s]?)?0*(\d+)\s*", str(raw), flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def validate_story(entry: dict, story_input: dict) -> list[str]:
    p = []
    if not isinstance(entry, dict):
        return ["answer is not a JSON object"]
    allowed = set(STORY_SINGLE) | {"act_2_escalation"}
    keys = set(entry) - META_KEYS
    if keys != allowed:
        if allowed - keys:
            p.append(f"missing keys: {sorted(allowed - keys)}")
        if keys - allowed:
            p.append(f"unexpected keys: {sorted(keys - allowed)}")
        return p
    valid_ids = {t["id"] for t in story_input.get("transcript", [])}
    full_text = " ".join(t["text"] for t in story_input.get("transcript", []))
    esc = entry["act_2_escalation"]
    if not isinstance(esc, list) or not (1 <= len(esc) <= 4):
        p.append("act_2_escalation must be a list of 1 to 4 objects")
        esc = esc if isinstance(esc, list) else []
    ordered = [("act_1_hook", entry["act_1_hook"]), ("act_1_anchor", entry["act_1_anchor"])] + \
              [(f"act_2_escalation[{i}]", x) for i, x in enumerate(esc)] + \
              [("act_3_payoff", entry["act_3_payoff"]), ("act_3_closing", entry["act_3_closing"])]
    starts = {t["id"]: _num(t.get("start_sec")) for t in story_input.get("transcript", [])}
    total = _num(story_input.get("duration_sec")) or 0.0
    # (earliest start allowed, latest start allowed) as a share of the video; only applied to videos of 90 s or more
    windows = {"act_1_hook": (0.0, 0.35), "act_1_anchor": (0.0, 0.50), "act_3_payoff": (0.45, 1.0), "act_3_closing": (0.70, 1.0)}
    last = -1
    for name, pt in ordered:
        if not isinstance(pt, dict) or set(pt) != {"segment_id", "function", "note"}:
            p.append(f"{name} must be an object with exactly segment_id, function, note"); continue
        seg = segment_number(pt["segment_id"])
        if seg is None or seg not in valid_ids:
            p.append(f"{name}: segment_id '{pt['segment_id']}' is not a segment of this transcript")
        else:
            if seg < last:
                p.append(f"{name}: segment {seg} comes before an earlier act point; acts must follow the video order")
            last = max(last, seg)
            w, st = windows.get(name), starts.get(seg)
            if w and total >= 90 and st is not None and not (w[0] * total - 1 <= st <= w[1] * total + 1):
                p.append(f"{name}: segment {seg} starts at {st:.0f}s of a {total:.0f}s video; pick a segment between "
                         f"{w[0] * total:.0f}s and {w[1] * total:.0f}s for this act point")
        if not _is_str(pt["function"]) or _words(pt["function"]) > 12:
            p.append(f"{name}: function must be a short phrase (max 12 words)")
        if not _is_str(pt["note"]) or len(pt["note"]) > 300:
            p.append(f"{name}: note must be one sentence (max 300 characters)")
        else:
            bad = ungrounded_quotes(pt["note"], full_text)
            if bad:
                p.append(f"{name}: note quotes words that nobody says in the transcript: {bad}")
    return p


def normalize_story(entry: dict, story_input: dict | None = None) -> dict:
    e = dict(entry)
    pts = [e.get(k) for k in STORY_SINGLE] + list(e.get("act_2_escalation") or [])
    for pt in pts:
        if isinstance(pt, dict):
            n = segment_number(pt.get("segment_id"))
            if n is not None:
                pt["segment_id"] = f"seg_{n}"
    return e


# ----------------------------------------------------------------------------- Footage
def validate_footage(entry: dict, chunk: dict) -> list[str]:
    p = []
    if not isinstance(entry, dict):
        return ["answer is not a JSON object"]
    if entry.get("chunk_id") != chunk["chunk_id"]:
        p.append(f"chunk_id must be exactly '{chunk['chunk_id']}'")
    if entry.get("decision") not in ("confirm", "reject"):
        p.append("decision must be \"confirm\" or \"reject\"")
    if entry.get("confirmed_asset_ids"):
        p.append("confirmed_asset_ids must be empty: no candidate pool exists yet")
    return p


# ----------------------------------------------------------------------------- dispatch
def check(kind: str, entry: dict, ctx: dict) -> list[str]:
    """kind: director | footage (ctx = the chunk), style (ctx = style_rollup), story (ctx = story_input)."""
    if kind == "director":
        return validate_director(entry, ctx)
    if kind == "footage":
        return validate_footage(entry, ctx)
    if kind == "style":
        return validate_style(entry, ctx)
    if kind == "story":
        return validate_story(entry, ctx)
    raise ValueError(kind)


def normalize(kind: str, entry: dict, ctx: dict) -> dict:
    if kind == "director":
        return normalize_director(entry, ctx)
    if kind == "style":
        return normalize_style(entry, ctx)
    if kind == "story":
        return normalize_story(entry, ctx)
    return dict(entry)
