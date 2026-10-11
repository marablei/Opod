"""
Prompt templates for the 4 reasoning passes, matching the SFT dataset
formats already agreed on (system/user/assistant chat shape). Each builder
returns (system_prompt, user_content) — the client wraps these into
whatever request format the target endpoint (vLLM OpenAI-compatible, or a
fake local mock) expects.
"""

from omni_client.validators import VISUAL_KINDS, TREATMENTS, STYLE_KEYS, NOT_PRESENT, MAX_BEATS

# Shared caveat, appended to every system prompt: the text evidence below
# comes from automated extraction (OCR/YOLO/scene-detection/etc.) and can be
# wrong — misread text, mislabeled objects, missed cuts. The attached video
# is the actual source. When your own direct read of the video disagrees
# with the evidence text, trust the video and go with what you actually
# see/hear. Use the evidence as a hint for where to look, not as fact.
EVIDENCE_CAVEAT = (
    " The text evidence provided alongside the video (OCR text, object "
    "labels, shot/scene data) was produced by automated tools and can "
    "contain errors. You have the actual video attached — if what you see "
    "or hear in it disagrees with the evidence text, go with what you "
    "directly observe, not the evidence. Treat the evidence as a pointer "
    "to where to look, not as ground truth."
)

# The video, its speech and its on-screen text can be in ANY language (a lot of our corpus is
# Arabic). Source text stays in its original language and is quoted exactly; everything the model
# WRITES is English, because the footage/music search libraries it feeds are searched in English.
LANGUAGE_NOTE = (
    " The transcript and any on-screen text may be in any language (for example Arabic) and are given "
    "in their original language. Write every JSON string value in English, except text you quote from "
    "the source, which must stay in its original language, exactly as written. Never translate or "
    "alter IDs, timestamps or JSON keys."
)

