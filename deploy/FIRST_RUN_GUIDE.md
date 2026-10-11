# Orion — First Run Guide (copy-paste version)

**How to use this guide:** do the steps **in order**. In every step there is a grey box with a command.
**Copy the whole box, paste it into the terminal, press Enter.** You never have to write code.

Each step says: **PASTE** (what to copy) → **YOU SHOULD SEE** (what the screen shows) → **IF NOT** (what to do).

> **Honest status:** everything was tested with fake data only. Nothing has run on a real GPU, the real model, real
> Whisper or real Google Drive yet. This first run is the real test. If something fails, that is normal: use
> **STEP 11 (the report command)** and send the output to Claude.

**Pasting in the black terminal:** use **Ctrl+Shift+V** (or right-click → Paste), then press **Enter**.
When you paste a password/token, **nothing shows on screen**. That is normal.

**Time and cost (estimates, not measured):** install + model download 20–40 min, one video 10–30 min.
A40 costs about $0.60/hour → roughly $1–2 for the whole first run.

---

# PART A — Things to prepare on your PC/phone (before renting anything)

Do all of this first, so the pod doesn't bill you while you search for things.

### A1. One test video in Google Drive
Put **one** 1080p MP4 (5–10 minutes, with clear speech) in your **Input videos** Drive folder.
Only one video should be in that folder for this test. A loose file is fine.

### A2. GitHub token (read-only)
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
- Expiration: **30 days**
- Repository access: **Only select repositories** → **Opod**
- Permissions: **Contents → Read-only** (nothing else)
- Copy the token **once** into a private note (not into chat).

### A3. Google Drive token (on your PC, about 2 minutes)
1. Open **PowerShell**. Paste: `winget install Rclone.Rclone` → Enter. Then **close and reopen PowerShell**.
2. Paste: `rclone authorize "drive"` → Enter. (If it asks for a client ID, just press Enter.)
3. A browser opens. Log in with the Google account that **owns** your three Drive folders. Click **Allow**.
4. PowerShell prints a line like `Paste the following into your remote machine ---> {"access_token":"...",...} <---End paste`.
   **Copy only the `{ ... }` part** (one long line) into a private note.
- This token is a **password to your whole Drive**. Never paste it in chat or screenshots.

### A4. RunPod balance
You need at least $5 in RunPod (yours showed $15).

---

# PART B — Rent the pod on RunPod

1. runpod.io → **Pods** (not Serverless) → **Deploy**.
2. **GPU:** pick **A40 (48 GB)**. (Other 48 GB options: RTX A6000, L40S.) Not 32 GB or smaller. Avoid "MIG".
3. **GPU count:** 1.
4. **Template:** **Runpod Pytorch 2.8.0** (or the newest "Runpod Pytorch").
5. **CUDA:** if there is a filter, choose **12.8 or newer**.
6. **Instance pricing:** **On-Demand** (not Spot).
7. **Container disk:** **100 GB** or more. (It is erased when the pod is stopped. Fine for this test: results go to Drive.)
8. Click **Deploy Pod**. Wait until it says **Running**.

If a card says "out of capacity", pick another 48 GB card or try later.
**Billing starts when it is Running. Don't deploy until you are ready to do PART C.**

---

# PART C — Copy-paste commands (the real work)

### STEP 1 — Open the terminal
Click the pod name → **Connect** → **Start Web Terminal** → **Open Web Terminal**. A black screen opens.

### STEP 2 — Check the graphics card
**PASTE:**
```bash
nvidia-smi
```
**YOU SHOULD SEE:** a table that says **A40** and about **46068MiB** of memory.
**IF NOT** ("command not found"): the pod has no GPU. Terminate it and deploy a GPU pod.

### STEP 3 — Download the code from GitHub
**PASTE:**
```bash
cd /workspace && git clone https://github.com/marablei/Opod.git
```
It asks two questions:
- `Username for 'https://github.com':` → type `marablei` → Enter
- `Password for ...:` → paste your **GitHub token** (nothing shows) → Enter

**YOU SHOULD SEE:** `Cloning into 'Opod'...` and at the end `done.`
**IF NOT** (`Authentication failed`): the token is wrong or expired. Make a new one (A2) and paste STEP 3 again.
If it says `destination path 'Opod' already exists`, that is fine — go on to STEP 4.

### STEP 4 — Copy the settings file
**PASTE:**
```bash
cp /workspace/Opod/deploy/orion.env.example /workspace/orion.env && echo "settings file created"
```
**YOU SHOULD SEE:** `settings file created`

