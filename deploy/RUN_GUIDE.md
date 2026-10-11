# Orion — run guide  ·  copy-paste only

Two parts. **Part 1 is done ONCE.** After that, every run is **Part 2 = one paste**.
Tokens are never typed into a command and never sent in chat.

## Part 1 — one-time setup (about 10 minutes)

**1a. Persistent storage** (so nothing is reinstalled or re-downloaded next time)
RunPod → Storage → **New Network Volume** → size **100 GB** → pick the datacenter where **A40** is available. (Costs a few dollars a month while it exists; RunPod shows the exact price before you confirm.) It keeps the installed software, the model, the code and your settings.

**1b. Secrets** (RunPod → Settings → Secrets → New Secret). Paste each value in the RunPod page only:
- name `drive_token` → the `{ ... }` part printed by `rclone authorize "drive"` on your PC (revoke any old/exposed one first: myaccount.google.com/permissions → rclone → Remove access).
- name `gh_token` → a GitHub token, read-only, repo `Opod` (needed because the repo is Private).

**1c. Template** (RunPod → Templates → New Template)
- Image: the **Runpod Pytorch 2.8.0** image
- Container disk: 30 GB  ·  Volume mount path: `/workspace`
- **Expose HTTP ports: `8000`** (live dashboard)
- Environment variables (name → value):
  - `RCLONE_CONFIG_GDRIVE_TOKEN` → `{{ RUNPOD_SECRET_drive_token }}`
  - `GH_TOKEN` → `{{ RUNPOD_SECRET_gh_token }}`
  - `RCLONE_CONFIG_GDRIVE_TYPE` → `drive`
  - `RCLONE_CONFIG_GDRIVE_SCOPE` → `drive`
- Save.

## Part 2 — every run (one paste)

1. RunPod → Deploy → **A40 48 GB**, **On-Demand**, your template, attach your network volume.
2. Open the pod's web terminal, paste this whole box, Enter:
```bash
cd /workspace && ([ -d Opod ] || git -c "http.extraheader=Authorization: Basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)" clone -q https://github.com/marablei/Opod.git) && nohup bash Opod/go.sh --limit 5 > go.out 2>&1 & sleep 10; tail -n 25 /workspace/go.out
```
That single command: pulls the newest code → creates the settings file → runs the doctor (stops in seconds with the exact fix if something is wrong) → installs what is missing → starts the model → runs the videos → saves everything to Drive.
First time on a new volume: about 30–40 min (install + 20–30 GB model download). **Later runs: about 6–8 min of start-up** (model loading), because everything is already on the volume.

3. Watch: the dashboard address (the `https://…proxy.runpod.net/` line):
```bash
grep -A3 "LIVE DASHBOARD" /workspace/go.out | head -5
```
Page does not open → port 8000 was not exposed; use `python3 /workspace/Opod/deploy/status.py` instead.

4. When the dashboard says **FINISHED**:
```bash
cat /workspace/OUTPUT/_run_report.md
```
(also saved to Drive). Send it to Claude with ~5 chunks per video you checked against the video.

5. **Stop** the pod in RunPod (Stop keeps the volume; the volume is what you keep paying a little for).

Change `--limit 5` to `--limit 20`, or remove it for "everything not done yet".

## If something fails
- `python3 /workspace/Opod/deploy/doctor.py --stage all` → send the output.
- Resume after any stop (nothing lost): paste the same box again.
- Redo only the reasoning stages after a prompt change: use `--redo-from stage_b` (old outputs are archived, never deleted).
- Drive token rejected after weeks: Google may have expired it. Make a new one on your PC and replace the `drive_token` secret in RunPod (Part 1b). Nothing else changes.

## Not verified yet (honest list)
- RunPod passing secrets into the web terminal as environment variables, and the `{{ RUNPOD_SECRET_… }}` syntax: documented by RunPod, but not tested by me. If the doctor says "Drive token is empty", fall back to `cat > /workspace/drive_token.txt` + `python3 /workspace/Opod/deploy/save_drive_token.py`; with a network volume that token is then saved on the volume and survives restarts too.
- Whether the Drive refresh token stays valid for weeks (it should with rclone's own app; if your Google app is in "Testing" mode Google expires it after 7 days).
- The dashboard link through RunPod's proxy.
