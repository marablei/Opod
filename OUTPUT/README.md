# OUTPUT

Everything the pipeline produces lands here. Nothing outside this folder
gets written (besides reading from INPUT/).

```
OUTPUT/
  _batch_status.json          <- live progress, read by the terminal table
  _batch_summary.csv          <- one row per video when the batch finishes
  _sync_errors.log            <- only if a Drive upload/download had trouble
  director_sft.jsonl          <- FINAL, aggregated across all videos (Stage G)
  style_sft.jsonl             <- FINAL, aggregated across all videos (Stage G)
  footage_sft.jsonl           <- FINAL, aggregated across all videos (Stage G)
  story_architect_sft.jsonl   <- FINAL, aggregated across all videos (Stage G)

  video_001/                  <- one folder per video, every stage's raw output kept
    _state.json                <- checkpoint: which stages are done (resume reads this)
    _timing.json                <- how long each stage took, for this video
    _errors.log                 <- any stage failure for this video, with traceback
    _data_quality.json          <- transcript coverage / counts / warnings (written after Stage A)
    00_self_extract.json        <- Stage A: video facts, shot cuts, Whisper transcript (cached; no agent files)
    01_unified_dna.json         <- Stage A
    02_chunks/                  <- Stage B
      segment_chunks.json
      style_rollup.json
      story_input.json
    03_omni_raw/                <- Stage C (fake or live, depending on ORION_OMNI_MODE)
      _cache_live/               <- one file per finished model call: what makes a lost GPU resumable
      director_raw.json
      footage_raw.json
      style_raw.json
      story_architect_raw.json
    04_judge/                   <- Stage D
      director_validated.json
      footage_validated.json
      style_validated.json
      story_architect_validated.json
      rejected_claims.log       <- exactly what got thrown out and why
    05_sft/                     <- Stage E + F (pre_qa.jsonl kept alongside the gated version)
      director_sft.jsonl
      footage_sft.jsonl
      style_sft.jsonl
      story_architect_sft.jsonl
      _qa_rejections.log

  video_002/
    ... same structure
```

Every stage's output is kept on disk, per video — nothing is silently
overwritten or discarded, so any quality problem can be traced back to
exactly which stage introduced it.

With Drive sync on (ORION_DRIVE_REMOTE), this whole tree is mirrored to `<drive>/OUTPUT/`
after every stage. Scratch folders (`_clips/`, `_frames/`) are never uploaded.