# ---------------------------------------------------------------------------
# OUTPUT SCHEMAS. A model told only "output JSON" invents its own keys (first real run: the Style
# answer was 30 made-up "channel_video_*" fields and the Story answer used a different layout, so the
# judge rejected both). The exact keys are stated in the prompt, for the teacher AND for the student
# (the student must learn the same format).
# ---------------------------------------------------------------------------
_KINDS_HELP = (
    "talking_head = the host/speaker on camera; broll = cutaway footage illustrating the words; "
    "document_or_screenshot = a document, article, web page, social post or screenshot; "
    "graphic_overlay = animated text or graphic over footage; screen_recording = recorded screen/app; map; "
    "chart_or_data; product_closeup = a physical object in close-up (phone, package, prop); "
    "archival_clip = news/archive footage; text_card = full-screen title/text; other"
)
DIRECTOR_SCHEMA = (
    ' Output ONE JSON object with exactly these keys: "chunk_id" (copy it from the input), '
    '"editorial_function" (a short phrase of at most 14 words: what this segment does in the story), '
    f'"treatment" (one of: {", ".join(TREATMENTS)}; the dominant look of the whole chunk, "mixed" if no single kind dominates), '
    '"visual_beats" (a list of 1 to ' + str(MAX_BEATS) + ' objects in time order, each {"start_sec", "end_sec", "kind", "what"}: '
    'start_sec/end_sec are seconds counted from the START of this chunk (0 = first frame, chunk_duration_sec = last); '
    f'"kind" is one of: {", ".join(VISUAL_KINDS)}; "what" is 2 to 14 words saying what is on screen. '
    'List every visual change you see - cutaways, documents, text overlays, props, screens - and merge neighbouring cuts that show the same kind. '
    f'Meaning of the kinds: {_KINDS_HELP}), '
    '"footage_need" = {"query": a visual-search phrase of at most 12 words for the most important NON-host visual to source for this chunk, '
    'or "none" if only the host is on screen, "duration_sec": total seconds of the beats that are not talking_head (0 when the query is "none")}, '
    '"audio_need" = {"query": a music/sfx search phrase of at most 12 words, or "none", "duration_sec": the chunk duration (0 when type is "none"), '
    '"type": "music" or "sfx" or "none"}, '
    '"reasoning" (one to three sentences saying what in the text or evidence supports this). '
    'Rules: describe only what is clearly visible or audible - if you cannot tell, use kind "other"; never invent. '
    'For audio_need, describe what you actually HEAR (mood, instrument or texture, tempo); use "none" when there is only speech. '
    'Queries are short search phrases, not descriptions of the whole clip, and contain no timestamps. '
    'Name a person, brand, app or product only if the name is spoken in the transcript or readable on screen; otherwise say "man", "woman", "app". '
    'Do not add other keys.'
)
FOOTAGE_SCHEMA = (
    ' Output ONE JSON object with exactly these keys: "chunk_id", "decision" ("confirm" or "reject"), '
    '"confirmed_asset_ids" (list; only ids that appear in retrieved_candidates), "rejected_asset_ids" (list), '
    '"better_query" (a search phrase of at most 12 words; only when decision is "reject", otherwise ""), '
    '"reasoning" (one or two sentences). Do not add other keys.'
)
STYLE_SCHEMA = (
    ' Output ONE JSON object with exactly these six keys and no others: "pacing_target", "typography_system", '
    '"color_lut", "graphic_density", "sound_design_intensity", "map_motion_frequency". Each value is a short '
    'English phrase (at most 14 words) describing that aspect of the channel style, based on the frames and '
    'evidence. If the video does not show an aspect at all (for example it has no maps), the value must be exactly '
    '"' + NOT_PRESENT + '" - never invent one. For pacing_target use the measured cuts_per_minute from the evidence. '
    'Do NOT report technical facts such as codec, resolution, bitrate, duration or file name.'
)
STORY_SCHEMA = (
    ' Output ONE JSON object with exactly these keys: "act_1_hook", "act_1_anchor", "act_3_payoff", '
    '"act_3_closing" (each an object) and "act_2_escalation" (a list of 1 to 4 such objects). Every object has '
    '"segment_id" (written exactly like the ids in the transcript, for example "seg_12"), "function" (a short '
    'phrase) and "note" (one sentence). Only use segment ids that appear in the transcript. '
    'A sponsor or advertisement read (the host promoting a product or app, a "link in the description") is NOT a story step: '
    'never use it as the hook, anchor, escalation or payoff, and choose the story beats from the actual argument of the video. '
    'The acts follow the video timeline, so read the timestamps: act_1_hook and act_1_anchor come from roughly the first quarter of the video, '
    'act_2_escalation points are spread through the middle, act_3_payoff comes from the last third and act_3_closing from the last quarter. '
    'Do not add other keys.'
)

DIRECTOR_SYSTEM = (
    "You are a documentary editor. Given a video clip, a script/transcript "
    "segment with timings, the detected shot cuts and the channel's style config, describe what happens "
    "visually in this segment as a short list of visual beats, then decide the editorial "
    "treatment. You do not pick specific footage or music "
    "assets — you don't have access to the asset library. Instead, state "
    "what you need as a search query: footage_need (the main non-host visual, as a "
    "short visual-search phrase) and audio_need "
    "(music or SFX, mood/instrumentation/pacing as a search phrase). "
    "A separate retrieval step will search the "
    "real libraries from your query later — write the query a search "
    "engine could actually use, not a vague mood word. Cite the chunk_id. "
    "Respond with valid JSON only." + LANGUAGE_NOTE + DIRECTOR_SCHEMA + EVIDENCE_CAVEAT
)

FOOTAGE_SYSTEM = (
    "You are a footage researcher. Given a video clip, a segment's text, "
    "and a pool of retrieved candidate assets, confirm which candidates "
    "genuinely match, or reject the pool and specify a better search "
    "query. Only reference candidates actually provided — never invent an "
    "asset. Respond with valid JSON only." + LANGUAGE_NOTE + FOOTAGE_SCHEMA + EVIDENCE_CAVEAT
)

