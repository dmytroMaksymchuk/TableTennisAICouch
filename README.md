# TT AI Coach (v1)

TT AI Coach is a local, single-user web app for analysing table-tennis technique from a fixed-camera video:

1. Upload a clip (mp4/mkv/mov, optional trim, handedness).
2. Pose estimation runs with **Sports2D**.
3. The backend computes joint angles, segments strokes into phases, classifies forehand/backhand, and evaluates the strokes against rules in an editable YAML file.
4. The results page shows the video with a skeleton overlay, synchronized angle charts, and a clickable stroke list with feedback.

The design and API contract live in [`DESIGN.md`](DESIGN.md). It covers the frame/time rule, job state machine, endpoints, schemas, the Sports2D notes (§8) and the e2e results (§8.1).

> All coaching thresholds in `backend/rules/default.yaml` are **placeholder defaults, not coaching-validated**.

## Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| ffmpeg + ffprobe | 7.1 (`C:\ffmpeg\bin`) | Must be on `PATH`, or set `TTCOACH_FFMPEG_PATH` / `TTCOACH_FFPROBE_PATH` |
| [uv](https://docs.astral.sh/uv/) | 0.12 | Manages Python 3.12 for the backend and the Sports2D tool env |
| Node.js + npm | 22.16 (PowerShell) / 20.15 (Git Bash) | Git Bash may resolve a different Node than PowerShell on this machine; both versions work |
| GPU | optional | Sports2D runs on CPU (OpenVINO) at about 0.11 s/frame at 720p |

## Setup

```powershell
# 1. Sports2D in its own isolated tool environment (never installed into the backend env)
uv tool install "sports2d==0.8.34" --python 3.12

# 2. Backend deps (uv creates backend/.venv on first run; this step is optional)
uv sync --project backend

# 3. Frontend deps
npm --prefix frontend i
```

The backend finds Sports2D automatically at `<uv tool dir>\sports2d\Scripts\python.exe`.

### Optional: `backend/ttcoach.local.toml` (git-ignored)

Every setting can also be set as an environment variable with the `TTCOACH_` prefix, for example `TTCOACH_MAX_DURATION_S=600`.

```toml
# data_dir = "data"                 # relative paths resolve against the repo root
# max_duration_s = 300

[estimator_options.sports2d]
# python = "C:/Users/<you>/AppData/Roaming/uv/tools/sports2d/Scripts/python.exe"  # only if auto-discovery fails
mode = "balanced"        # lightweight | balanced | performance
device = "auto"
timeout_s = 3600
```

## Run

Run these from the repo root, in two terminals:

```powershell
uv run --project backend ttcoach-api      # FastAPI on http://127.0.0.1:8000 (API under /api)
npm --prefix frontend run dev             # Vite on http://localhost:5173, proxies /api to the backend
```

Then open http://localhost:5173.

- Jobs and artifacts are stored under `data/jobs/<job_id>/`.
- Interrupted jobs resume automatically when the backend restarts.
- To check that ffmpeg and Sports2D are found, use `GET /api/health`.

## Tests

| Command | What it covers |
|---|---|
| `uv run --project backend pytest -q -m "not sports2d"` | Backend: config, registry, repository, child-process tree kill, TRC parsing, alignment, kinematics, coaching, rules, API, worker recovery, e2e tool. Uses a stub estimator. |
| `uv run --project backend pytest -m sports2d -rs` | Real Sports2D run on 70–74 s of the sample clip. Must report `1 passed`, 0 skipped. |
| `npm --prefix frontend run typecheck` | TypeScript |
| `npm --prefix frontend test` | Frontend unit tests (Vitest) |
| `npm --prefix frontend run build` | Production bundle |

### End-to-end check against a running backend

```powershell
uv run --project backend ttcoach-e2e --video "Xu Xin Training ｜ Swedish Open 2017 [A5lmr4FoqH8].mkv" `
  --handedness left --trim-start 70 --trim-end 80 --person auto --render-frames first,mid,last --out data/e2e
# after restarting the backend:
uv run --project backend ttcoach-e2e --job <job_id> --check-only
```

The tool uploads the clip, polls the job, and selects the suggested person on `awaiting_selection`. It then validates all four result documents and prints:
- the stroke count,
- the view class,
- the feedback counts,
- the frame drift.

With `--render-frames` it also writes PNGs with the smoothed keypoints drawn on them. It exits 0 on success and 1 otherwise.

E2E result on the sample (70–80 s): both handedness settings reach `done` with frame drift 0 and an `oblique` view, and the overlay lines up with the player's joints. **0 strokes** are detected because the placeholder `min_peak_speed` (4.0 torso_len/s) is above this clip's peak wrist speed (about 2.7). See DESIGN §8.1.

## Architecture (short)

```
browser (Vite/React/TS) ──/api──> FastAPI (ttcoach.api)
                                     │ JobWorker thread + queue (in-process)
                                     ├─ normalizing:     ffmpeg → video/normalized.mp4 (CFR, start 0, ≤720p)
                                     ├─ estimating_pose: PoseEstimator registry → Sports2D child process (own uv tool env,
                                     │                   Windows Job Object tree-kill) → px TRC → align_pose → persons/candidates
                                     │                   (≥2 persons → awaiting_selection → POST /selection)
                                     └─ analyzing:       kinematics (smoothing, angles) + coaching (segmentation, phases,
                                                         FH/BH, metrics, view, YAML rules) → analysis/rev-NNNN/*.json(.gz)
```

- **File-based job store** (`data/jobs/<id>/`). Writes are atomic, each stage leaves a completion marker, and restart recovery is bounded. Every analysis run gets a new revision (`analysis_rev`), so re-analysis after editing the rules never mixes results.
- **Model-agnostic core.** Estimators return sparse canonical 26-joint tracks, and the core aligns them to video frames. Adding an estimator takes one adapter module plus one import line in `ttcoach/pose/__init__.py`.
- **Single frame/time rule** (DESIGN §0), shared by backend and frontend: `f = floor(t·fps + 1e-6)`, seek to `(f + 0.5)/fps`.
- **Extension points** (signatures only) are in `ttcoach/extensions.py`: pro-reference comparison and LLM advice.

Main backend modules (`backend/src/ttcoach/`):

| Module | Role |
|---|---|
| `config.py` | Settings |
| `procs.py` | Child processes |
| `media/ffmpeg.py` | ffmpeg wrapper |
| `pose/` | Schema, registry, Sports2D adapter, TRC parser, alignment, persons |
| `kinematics/`, `coaching/` | Analysis |
| `pipeline/` | Stages and worker |
| `api/` | Routes |
| `tools/e2e.py` | e2e check |
