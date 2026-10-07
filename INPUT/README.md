# INPUT

One subfolder per video. Each subfolder holds just the video file. No other files are needed.

```
INPUT/
  video_001/
    <the video file>.mp4      (.mov .mkv .avi .webm also work)
  video_002/
    <the video file>.mp4
```

Any `P1_A*.jsonl` agent files in a folder are ignored.

`python orchestrator/run_pipeline.py` reads from here and writes everything to `../OUTPUT/`.
With Drive sync on, the input Drive folder holds either a subfolder per video or the video files directly (the
video name without extension becomes its id), and each video is downloaded
to the pod only when its turn comes. Nothing is ever deleted (pod or Drive).
See deploy/README_RUNPOD.md. The contents of this folder are git-ignored.
