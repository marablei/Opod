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

  video_001/                  <- one folder per video
    _state.json                <- bookkeeping: which stages are done (resume reads this)
    _timing.json               <- bookkeeping: how long each stage took
    _errors.log                <- bookkeeping: any stage failure, with traceback
    _data_quality.json         <- bookkeeping: transcript coverage / counts / warnings
    stage_a_extract/           <- Stage A: video -> facts
      self_extract.json          ffprobe info, shot cuts, Whisper transcript (cached)
      unified_dna.json
    stage_b_chunks/            <- Stage B
      segment_chunks.json
      style_rollup.json
      story_input.json
    stage_c_omni/              <- Stage C (Qwen3-Omni answers)
      _cache_live/               one file per finished model call: what makes a lost GPU resumable
      director_raw.json
      footage_raw.json
      style_raw.json
      story_architect_raw.json
    stage_d_judge/             <- Stage D
      director_validated.json
      footage_validated.json
      style_validated.json
      story_architect_validated.json
      rejected_claims.log        exactly what got thrown out and why
    stage_e_sft/               <- Stage E: training rows BEFORE the QA gate (never modified afterwards)
      director_sft.jsonl  footage_sft.jsonl  style_sft.jsonl  story_architect_sft.jsonl
    stage_f_final/             <- Stage F: this video's FINAL rows, after the QA gate. Stage G reads only this.
      director_sft.jsonl  footage_sft.jsonl  style_sft.jsonl  story_architect_sft.jsonl
      _qa_report.json  _qa_rejections.log
  video_002/
    ...
```

Every stage's output is kept on disk, per video — nothing is silently
overwritten or discarded, so any quality problem can be traced back to
exactly which stage introduced it.

With Drive sync on (ORION_DRIVE_REMOTE), this whole tree is mirrored to `<drive>/OUTPUT/`
after every stage. Scratch folders (`_clips/`, `_frames/`) are never uploaded.
