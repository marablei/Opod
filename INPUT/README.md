# INPUT

One subfolder per video. Each subfolder = that video's file + its matching
P1_A01.jsonl ... P1_A30.jsonl files, together, same folder.

```
INPUT/
  video_001/
    <the video file>.mp4
    P1_A01.jsonl
    P1_A01b.jsonl
    P1_A02.jsonl
    ... through P1_A30.jsonl
  video_002/
    <the video file>.mp4
    P1_A01.jsonl
    ...
```

Running `python orchestrator/run_pipeline.py` with no arguments reads from
here by default and writes everything to `../OUTPUT/`.

With Drive sync on, the same layout lives at `<drive>/INPUT/<video_id>/` and each bundle is
downloaded to the pod only when its turn comes. Nothing is ever deleted (pod or Drive). Required per bundle:
a video file. That is all: no A-files needed (any P1_A*.jsonl present is ignored by default).
See deploy/README_RUNPOD.md.
