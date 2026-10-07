# Running Orion on RunPod (with Google Drive as the safe copy)

Everything the pipeline produces is pushed to your Drive after **every stage** (and, inside the
long Stage C, every ~90 s). The pod's disk is only scratch. If the GPU dies, rent another one,
run the same two commands, and it resumes from what reached Drive.

## 0. One-time, on your Windows PC (2 minutes)

1. Download rclone (zip, no install needed): https://rclone.org/downloads/ -> unzip.
2. In a terminal inside that folder:  `rclone authorize "drive"`
3. A browser opens; log in with the Google account that owns the Drive. Back in the terminal it
   prints a line `{"access_token":"...","refresh_token":"...",...}` between `--->` and `<---`.
   Copy that JSON. (It is a password to your Drive: don't post it anywhere.)
4. Copy `deploy/orion.env.example` to a file called `orion.env`, paste the JSON into the
   `RCLONE_CONFIG_GDRIVE_TOKEN='...'` line (keep the single quotes).

## 1. Put your videos on Drive

```
My Drive/Orion/INPUT/video_001/   <- just the video file (.mp4/.mov/.mkv/.avi/.webm)
My Drive/Orion/INPUT/video_002/
...
```
(`Orion/OUTPUT/` is created by the pipeline.) One folder per video, any folder names.

**Only the video is required. No A-files.** Any `P1_A*.jsonl` in a folder is ignored. The pipeline reads the video
itself: ffprobe (duration), ffmpeg (shot cuts), Whisper (transcript with timestamps), and the Omni model watches and
listens. (Set `ORION_USE_AGENTS=1` only if you ever want the old agent files used again.)

## 2. Rent the pod

* Template: a RunPod **PyTorch** template (Ubuntu, Python 3.10-3.12, CUDA 12.x).
* GPU: **one 48 GB card** (RTX A6000 / L40S / RTX 6000 Ada) for the default `awq48` profile.
* Disk: set **Volume Disk >= 100 GB** (it is mounted at `/workspace`, where the model (~20 GB), the Python
  environment, your videos and all outputs live). Container Disk can stay at its default (~30 GB).
  Setup stops early with a clear message if `/workspace` is too small. Nothing is deleted from the pod.
  Stopping a pod keeps the Volume Disk; terminating it deletes it (Drive still has your results).
* Official BF16 weights (`MODEL_PROFILE=bf16`) need 80 GB+ and in practice 2x80 GB: far more
  expensive per hour. See "What is NOT proven".

## 3. On the pod: ONE command

Give the pod your Drive settings once (pick one):
* **Best: pod environment variables.** In RunPod, save a Template (or edit the pod) with the variables from
  `orion.env.example`: `ORION_DRIVE_REMOTE`, `RCLONE_CONFIG_GDRIVE_TYPE`, `RCLONE_CONFIG_GDRIVE_SCOPE`,
  `RCLONE_CONFIG_GDRIVE_TOKEN`. Every pod you start from that template already has them.
  Check in the web terminal: `echo $ORION_DRIVE_REMOTE` should print `gdrive:Orion`.
* **Or a file:** paste the same lines into `/workspace/orion.env` (`nano /workspace/orion.env`).
* Do NOT commit the token to git: it is a password to your whole Drive and stays in git history forever,
  even in a private repo.

Then:
```bash
cd /workspace && git clone <your-github-repo-url> Orion_Pipeline
bash Orion_Pipeline/go.sh --limit 1      # install everything + model + server + smoke test + the 1-video test
```
`go.sh` stops in seconds if Drive is not configured. It installs ffmpeg, rclone, vLLM, faster-whisper, downloads
the model (15-30 min the first time), starts the server, runs the smoke test, then starts the pipeline in the
background. It is safe to run again on a new or restarted pod: it skips what is already installed and the
pipeline resumes from Drive. (`deploy/setup_runpod.sh` and `deploy/run_on_pod.sh` are the two halves, if you
ever want them separately.)

When the 1-video test looks right (`watch -n 10 /workspace/venv/bin/python Orion_Pipeline/deploy/status.py`),
run everything that's left:
```bash
bash Orion_Pipeline/go.sh
```
It runs in the background, so closing the terminal doesn't stop it. Follow with `tail -f /workspace/orion_run.log`.

Results appear on Drive under `Orion/OUTPUT/`: the 4 final `*_sft.jsonl`, plus one folder per video
with every stage's output, `_timing.json`, `_data_quality.json` and `_errors.log`.

## 4. If the GPU dies / you stop early

Rent a new pod, repeat step 3 (`setup_runpod.sh`, then `run_on_pod.sh`). It will:
pull `OUTPUT/` from Drive, skip finished videos, resume a half-done video at its first unfinished
stage, and inside Stage C continue from the first model call not yet saved.
`run_on_pod.sh --limit 10` = "do 10 more videos today".

## 5. Safety nets built in

* `drive_check.py` runs before the batch: Drive reachable, and every INPUT bundle has a video file.
  Bundles without one are skipped, not run.
* 3 videos failing in a row stops the batch (model server down?) instead of burning GPU time.
* Drive sync failures are retried and logged to `OUTPUT/_sync_errors.log`; they never crash a run.
* `_data_quality.json` flags transcripts that cover little of the video (e.g. ending at 600 s of a 1208 s video).

## What is NOT proven (be honest with yourself before the first paid run)

* **None of the pod scripts have been run on a real GPU.** `setup_runpod.sh`, the vLLM install and
  the AWQ model load are written from the model card / vLLM docs / a user report, not tested here.
  The smoke test at the end of setup exists precisely to catch this cheaply.
* The AWQ model is a **community 4-bit quantisation**, not Qwen's official weights.
* Whether a 75 s chunk (video + audio) fits in the 32k context at 360p/2 fps is an estimate.
  If the smoke/1-video test OOMs: `ORION_CLIP_HEIGHT=288 ORION_CLIP_FPS=1` in `orion.env`.
* The real rclone binary was not available where this was built; the Drive logic is tested with a
  folder backend and an rclone stand-in, not against Google. `drive_check.py` is your first real test.
* The 10-30 min/video and the $/100-video figures remain estimates until the 1-video run.