### STEP 5 — Save your Drive token (two small pastes)

**5a. PASTE this one line, press Enter:**
```bash
read -r -s -p "Paste your Drive token, then press Enter: " TOK; echo
```
Now the terminal waits. **Paste your Drive token** (the `{ ... }` line from A3) and press **Enter**.
(Nothing shows while pasting. That is normal.)

**5b. PASTE this whole box, press Enter:**
```bash
TOK="$TOK" python3 - <<'PY'
import os
tok = os.environ["TOK"].strip()
if tok.startswith("'") and tok.endswith("'"):
    tok = tok[1:-1]
if not (tok.startswith("{") and tok.endswith("}") and "access_token" in tok):
    raise SystemExit("That does not look like the token (it should start with { and contain access_token). Nothing was changed. Redo 5a.")
p = "/workspace/orion.env"
lines = open(p, encoding="utf-8").read().splitlines()
out, done = [], False
for l in lines:
    if l.startswith("RCLONE_CONFIG_GDRIVE_TOKEN="):
        out.append("RCLONE_CONFIG_GDRIVE_TOKEN='" + tok + "'")
        done = True
    else:
        out.append(l)
if not done:
    out.append("RCLONE_CONFIG_GDRIVE_TOKEN='" + tok + "'")
open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("Token saved.")
PY
unset TOK
```
**YOU SHOULD SEE:** `Token saved.`
**IF NOT:** it prints "That does not look like the token". Redo **5a** and paste the whole `{...}` line.

### STEP 6 — (Arabic video only) tell it the language
If your test video is **Arabic**, **PASTE:**
```bash
echo "ORION_LANGUAGE=ar" >> /workspace/orion.env && echo "language set to Arabic"
```
If it is **English**, skip this step. (Without it, the language is auto-detected.)

### STEP 7 — Start everything (this is the long part)
**PASTE this whole box:**
```bash
cd /workspace/Opod
nohup bash go.sh --limit 1 > /workspace/go.out 2>&1 &
sleep 5
tail -n 15 /workspace/go.out
```
**YOU SHOULD SEE** (after 5 seconds): lines starting with `[time] == Orion setup | profile=awq48 | model=cyankiwi/Qwen3-Omni...`
and then GPU lines. It is now running in the **background**: even if your browser closes, it keeps going.

**IF YOU SEE an `ERROR:` line right away:**
- `Drive is configured but RCLONE_CONFIG_GDRIVE_TOKEN is empty` → redo STEP 5.
- `nvidia-smi not found` → not a GPU pod (see STEP 2).
- Anything else → go to **STEP 11** and send the report.

### STEP 8 — Watch it work (as often as you like)
**PASTE:**
```bash
tail -n 30 /workspace/go.out
```
Run it every few minutes. Or watch live (press **Ctrl+C** to stop watching — it keeps running):
```bash
tail -f /workspace/go.out
```

**What you will see, in order** (a long quiet stretch during the download is normal):

| # | What is happening | You'll see | Roughly (estimate) |
|---|---|---|---|
| 1 | Checks GPU and disk | `GPUs: 1`, A40 line | seconds |
| 2 | Installs ffmpeg, rclone | `apt installing`, `installing rclone` | 1–3 min |
| 3 | Installs vLLM + Python packages | `installing vllm==0.11.1` | 5–15 min |
| 4 | **Downloads the AI model (~20–30 GB)** | `downloading cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit` | 5–20 min |
| 5 | Loads Whisper (speech-to-text) | `whisper 'large-v3' loaded on cuda` | 1–3 min |
| 6 | Starts the model | `waiting for model to load ... (NNNs)` → `model server is up` | 3–10 min |
| 7 | Smoke test | `✓ multimodal request works` and `✓ 8-image request works` | under 1 min |
| 8 | Setup done | `SETUP COMPLETE` | |
| 9 | Pipeline starts | `checking Drive access + input bundles ...`, `pipeline started in background` | seconds |
| 10 | Your video is processed | see STEP 9 | 10–30 min |

### STEP 9 — Watch the video being processed
After `pipeline started in background`, **PASTE:**
```bash
tail -n 30 /workspace/orion_run.log
```
You will see stage names for your video:

| Stage | What it does | AI? |
|---|---|---|
| `stage_a` | video facts, shot cuts, **Whisper transcript**, language | Whisper only |
| `stage_b` | splits the video into chunks at speech pauses | no |
| `stage_c` | **the model watches each chunk** and answers (slowest) | yes |
| `stage_d` | checks the answers' structure | no |
| `stage_e` | builds the training rows | no |
| `stage_f` | quality gate | no |