STYLE_SYSTEM = (
    "You are a channel style analyst. Given sampled frames from a video "
    "plus its pacing, typography, and visual-density evidence, output "
    "that channel's Style Profile config. Respond with valid JSON "
    "only." + LANGUAGE_NOTE + STYLE_SCHEMA + EVIDENCE_CAVEAT
)

STORY_ARCHITECT_SYSTEM = (
    "You are a documentary story architect. Given sampled frames from a "
    "video plus its transcript with timestamps, identify its 3-act "
    "narrative structure: hook, anchor points, escalation, and payoff. "
    "Every claim must cite a real timestamp/segment from the evidence. Do "
    "not invent structure not supported by the transcript. Respond with "
    "valid JSON only." + LANGUAGE_NOTE + STORY_SCHEMA + EVIDENCE_CAVEAT
)


# ---------------------------------------------------------------------------
# STUDENT prompts: what gets stored in the SFT training rows.
# The teacher (Qwen3-Omni, Stage C) watches the video. The student LoRAs are
# trained on Qwen3-30B-A3B, a TEXT-ONLY model: at training and at inference it
# only ever sees the evidence text, never the video. So the stored system prompt
# must not mention a video or tell the model to "trust what you see" — that
# would teach the student something false about its own inputs. The user text is
# identical to what the teacher got; only the system prompt differs.
# ---------------------------------------------------------------------------
_STUDENT_LANGUAGE_NOTE = (
    " The transcript and on-screen text may be in any language (for example Arabic) and are given in "
    "their original language. Write every JSON string value in English, except text you quote from the "
    "source, which stays in its original language, exactly as written. Never translate or alter IDs, "
    "timestamps or JSON keys."
)

STUDENT_SYSTEM = {
    "director": (
        "You are a documentary editor. Given a script/transcript segment with timings and the "
        "detected shot cuts, plan what is on screen as a short list of visual beats and decide the "
        "editorial treatment for this segment. You do not pick specific footage or music assets "
        "— you don't have access to the asset library. Instead, state what you need as a search "
        "query: footage_need (the main non-host visual, as a short visual-search phrase) and "
        "audio_need (music or SFX, mood/instrumentation/pacing as a search phrase). A separate "
        "retrieval step will search the real libraries from your query later. Cite the chunk_id. "
        "Respond with valid JSON only."
    ),
    "footage": (
        "You are a footage researcher. Given a segment's text and a pool of retrieved candidate "
        "assets, confirm which candidates genuinely match, or reject the pool and specify a "
        "better search query. Only reference candidates actually provided — never invent an "
        "asset. Respond with valid JSON only."
    ),
    "style": (
        "You are a channel style analyst. Given a video's pacing, typography, and visual-density "
        "evidence, output that channel's Style Profile config. Respond with valid JSON only."
    ),
    "story_architect": (
        "You are a documentary story architect. Given a video's transcript with timestamps, "
        "identify its 3-act narrative structure: hook, anchor points, escalation, and payoff. "
        "Every claim must cite a real timestamp/segment from the transcript. Do not invent "
        "structure not supported by the transcript. Respond with valid JSON only."
    ),
}
_STUDENT_SCHEMA = {"director": DIRECTOR_SCHEMA, "footage": FOOTAGE_SCHEMA, "style": STYLE_SCHEMA, "story_architect": STORY_SCHEMA}
STUDENT_SYSTEM = {k: v + _STUDENT_LANGUAGE_NOTE + _STUDENT_SCHEMA[k] for k, v in STUDENT_SYSTEM.items()}


def _lang_line(code) -> list[str]:
    return [f"spoken_language: {code}"] if code else []


def _rel(x: float, base: float) -> float:
    return round(max(0.0, float(x) - base), 1)


