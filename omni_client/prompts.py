"""
Prompt templates for the 4 reasoning passes, matching the SFT dataset
formats already agreed on (system/user/assistant chat shape). Each builder
returns (system_prompt, user_content) — the client wraps these into
whatever request format the target endpoint (vLLM OpenAI-compatible, or a
fake local mock) expects.
"""

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
DIRECTOR_SCHEMA = (
    ' Output ONE JSON object with exactly these keys: "chunk_id" (copy it from the input), '
    '"editorial_function" (a short phrase: what this segment does in the story), '
    '"treatment" (a short snake_case label such as talking_head, direct_broll, graphic_overlay, screen_recording), '
    '"footage_need" = {"query": a visual-search phrase of at most 12 words naming the main thing to show, '
    '"duration_sec": number}, '
    '"audio_need" = {"query": a music/sfx search phrase of at most 12 words, or "none", "duration_sec": number, '
    '"type": "music" or "sfx" or "none"}, '
    '"reasoning" (one or two sentences saying what in the text or evidence supports this). '
    'Queries are short search phrases, not descriptions of the whole clip, and contain no timestamps. '
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
    'English phrase (at most 12 words) describing that aspect of the channel style, based on the frames and '
    'evidence. Do NOT report technical facts such as codec, resolution, bitrate, duration or file name.'
)
STORY_SCHEMA = (
    ' Output ONE JSON object with exactly these keys: "act_1_hook", "act_1_anchor", "act_3_payoff", '
    '"act_3_closing" (each an object) and "act_2_escalation" (a list of 1 to 4 such objects). Every object has '
    '"segment_id" (written exactly like the ids in the transcript, for example "seg_12"), "function" (a short '
    'phrase) and "note" (one sentence). Only use segment ids that appear in the transcript. Do not add other keys.'
)

DIRECTOR_SYSTEM = (
    "You are a documentary editor. Given a video clip, a script/transcript "
    "segment, and the channel's style config, decide the editorial "
    "treatment for this segment. You do not pick specific footage or music "
    "assets — you don't have access to the asset library. Instead, state "
    "what you need as a search query: footage_need (what to show, as a "
    "short visual-search phrase, plus target duration) and audio_need "
    "(music or SFX, mood/instrumentation/pacing as a search phrase, plus "
    "target duration and type). A separate retrieval step will search the "
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
        "You are a documentary editor. Given a script/transcript segment and automatically "
        "extracted evidence about it (shot count, on-screen text, detected objects), decide the "
        "editorial treatment for this segment. You do not pick specific footage or music assets "
        "— you don't have access to the asset library. Instead, state what you need as a search "
        "query: footage_need (what to show, as a short visual-search phrase, plus target "
        "duration) and audio_need (music or SFX, mood/instrumentation/pacing as a search phrase, "
        "plus target duration and type). A separate retrieval step will search the real "
        "libraries from your query later. Cite the chunk_id. Respond with valid JSON only."
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


def build_director_prompt(chunk: dict) -> tuple[str, str]:
    # NOTE: Director does NOT see candidate assets (footage or audio) at
    # all — per the agreed design, Director's only job is to state the
    # editorial NEED as a search query (footage_need/audio_need in its
    # output). Actually searching real libraries for matches, across
    # however many sources (Epidemic Sound + ~20-25 others + local), is a
    # separate deterministic Python retrieval step that runs on Director's
    # output — not built yet ("next part, when we actually use the LoRAs"
    # per your call). Nothing here waits on that; this prompt and the SFT
    # rows it produces are already complete without it.
    # Evidence lines are included only if that agent actually ran. Missing != zero.
    # (Older outputs without the flag keep the old behaviour: everything included.)
    ev = chunk.get("evidence_available") or {"shots": True, "ocr": True, "objects": True}
    lines = [
        f"chunk_id: {chunk['chunk_id']}",
        f"start_sec: {chunk['start_sec']}  end_sec: {chunk['end_sec']}",
        *_lang_line(chunk.get("language")),
        f"text: \"{chunk['transcript_text']}\"",
    ]
    if ev.get("shots", True):
        lines.append(f"shots_in_chunk: {len(chunk['shots'])}")
    if ev.get("ocr", True):
        lines.append(f"ocr_text_regions: {[o.get('text') for o in chunk['ocr_text_regions']]}")
    if ev.get("objects", True):
        lines.append(f"objects_detected: {[o.get('label') for o in chunk['objects']]}")
    user = "\n".join(lines)
    return DIRECTOR_SYSTEM, user


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
    lines = [
        f"seg_{t['id']}: {t['start_sec']}-{t['end_sec']} | \"{t['text']}\""
        for t in story_input.get("transcript", [])
    ]
    user = (
        f"VIDEO: {story_input.get('video_id')}\n"
        f"DURATION: {story_input.get('duration_sec')}\n"
        + "".join(l + "\n" for l in _lang_line(story_input.get("language"))) +
        f"TRANSCRIPT:\n" + "\n".join(lines)
    )
    return STORY_ARCHITECT_SYSTEM, user