Lines starting with `[data-quality]` are warnings worth reading (e.g. empty transcript, unsure language).

**Finished** when STEP 10 shows every stage `done`.

### STEP 10 — Is it finished?
**PASTE:**
```bash
/workspace/venv/bin/python /workspace/Opod/deploy/status.py
```
**YOU SHOULD SEE:** your video with all stages `done` (or `running` while it works).
If the line says `failed`, go to STEP 11.

### STEP 11 — Make a SAFE report to send to Claude
Use this any time something fails, or when it finishes. It hides your tokens automatically.

**PASTE this whole box:**
```bash
cat > /workspace/report.sh <<'EOF'
#!/usr/bin/env bash
W=/workspace
mask() { sed -E 's/ya29\.[A-Za-z0-9_.-]+/[HIDDEN]/g; s/github_pat_[A-Za-z0-9_]+/[HIDDEN]/g; s/ghp_[A-Za-z0-9]+/[HIDDEN]/g; s/1\/\/[A-Za-z0-9_-]{20,}/[HIDDEN]/g; s/"(access_token|refresh_token)": ?"[^"]*"/"\1":"[HIDDEN]"/g; s/hf_[A-Za-z0-9]{20,}/[HIDDEN]/g'; }
echo "===== GPU ====="; nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv 2>&1 | head -3
echo; echo "===== STATUS ====="; [ -x $W/venv/bin/python ] && $W/venv/bin/python $W/Opod/deploy/status.py 2>&1 | head -30 || echo "(not installed yet)"
echo; echo "===== LAST LINES: go.out ====="; tail -n 25 $W/go.out 2>&1 | mask
echo; echo "===== LAST LINES: orion_run.log ====="; tail -n 25 $W/orion_run.log 2>&1 | mask
echo; echo "===== LAST LINES: vllm_server.log ====="; tail -n 10 $W/vllm_server.log 2>&1 | mask
echo; echo "===== DATA QUALITY ====="; cat $W/OUTPUT/*/_data_quality.json 2>/dev/null | mask | head -40
echo; echo "===== ERRORS ====="; cat $W/OUTPUT/*/_errors.log 2>/dev/null | mask | tail -n 30
echo; echo "===== FIRST 2 DIRECTOR ROWS ====="; head -n 2 $W/OUTPUT/director_sft.jsonl 2>/dev/null | cut -c1-1500 | mask
echo; echo "===== DISK ====="; df -h $W | tail -1
EOF
bash /workspace/report.sh
```
**Then:** select everything it printed, copy it, and **paste it to Claude**. After the first time you can just run
`bash /workspace/report.sh` again.

> Before sending, glance over it. It hides tokens, but if you see anything that looks like a password, replace it with `XXX`.

### STEP 12 — Look at the results in Google Drive
Open your Drive folders:
1. **Each stage** → a folder named after your video → `stage_a_extract` … `stage_f_final`, plus `_state.json`,
   `_timing.json`, `_errors.log`, `_data_quality.json`.
2. **Output SFT** → `director_sft.jsonl`, `style_sft.jsonl`, `story_architect_sft.jsonl`, `footage_sft.jsonl`.
   - `footage_sft.jsonl` is **empty on purpose** (those rows are held back until the retrieval step exists).
   - The others should have rows.

Peek at the first rows on the pod:
```bash
head -n 2 /workspace/OUTPUT/director_sft.jsonl
```
Judge: is the Arabic real Arabic letters (not `ا...` codes)? Is the answer valid JSON with sensible English
search phrases? Does the quoted text match what is said in the video? Send the rows to Claude to judge.