def build_director_prompt(chunk: dict) -> tuple[str, str]:
    """User text for one chunk. All times are RELATIVE to the chunk start (the clip the model sees starts
    at 0 too), so the model can answer in the same clock. The same text is stored in the SFT row."""
    # NOTE: Director does NOT see candidate assets: its only job is to state the editorial NEED as a
    # search query (a separate deterministic retrieval step runs on that later).
    # Evidence lines are included only if that agent actually ran. Missing != zero.
    ev = chunk.get("evidence_available") or {"shots": True, "ocr": True, "objects": True}
    base = float(chunk["start_sec"])
    lines = [
        f"chunk_id: {chunk['chunk_id']}",
        f"chunk_duration_sec: {round(float(chunk['duration_sec']), 1)}",
        *_lang_line(chunk.get("language")),
    ]
    if ev.get("shots", True):
        cuts = [_rel(s["start_sec"], base) for s in chunk.get("shots", [])]
        lines.append(f"shots_in_chunk: {len(cuts)}")
        lines.append(f"shot_cuts_sec: {cuts[:40]}")
    segs = chunk.get("transcript_segments") or []
    if segs:
        lines.append("transcript:")
        for t in segs:
            lines.append(f"  [{_rel(t['start_sec'], base)}-{_rel(t['end_sec'], base)}] \"{t['text']}\"")
    else:
        lines.append("transcript: (no speech in this chunk)")
    if ev.get("ocr", True):
        lines.append(f"ocr_text_regions: {[o.get('text') for o in chunk['ocr_text_regions']]}")
    if ev.get("objects", True):
        lines.append(f"objects_detected: {[o.get('label') for o in chunk['objects']]}")
    return DIRECTOR_SYSTEM, "\n".join(lines)


def build_footage_prompt(chunk: dict) -> tuple[str, str]:
    lang = "".join(l + "\n" for l in _lang_line(chunk.get("language")))
    user = (
        f"chunk_id: {chunk['chunk_id']}\n"
        f"{lang}"
        f"text: \"{chunk['transcript_text']}\"\n"
        f"retrieved_candidates: []  # TODO: wire in Stage 2's retrieval step"
    )
    return FOOTAGE_SYSTEM, user


def build_style_prompt(style_rollup: dict) -> tuple[str, str]:
    dna = style_rollup.get("dna", {})
    ev = style_rollup.get("evidence_available") or {"style_dna": True}
    lines = [f"VIDEO: {style_rollup.get('video_id')}", f"duration_sec: {style_rollup.get('duration_sec')}",
             *_lang_line(style_rollup.get("language"))]
    if ev.get("style_dna", True):
        for key in ("pacing", "motion", "audio"):
            if dna.get(key):
                lines.append(f"{key}: {dna.get(key)}")
    if style_rollup.get("style_tags"):
        lines.append(f"existing_style_tags: {style_rollup.get('style_tags')}")
    lines.append(f"counts: {style_rollup.get('counts')}")
    return STYLE_SYSTEM, "\n".join(lines)


def build_story_architect_prompt(story_input: dict) -> tuple[str, str]:
    total = story_input.get("duration_sec") or 0
    def pct(t):          # how far into the video this line is: code does the arithmetic so the model does not have to
        try:
            return f" ({round(100 * float(t['start_sec']) / float(total))}% into the video)" if float(total) > 0 else ""
        except (TypeError, ValueError, KeyError):
            return ""
    lines = [
        f"seg_{t['id']}{pct(t)}: {t['start_sec']}-{t['end_sec']} | \"{t['text']}\""
        for t in story_input.get("transcript", [])
    ]
    user = (
        f"VIDEO: {story_input.get('video_id')}\n"
        f"DURATION: {story_input.get('duration_sec')}\n"
        + "".join(l + "\n" for l in _lang_line(story_input.get("language"))) +
        f"TRANSCRIPT:\n" + "\n".join(lines)
    )
    return STORY_ARCHITECT_SYSTEM, user