### STEP 13 — Stop the pod (so billing ends)
1. Make sure the results are in Drive (STEP 12).
2. RunPod → your pod → **Stop** (or **Terminate** if you won't reuse it).
   - With container disk only, **Stop wipes the disk** (the next start reinstalls: 20–40 min) and your GPU is no
     longer reserved. For a test, **Terminate** is fine. Nothing in Drive is deleted.
3. Check **Billing** in RunPod that nothing is still running.

---

# PART D — If something goes wrong (copy-paste fixes)

**General rule:** run **STEP 11** and send the report to Claude. These are the common cases:

### D1. It stopped or the browser closed — resume
Nothing is lost. **PASTE:**
```bash
cd /workspace/Opod && nohup bash go.sh --limit 1 > /workspace/go.out 2>&1 &
sleep 5; tail -n 15 /workspace/go.out
```
It skips everything already finished.

### D2. Model download is slow / refused by Hugging Face
Make a free token at huggingface.co → Settings → Access Tokens (read). Then **PASTE** (replace `PASTE_HF_TOKEN_HERE`):
```bash
echo "HF_TOKEN=PASTE_HF_TOKEN_HERE" >> /workspace/orion.env
```
Then run D1.

### D3. "Out of memory" when the model starts
**PASTE:**
```bash
printf "ORION_CLIP_HEIGHT=288\nORION_CLIP_FPS=1\n" >> /workspace/orion.env
```
Then run D1.

### D4. Every model call fails with "HTTP 400"
**PASTE:**
```bash
echo "ORION_OMNI_JSON_MODE=0" >> /workspace/orion.env
```
Then run D1.

### D5. Wrong language in the transcript
**PASTE** (change `ar` to the right code, e.g. `en`):
```bash
echo "ORION_LANGUAGE=ar" >> /workspace/orion.env
rm -rf /workspace/OUTPUT/*/stage_a_extract
```
Then run D1. (This redoes the transcript step only on the pod; Drive is not touched.)

### D6. "Drive check failed" or "No videos found"
- Is exactly one MP4 in the **Input videos** Drive folder?
- Was the Drive token made with the Google account that **owns** the folders? If unsure, redo **A3** and **STEP 5**.

### D7. "No space left on device"
Terminate the pod and deploy again with a larger container disk (e.g. 150 GB).

### D8. Whisper says `loaded on cpu`
It still works, only slower. Fine for the test.

---

# PART E — Security reminders

- The **Drive token** and **GitHub token** are passwords. Never paste them in chat, screenshots or videos.
- If a token was ever shown in a chat or screenshot, **revoke it**:
  - GitHub → Settings → Developer settings → Personal access tokens → delete it.
  - Google → myaccount.google.com/permissions → remove rclone access, then run A3 again.
- `/workspace/orion.env` exists only on the pod and is not in git.

---

# PART F — What each thing is (only if you are curious)

| Part | What does the work |
|---|---|
| Video facts, shot cuts | normal code (ffprobe, ffmpeg) |
| Transcript with timestamps | **Whisper `large-v3`** (speech-to-text) |
| Chunking, checks, building rows, quality gate | normal code |
| All the reasoning about the video | **Qwen3-Omni-30B-A3B-Instruct (4-bit)** — the "teacher" |
| Later: training the student | Qwen3-30B-A3B with LoRA (not built yet) |
| Later: the editor (Pipeline 2) | Qwen3-Coder LoRAs (not built yet) |

Where things live on the pod:

| What | Where |
|---|---|
| Python tools + vLLM | `/workspace/venv` |
| Model files + Whisper | `/workspace/hf_cache` |
| Everything `go.sh` printed | `/workspace/go.out` |
| Setup log | `/workspace/orion_setup.log` |
| Model server log | `/workspace/vllm_server.log` |
| Pipeline log | `/workspace/orion_run.log` |
| Your video copied from Drive | `/workspace/INPUT/` |
| Results before they go to Drive | `/workspace/OUTPUT/` |

Later, for all ~115 videos: `cd /workspace/Opod && nohup bash go.sh > /workspace/go.out 2>&1 &`
(no `--limit`) processes every video not yet done. Not built yet: footage/audio retrieval, LoRA training, Pipeline 2.


---

# PART G — Fixes found on the first real run (already in the repo)

Full table: `deploy/KNOWN_ISSUES_FIRST_RUN.md`. A NEW pod cloned from the updated GitHub repo already has them:
transformers pinned below 5, disk check relaxed on re-runs, audio decoded with ffmpeg (no PyAV error), vLLM memory 80%.

**Saving the Drive token (use this instead of STEP 5 if the hidden prompt won't paste):**
1. `cat > /workspace/drive_token.txt` then Enter
2. Paste the token, Enter, then Ctrl+D
3. `clear`, then paste:
```bash
python3 - <<'PY'
tok = open("/workspace/drive_token.txt").read().strip()
assert tok.startswith("{") and tok.endswith("}") and "refresh_token" in tok and "..." not in tok, "bad token"
p = "/workspace/orion.env"
L = [l for l in open(p).read().splitlines() if not l.startswith("RCLONE_CONFIG_GDRIVE_TOKEN=")]
L.append("RCLONE_CONFIG_GDRIVE_TOKEN='" + tok + "'")
open(p, "w").write("\n".join(L) + "\n")
print("Token saved, length", len(tok))
PY
rm -f /workspace/drive_token.txt
```
