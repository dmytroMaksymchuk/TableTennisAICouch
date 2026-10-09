# DESIGN.md — TT AI Coach v1 API & Data Contract

Status: draft, iteration 3 (2026-10-04). Copied to repo-root `DESIGN.md` in plan Step 0; Step 0 also
fills §8 (Sports2D spike results) in the repo copy.
Spec: `.omc/specs/deep-interview-tt-ai-coach.md`. Plan: `.omc/plans/tt-ai-coach-v1.md`.

All JSON uses `snake_case`. Every persisted JSON document carries `schema_version` (int, starts at 1).

---
 
## 0. Frame / time rule (single source of truth)

The **normalized video** is constant-frame-rate with `start_time == 0` (enforced in §5 / normalize).
Frame `f` (0-based) has presentation time `f / fps` and is on screen during `[f/fps, (f+1)/fps)`.

| Conversion | Formula | Where |
|---|---|---|
| time → frame | `f = clamp(floor(t * fps + 1e-6), 0, n_frames - 1)` | frontend `utils/time.ts::timeToFrame`, backend `ttcoach/timebase.py::time_to_frame` |
| frame → seek time | `t_seek = (f + 0.5) / fps` (middle of the frame interval; avoids landing on the boundary and showing f−1) | frontend `utils/time.ts::frameToSeekTime` |
| frame → reported time | `t = f / fps` (all `*_s` fields in JSON, chart x values) | backend |

Every frame index in every artifact (pose, angles, strokes, phases, candidates) is an index into the
normalized video. Every seek the frontend performs (chart click, stroke click, person preview) goes
through `timeToFrame` then `frameToSeekTime`. Overlay drawing uses `requestVideoFrameCallback`
`metadata.mediaTime` → `timeToFrame`.

---

## 1. Processes and ports

| Process | Command (from repo root) | Port |
|---|---|---|
| Backend (FastAPI/uvicorn) | `uv run --project backend ttcoach-api` (= `uvicorn ttcoach.api.app:create_app --factory --host 127.0.0.1 --port 8000`, no `--reload`) | 8000 |
| Frontend (Vite dev) | `npm --prefix frontend run dev` | 5173, proxies `/api` → `http://127.0.0.1:8000` |
| Sports2D | separate uv tool environment (`uv tool install sports2d==<pinned> --python 3.12`), launched as a child process by the adapter | — |

No CORS needed in dev (Vite proxy). All endpoints are under `/api`.

---

## 2. Job state machine

```
POST /api/jobs ──(sync ffprobe ok, duration ok)──> queued
                                                     │ worker picks up (resume_from)
                                                     v
                                running / normalizing ──fail──> failed(ffmpeg_failed)
                                                     v
                                running / estimating_pose ──fail──> failed(pose_failed)
                                                     │       └─0 candidates─> failed(no_person_detected)
                                     ┌── 1 candidate (auto) ──┤
                                     │                        └─ ≥2 candidates ─> awaiting_selection
                                     v                                                │ POST /selection
                                running / analyzing  <──── queued(resume_from=analyzing) <┘
                                     │ ──fail──> failed(analysis_failed)
                                     v
                                    done ── POST /selection | POST /reanalyze ──> queued(resume_from=analyzing)

failed ── POST /retry ──> queued(resume_from = first stage whose artifacts are incomplete)
failed(stage=analyzing) ── POST /reanalyze ──> queued(resume_from=analyzing)
```

Enums:
- `status`: `queued | running | awaiting_selection | done | failed`
- `stage`: `normalizing | estimating_pose | analyzing` (meaningful when `running`; last value kept otherwise)
- `resume_from` (in job.json, also exposed read-only): `normalizing | estimating_pose | analyzing`

### 2.1 Stage completion markers (used by restart recovery and /retry)
| Stage | Complete when (all exist and parse) |
|---|---|
| normalizing | `video/normalized.mp4` and `video/probe.json` (probe written last, atomically) |
| estimating_pose | `pose/persons.json` and `pose/candidates.json` (candidates written last) |
| analyzing | `analysis/rev-{analysis_rev:04d}/report.json` for the `analysis_rev` recorded in job.json |

`first_incomplete_stage(job)` = first stage in order whose marker is missing (`analyzing` if pose is complete).

### 2.2 Restart recovery (worker startup, before accepting new work)
| Status found in job.json | Action |
|---|---|
| `queued` | re-enqueue in `created_at` order (unchanged `resume_from`) |
| `running` | `recovery_attempts += 1`; if `recovery_attempts <= 2` → `status=queued`, `resume_from=first_incomplete_stage(job)`, re-enqueue; else → `failed(code="interrupted", stage=<stage>)` |
| `awaiting_selection` | untouched (waits for `POST /selection`) |
| `done`, `failed` | untouched |

Before (re-)running `estimating_pose`, the worker deletes `pose/<estimator>/` and the
`pose/*.json` files so a half-written previous run can never be parsed. If that deletion fails
(e.g. a Windows sharing lock held by an orphaned process or an open viewer), the job goes to
`failed(code="pose_failed", stage="estimating_pose", message="cannot clear pose work dir <path>: <OSError>; close programs using it and POST /retry")`;
the exception never escapes the worker loop.

### 2.3 Child-process lifecycle
All external processes (ffmpeg, ffprobe for long ops, Sports2D) are started through
`ttcoach/procs.py::run_child(cmd, timeout_s, on_line)`:
- launched with an **explicit executable path** (ffmpeg path from Settings; for Sports2D the tool
  environment's `Scripts\python.exe` + entry point), `stdin=DEVNULL`. Note: on Windows a uv venv's
  `Scripts\python.exe` is itself a **launcher that spawns the base interpreter** as a child, so the
  process we start is never the only process — tree containment below is mandatory, not optional;
- **Windows: every child runs inside a Job Object** created via `ctypes` (kernel32; no new dependency):
  `CreateJobObjectW` → `SetInformationJobObject(JobObjectExtendedLimitInformation,
  LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)` → `Popen(..., creationflags=CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP)`
  → `AssignProcessToJobObject(job, proc._handle)` → resume (`ntdll.NtResumeProcess(proc._handle)`), so the
  launcher cannot spawn the base interpreter before it is in the job; descendants inherit membership.
  Kill = `TerminateJobObject(job, 1)`; `CloseHandle(job)` after the child exits. Because of
  KILL_ON_JOB_CLOSE, if the backend process itself dies (crash, console closed) the OS closes the handle
  and kills the whole tree. If job assignment fails (`ERROR_ACCESS_DENIED`), log a warning and fall back
  to `taskkill /PID <pid> /T /F` on kill;
- **POSIX:** `start_new_session=True` (own process group); kill = `os.killpg(pgid, SIGKILL)`;
- stdout/stderr are read by a reader thread that splits on **both `\r` and `\n`** (tqdm redraws with `\r`)
  and calls `on_line(line)` per fragment; the last 50 lines are kept for error messages;
- on timeout or on lifespan shutdown the **whole tree** is killed by the mechanism above;
- the worker tracks the current child; FastAPI lifespan shutdown sets a stop event, kills the
  child tree, joins the worker thread (timeout 10 s). The interrupted job stays `running` on disk and
  is handled by §2.2 at next start.

### 2.4 Analysis revisions
Each `analyzing` run:
1. Deletes stale `analysis/rev-*.tmp/` directories (leftovers of an interrupted run).
2. Allocates `N = max(job.analysis_rev, highest NNNN among existing analysis/rev-NNNN/ dirs) + 1`
   — a crash between the directory rename and the job.json update (rev-N on disk, job.json still N−1)
   therefore never reuses N.
3. Writes all four artifacts to `analysis/rev-NNNN.tmp/`, each as `name.json` **and** a precompressed
   `name.json.gz` (gzip level 6), then `os.replace` → `analysis/rev-NNNN/`.
4. Updates job.json (`analysis_rev = N`, `person_ids`, `ruleset_version`) atomically.
5. Prunes: keeps the two newest revisions; deletion of older `rev-*` dirs is **best-effort** — errors
   (e.g. Windows sharing violation while a `FileResponse` is streaming a file from it) are logged and
   ignored; pruning is retried on the next analyzing run.
Readers always resolve artifacts through the job's `analysis_rev` (or an explicit `?rev=`), so a reader
can never mix revisions. A `rev-N` dir newer than job.json's `analysis_rev` is ignored by readers.

---

## 3. REST endpoints

Error body for all 4xx/5xx: `{ "error": { "code": "job_not_found", "message": "..." } }`.

### 3.1 `POST /api/jobs` — upload video, create job
`multipart/form-data`:

| field | type | required | notes |
|---|---|---|---|
| `file` | file | yes | extensions `.mp4 .mkv .mov` (case-insensitive) |
| `handedness` | `"right" \| "left"` | yes | |
| `trim_start_s` | float >= 0 | no | |
| `trim_end_s` | float > trim_start_s | no | omitted = to end |

Processing: stream to `input/original.<ext>` in 1 MiB chunks → **synchronous ffprobe** of the
original → validate → write `input/probe.json` → write `job.json` **last** (`queued`, `resume_from=normalizing`).
A directory without `job.json` is invisible to listing and restart recovery; any error removes it.
Response `201`: `Job`. Errors (job dir removed on any of them):
- `413 upload_too_large` (more than `max_upload_bytes`, default 2 GiB: rejected up front when `Content-Length`
  exceeds the limit + 1 MiB multipart overhead, and enforced again by counting bytes while copying the upload)
- `415 unsupported_media_type` (extension) / `415 unreadable_media` (ffprobe fails, no video stream, or the
  duration cannot be determined from format/stream duration or frame count ÷ fps)
- `422 validation_error` (field errors, `trim_end_s <= trim_start_s`, `trim_start_s >= duration`)
- `422 video_too_long` (effective duration `min(trim_end_s, duration) - trim_start_s` > `max_duration_s`, default 300)

### 3.2 `GET /api/jobs/{job_id}` — status (polled every 1 s while `queued|running`)
```json
{
  "schema_version": 1,
  "id": "20261004T221500-3f9a1c",
  "status": "running",
  "stage": "estimating_pose",
  "resume_from": "estimating_pose",
  "progress": 0.42,
  "created_at": "2026-10-04T22:15:00Z",
  "updated_at": "2026-10-04T22:15:42Z",
  "settings": {
    "handedness": "right",
    "trim_start_s": 30.0,
    "trim_end_s": 40.0,
    "estimator": "sports2d"
  },
  "original_filename": "clip.mkv",
  "video": { "fps": 50.0, "n_frames": 500, "width": 1280, "height": 720, "duration_s": 10.0 },
  "analysis_rev": 0,
  "person_ids": null,
  "ruleset_version": null,
  "recovery_attempts": 0,
  "error": null
}
```
- `video`: null until normalizing completes.
- `analysis_rev`: 0 = no analysis yet; increments per analyzing run (§2.4). `person_ids`, `ruleset_version` describe the current rev.
- `progress`: 0..1 or null. normalizing: ffmpeg `-progress pipe:1` `out_time_us / duration`; estimating_pose: last `N/M` match in Sports2D stderr (tqdm), null if not parseable; analyzing: null.
- `error` (when failed): `{ "code": "...", "stage": "...", "message": "..." }`. Codes:
  `ffmpeg_failed | pose_failed | no_person_detected | analysis_failed | interrupted | internal`.
- 404 `job_not_found`.

### 3.3 `GET /api/jobs` — list jobs (newest first): `{ "jobs": [Job, ...] }` (AC3).

### 3.4 `GET /api/jobs/{job_id}/candidates`
Available once `estimating_pose` is complete.
```json
{
  "schema_version": 1,
  "candidates": [
    { "person_id": "p00", "coverage": 0.93, "mean_confidence": 0.81,
      "preview_frame": 120, "bbox_at_preview": [412.0, 180.5, 640.2, 702.0] }
  ],
  "suggested_person_ids": ["p00"]
}
```
`bbox` = `[x_min, y_min, x_max, y_max]` in normalized-video pixels.

### 3.5 `GET /api/jobs/{job_id}/frames/{n}.jpg` — single-frame thumbnail
JPEG of normalized-video frame `n` (full resolution, quality 85), extracted with
`ffmpeg -i normalized.mp4 -vf "select=eq(n\,{n})" -vframes 1` and cached at `video/frames/{n:06d}.jpg`.
Used by person selection (draw bboxes over the still) and by e2e frame rendering. 404 if `n` out of range.

### 3.6 `POST /api/jobs/{job_id}/selection`
Body `{ "person_ids": ["p00", "p03"] }` — several ids = same player split across tracker ids, merged
per frame in list order. `202` Job (`queued`, `resume_from=analyzing`).
`409 invalid_state` unless `awaiting_selection | done`; `422 unknown_person_id`.

### 3.7 `POST /api/jobs/{job_id}/reanalyze`
No body. Re-runs `analyzing` with the **current** rules file and current selection (AC8).
Allowed when status is `done`, or `failed` with `error.stage == "analyzing"`. → `202` Job
(`queued`, `resume_from=analyzing`).
`409 pose_unavailable` when estimating_pose is not complete (job failed earlier — use `/retry`);
`409 invalid_state` when `queued | running | awaiting_selection`.

### 3.8 `POST /api/jobs/{job_id}/retry`
No body. Allowed only for `failed`. Clears `error`, sets `resume_from = first_incomplete_stage(job)`,
resets `recovery_attempts = 0`, status `queued`. `202` Job; `409 invalid_state` otherwise.

### 3.9 `GET /api/jobs/{job_id}/video` — normalized mp4
`video/mp4`, `Accept-Ranges: bytes`, `Range: bytes=` → 206. Starlette `FileResponse` (Range support
version verified in Step 0; fallback: ranged `StreamingResponse`). Never gzip-encoded. 404 until normalizing complete.

### 3.10 `GET /api/jobs/{job_id}/results/{artifact}[?rev=N]`
`artifact` ∈ `keypoints | angles | strokes | report`. Default `rev` = job's current `analysis_rev`.
Response header `X-Analysis-Rev: N`; body field `analysis_rev: N`. When the request has
`Accept-Encoding: gzip`, the route serves the **precompressed** `name.json.gz` written at rev-write time
(§2.4) as a `FileResponse` with `Content-Type: application/json`, `Content-Encoding: gzip`,
`Vary: Accept-Encoding`; otherwise it serves `name.json`. No compression at request time. A global
`GZipMiddleware` is **not** used because it would also encode the video route and break Range responses.
404 `artifact_not_ready` (rev 0 or missing), 404 `rev_not_found` (pruned/unknown rev).

### 3.11 `GET /api/rules` → `{ "path": "<abs path>", "ruleset": RuleSet }` (read-only; edit the YAML file).

### 3.12 `GET /api/health`
`{ "status": "ok", "ffmpeg": true, "estimators": {"sports2d": {"available": true, "detail": "sports2d 0.x.y at C:\\...\\python.exe"}}, "default_estimator": "sports2d" }`.

---

## 4. Data schemas

### 4.1 Canonical joint set (`CANONICAL_JOINTS`, order fixed; Halpe-26 semantics)
| idx | name | idx | name |
|---|---|---|---|
| 0 | nose | 13 | l_knee |
| 1 | l_eye | 14 | r_knee |
| 2 | r_eye | 15 | l_ankle |
| 3 | l_ear | 16 | r_ankle |
| 4 | r_ear | 17 | head |
| 5 | l_shoulder | 18 | neck |
| 6 | r_shoulder | 19 | mid_hip |
| 7 | l_elbow | 20 | l_big_toe |
| 8 | r_elbow | 21 | r_big_toe |
| 9 | l_wrist | 22 | l_small_toe |
| 10 | r_wrist | 23 | r_small_toe |
| 11 | l_hip | 24 | l_heel |
| 12 | r_hip | 25 | r_heel |

Absent joints = null. `complete_derived()` synthesises `neck` (mid-shoulders) and `mid_hip` (mid-hips)
when absent. Required for analysis: shoulders, elbows, wrists, hips, knees, ankles.

`SKELETON_EDGES`: `head-neck, neck-l_shoulder, neck-r_shoulder, l_shoulder-l_elbow, l_elbow-l_wrist,
r_shoulder-r_elbow, r_elbow-r_wrist, neck-mid_hip, mid_hip-l_hip, mid_hip-r_hip, l_hip-l_knee,
l_knee-l_ankle, r_hip-r_knee, r_knee-r_ankle, l_ankle-l_heel, l_ankle-l_big_toe, l_big_toe-l_small_toe,
r_ankle-r_heel, r_ankle-r_big_toe, r_big_toe-r_small_toe`.

Coordinates: **pixels of the normalized video**, origin top-left, x right, y down.

### 4.2 Raw estimator output vs aligned `PoseSequence`
Estimators return **sparse, frame-indexed** tracks; core aligns them (`ttcoach/pose/align.py`).
```python
@dataclass(frozen=True)
class RawTrack:
    person_id: str              # "p00", ...
    frame_idx: np.ndarray       # int64 [k], indices into the normalized video, derived by the adapter
                                # from the source's own frame/time columns (never from row order)
    xy: np.ndarray              # float32 [k, n_joints, 2], NaN = missing
    conf: np.ndarray            # float32 [k, n_joints]

@dataclass(frozen=True)
class RawPose:
    estimator: str; estimator_version: str
    joints: tuple[str, ...]                 # == CANONICAL_JOINTS
    tracks: tuple[RawTrack, ...]
    source_n_frames: int                    # frames the estimator reports having processed
    confidence_kind: Literal["score", "presence"]

@dataclass(frozen=True)
class PersonTrack:
    person_id: str
    xy: np.ndarray                          # float32 [n_frames, n_joints, 2]
    conf: np.ndarray                        # float32 [n_frames, n_joints]

@dataclass(frozen=True)
class PoseSequence:
    estimator: str; estimator_version: str
    fps: float; n_frames: int; width: int; height: int      # == VideoMeta
    joints: tuple[str, ...]
    persons: tuple[PersonTrack, ...]
    confidence_kind: Literal["score", "presence"]
    frame_drift: int                                        # see 4.2.1
```

#### 4.2.1 Alignment and drift policy (`align_pose(raw, meta) -> PoseSequence`)
- `drift = |raw.source_n_frames − meta.n_frames|`; also any `frame_idx` outside `[0, n_frames)` counts
  toward drift (`max(drift, max_idx − (n_frames − 1))`).
- `drift <= 2` → rows placed at their `frame_idx`, indices `>= n_frames` dropped, missing frames NaN. Silent: `frame_drift = 0`, no log.
- `2 < drift <= max(5, 0.01 * n_frames)` → same placement; `frame_drift = drift` recorded in
  `report.quality.frame_drift`; WARNING written to `logs/pipeline.log`.
- `drift > max(5, 0.01 * n_frames)` → raise `PipelineError(code="pose_failed", message="frame drift {drift} > {limit}")`.
- Duplicate `frame_idx` within a track → last row wins, logged.

### 4.3 `pose/persons.json` (all persons, aligned)
```json
{
  "schema_version": 1,
  "estimator": "sports2d", "estimator_version": "<pinned>",
  "fps": 50.0, "n_frames": 500, "width": 1280, "height": 720,
  "confidence_kind": "presence", "frame_drift": 0,
  "joints": ["nose", "l_eye", "..."],
  "persons": [
    { "person_id": "p00",
      "x":    [[612.3, 615.0, null], ["... one array of n_frames per joint (26 arrays)"]],
      "y":    [["..."]],
      "conf": [["..."]] }
  ]
}
```
`x[j][f]` = joint j, frame f. Floats rounded to 0.1 px / 0.01.

### 4.4 `results/keypoints` — selected player, for overlay
```json
{
  "schema_version": 1, "analysis_rev": 3,
  "fps": 50.0, "n_frames": 500, "width": 1280, "height": 720,
  "person_ids": ["p00"],
  "joints": ["..."], "edges": [["head", "neck"], "..."],
  "x": [["..."]], "y": [["..."]], "conf": [["..."]],
  "x_smooth": [["..."]], "y_smooth": [["..."]]
}
```

### 4.5 `results/angles`
```json
{
  "schema_version": 1, "analysis_rev": 3,
  "fps": 50.0, "n_frames": 500,
  "t": [0.0, 0.02, "... f / fps"],
  "series": [
    { "name": "r_elbow", "label": "Right elbow flexion", "unit": "deg",
      "definition": "interior angle r_shoulder-r_elbow-r_wrist; 180 = straight",
      "values": [154.2, null], "velocity": [12.0, null], "velocity_unit": "deg/s" }
  ]
}
```
Series (v1): `l_elbow r_elbow l_shoulder r_shoulder l_hip r_hip l_knee r_knee trunk_lean shoulder_hip_separation`.

### 4.6 `results/strokes`
```json
{
  "schema_version": 1, "analysis_rev": 3,
  "ruleset_id": "default", "ruleset_version": "2026.10.0",
  "handedness": "right",
  "strokes": [
    {
      "stroke_id": "s001",
      "type": "FH", "type_confidence": 0.72,
      "view_class": "oblique",
      "start_frame": 102, "contact_frame": 131, "end_frame": 150,
      "start_s": 2.04, "contact_s": 2.62, "end_s": 3.0,
      "phases": [
        { "name": "backswing",      "start_frame": 102, "end_frame": 129 },
        { "name": "contact",        "start_frame": 130, "end_frame": 132 },
        { "name": "follow_through", "start_frame": 133, "end_frame": 150 }
      ],
      "metrics": {
        "elbow_angle_at_contact": { "value": 128.4, "unit": "deg" },
        "backswing_duration_s":   { "value": 0.54,  "unit": "s" },
        "foot_displacement_max":  { "value": 0.21,  "unit": "torso_len" }
      },
      "feedback": [
        {
          "rule_id": "fh.contact.elbow_extension",
          "metric": "elbow_angle_at_contact",
          "value": 168.0,
          "threshold": { "op": "between", "min": 90.0, "max": 150.0 },
          "severity": "warning",
          "message": "Elbow angle at contact is 168°; aim for 90–150° (slight bend).",
          "phase": "contact"
        }
      ]
    }
  ]
}
```
`*_s = frame / fps`. `type` ∈ `FH | BH`; `view_class` ∈ `side | oblique | front_or_back | unknown`
(computed over the stroke window); `phase.name` ∈ `backswing | contact | follow_through`;
`severity` ∈ `info | warning | critical`; `threshold.op` ∈ `lt | lte | gt | gte | between | outside`
(`lt/lte` use `max`, `gt/gte` use `min`). The rule states the desired range; feedback is emitted on
violation. A null metric → no feedback; listed in `report.skipped_rules`. `strokes` may be empty.

### 4.7 RuleSet (`backend/rules/default.yaml`; JSON form served by `/api/rules`)
Top-level: `schema_version, ruleset_id, ruleset_version, description, segmentation, classification, view, rules[]`.
Rule: `{ id, applies_to: [FH|BH], phase?, metric, op, min?, max?, severity, message }`;
`message` is a `str.format` template with `{value}`, `{min}`, `{max}`, `{unit}`. Full example in plan §4.4.

### 4.8 `results/report` — results page entry document
```json
{
  "schema_version": 1, "analysis_rev": 3,
  "job_id": "20261004T221500-3f9a1c",
  "generated_at": "2026-10-04T22:17:01Z",
  "handedness": "right",
  "estimator": { "name": "sports2d", "version": "<pinned>", "confidence_kind": "presence" },
  "ruleset": { "id": "default", "version": "2026.10.0", "path": "backend/rules/default.yaml" },
  "video": { "fps": 50.0, "n_frames": 500, "width": 1280, "height": 720, "duration_s": 10.0 },
  "person_ids": ["p00"],
  "view": {
    "class": "oblique", "side_on": false, "shoulder_width_ratio": 0.48,
    "notice": "Camera is not side-on: joint angles measured in 2D may be distorted. Treat angle feedback as approximate."
  },
  "quality": { "valid_frame_ratio": 0.94, "torso_len_px": 182.0, "frame_drift": 0 },
  "summary": { "n_strokes": 7, "n_fh": 5, "n_bh": 2,
               "feedback_counts": { "info": 1, "warning": 4, "critical": 0 } },
  "skipped_rules": [ { "stroke_id": "s003", "rule_id": "...", "reason": "metric_unavailable" } ]
}
```
`view.notice` is null when `side_on`. `summary` is always present (zeros when no strokes).

---

## 5. On-disk layout

Paths are anchored in `ttcoach/config.py` to `REPO_ROOT` computed from the package location
(`Path(__file__).resolve().parents[3]` for `backend/src/ttcoach/config.py`), never to the cwd.
Defaults: `data_dir = REPO_ROOT/data`, `rules_path = REPO_ROOT/backend/rules/default.yaml`.
The backend is always installed editable (uv project default), so `__file__` is in the source tree.

```
data/                                (git-ignored)
  jobs/
    {job_id}/                        job_id = UTC "YYYYMMDDTHHMMSS-" + 6 hex
      job.json                       Job (§3.2) + internal: selection source, attempt history
      input/original.{mp4|mkv|mov}
      input/probe.json               sync ffprobe of the upload
      video/normalized.mp4           H.264 yuv420p, CFR, start_time 0, faststart, <=720p, AAC or no audio
      video/probe.json               ffprobe -count_frames of normalized.mp4 (completion marker)
      video/frames/{n:06d}.jpg       cached thumbnails (§3.5)
      pose/sports2d/                 estimator-private work dir (cleared before each pose run)
      pose/persons.json              §4.3
      pose/candidates.json           §3.4 payload (completion marker)
      pose/selection.json            { "person_ids": [...], "selected_by": "auto|user" }
      analysis/rev-0003/             keypoints.json, angles.json, strokes.json, report.json
      analysis/rev-0002/             previous revision (kept)
      logs/pipeline.log              stage timings, warnings, child stderr tails
```
All JSON writes: `name.tmp` + `os.replace`. job.json is mutated only via
`FileJobRepository.update()` under a per-job `threading.Lock`.

### 5.1 Normalize command (frame-accurate trim, CFR, zero start)
```
ffmpeg -y -hide_banner -progress pipe:1 -nostats
  [-ss {trim_start_s}] [-to {trim_end_s}] -i input/original.ext       # input seeking + re-encode = frame-accurate
  -map 0:v:0 -map 0:a:0?
  -vf "scale=-2:'min(720,ih)'"
  -r {R} -fps_mode cfr                                                  # R = min(60, round(avg_frame_rate of input))
  -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p
  -c:a aac -b:a 128k
  -movflags +faststart video/normalized.mp4
```
Note (Step 0 finding, ffmpeg 7.1): `-avoid_negative_ts make_zero` (in the draft) makes the mp4 video
`start_time` 0.04 s (B-frame reorder delay pushed into PTS; with AAC audio the priming delay shifts video by
~0.021 s instead), which violates the post-condition. Without it, ffmpeg's default (`auto`) writes an mp4 edit
list and both streams start at exactly 0, so the flag is omitted. The post-condition still guards the invariant.

Post-condition (checked, else `failed(ffmpeg_failed)`): video stream `start_time` within 1e-3 s of 0,
`avg_frame_rate == R`, `nb_read_frames` ≥ 1; `n_frames = nb_read_frames`, `fps = R`.

---

## 6. Python interfaces

### 6.1 PoseEstimator Protocol + registry (`ttcoach/pose/base.py`, `ttcoach/pose/registry.py`)
```python
@dataclass(frozen=True)
class VideoMeta:
    path: Path; fps: float; n_frames: int; width: int; height: int; duration_s: float

@dataclass(frozen=True)
class PoseRunContext:
    work_dir: Path                          # estimator-private, empty at call time
    log: Callable[[str], None]              # appends to logs/pipeline.log
    progress: Callable[[float | None], None]
    run_child: RunChild                     # ttcoach.procs.run_child bound to the worker (tree-kill aware)

class PoseEstimator(Protocol):
    name: str
    def version(self) -> str: ...
    def check_available(self) -> None: ...  # raises EstimatorUnavailable(detail) — e.g. CLI/python not resolvable
    def estimate(self, video: VideoMeta, ctx: PoseRunContext) -> RawPose: ...

EstimatorFactory = Callable[[Mapping[str, Any]], PoseEstimator]   # receives estimator_options[name]

def register_estimator(name: str) -> Callable[[EstimatorFactory], EstimatorFactory]: ...
def get_estimator(name: str, options: Mapping[str, Any]) -> PoseEstimator: ...  # UnknownEstimator
def available_estimators() -> list[str]: ...
```
Core (`pipeline/stages.py::run_pose`) calls `estimate()`, then `align_pose(raw, meta)` (§4.2.1),
then `persons.candidates()`. Adapters never pad, truncate or resample.
Registration: adapter module decorates its factory with `@register_estimator("sports2d")`;
`ttcoach/pose/__init__.py` imports adapter modules — the only line touched besides the adapter
when adding an estimator. Tests register a `StubEstimator` the same way (AC4).

### 6.2 Settings (`ttcoach/config.py`, pydantic-settings, env prefix `TTCOACH_`, optional `backend/ttcoach.local.toml`)
| Setting | Default | Meaning |
|---|---|---|
| `data_dir` | `REPO_ROOT/data` | job repository root |
| `rules_path` | `REPO_ROOT/backend/rules/default.yaml` | active rule set |
| `pose_estimator` | `sports2d` | registry key |
| `estimator_options` | `{}` | `dict[str, dict[str, Any]]`; opaque to core, passed to the factory |
| `ffmpeg_path` / `ffprobe_path` | `shutil.which` result | explicit executables |
| `max_video_height` | `720` | |
| `max_fps` | `60` | |
| `max_duration_s` | `300` | upload-time limit |
| `max_upload_bytes` | `2147483648` (2 GiB) | upload size limit → `413 upload_too_large` |
| `smoothing_cutoff_hz_max` | `12.0` | see plan §5.1 |

Sports2D options (interpreted only by `sports2d_adapter.py`). **Default: no configuration needed** —
the adapter discovers the interpreter as `<uv tool dir>/sports2d/Scripts/python.exe` (Windows) or
`<uv tool dir>/sports2d/bin/python` (POSIX), where `uv tool dir` is obtained by running `uv tool dir` once
at adapter construction. Overrides go in `backend/ttcoach.local.toml`; the path below is an
**example only**:
```toml
[estimator_options.sports2d]
# python = "C:/Users/<you>/AppData/Roaming/uv/tools/sports2d/Scripts/python.exe"   # EXAMPLE override; omit to auto-discover
entry = "Sports2D.Sports2D:main"     # console-script target, confirmed in Step 0 from entry_points.txt
mode = "balanced"
device = "auto"
timeout_s = 3600
```

### 6.3 Other seams
```python
class JobRepository(Protocol):  # ttcoach/jobs/repository.py; impl FileJobRepository
    def create(self, settings: JobSettings, upload: BinaryIO, filename: str) -> Job: ...
    def get(self, job_id: str) -> Job: ...
    def list(self) -> list[Job]: ...
    def update(self, job_id: str, **changes: Any) -> Job: ...
    def path(self, job_id: str, rel: str) -> Path: ...
    def write_json(self, job_id: str, rel: str, obj: Any) -> None: ...
    def read_json(self, job_id: str, rel: str) -> Any: ...

# ttcoach/extensions.py — signatures only, no implementations in v1
class ReferenceProvider(Protocol):
    def reference_metrics(self, stroke_type: str) -> Mapping[str, MetricDistribution]: ...
class AdviceGenerator(Protocol):
    def advise(self, report: Mapping[str, Any], strokes: Sequence[Mapping[str, Any]]) -> list[AdviceItem]: ...
```

---

## 7. Frontend data flow
- `useJob(id)` polls 1 s while `queued|running`.
- Result queries are keyed `['result', jobId, artifact, analysis_rev]` and fetch `?rev={analysis_rev}`;
  when `job.analysis_rev` changes, all four refetch together; the results view renders only when all
  four have the same `analysis_rev`.
- Every seek: `video.currentTime = frameToSeekTime(timeToFrame(t))` (§0).

---

## 8. Sports2D integration notes (Step 0 spike, 2026-10-04)
| Item | Value |
|---|---|
| Sports2D version pinned | `sports2d==0.8.34` (pulls pose2sim 0.10.49, rtmlib 0.0.16, onnxruntime 1.30.0, opencv 5.0.0.93). Install: `uv tool install "sports2d==0.8.34" --python 3.12` |
| Tool env python / entry point | `<uv tool dir>\sports2d\Scripts\python.exe` (here `C:\Users\Dima\AppData\Roaming\uv\tools\sports2d\Scripts\python.exe`); entry `Sports2D.Sports2D:main` (from `sports2d-0.8.34.dist-info/entry_points.txt`); launched as `python.exe -c "import sys; from Sports2D.Sports2D import main; sys.exit(main())" --config <job>.toml` |
| Output TRC path pattern | `<result_dir>/<video_stem>_Sports2D/<video_stem>_Sports2D_px_person{NN:02d}.trc` (+ `logs.txt`); one file per tracked person, `NN` = 00.. in `highest_likelihood` order |
| TRC marker names → canonical | 22 markers (HALPE_26 minus eyes/ears): `Hip→mid_hip, RHip→r_hip, RKnee→r_knee, RAnkle→r_ankle, RBigToe→r_big_toe, RSmallToe→r_small_toe, RHeel→r_heel, LHip→l_hip, LKnee→l_knee, LAnkle→l_ankle, LBigToe→l_big_toe, LSmallToe→l_small_toe, LHeel→l_heel, Neck→neck, Head→head, Nose→nose, RShoulder→r_shoulder, RElbow→r_elbow, RWrist→r_wrist, LShoulder→l_shoulder, LElbow→l_elbow, LWrist→l_wrist`; `l_eye r_eye l_ear r_ear` are always null |
| Frame# base (0 or 1) and Time column meaning | `Frame#` base **0**; one row per processed video frame, even when the person is absent; `Time = Frame# / fps` (0.0, 0.02, … at 50 fps); header `NumFrames` = processed frames (500 for the 10 s 50 fps clip) |
| y-axis sign in px TRC | Image convention (y down, origin top-left), so no flip is needed. Z is always 0.0. The header `Units` reads `m` even for px files (ignored) |
| Missing-value representation | Empty cells (X, Y and Z all empty for an undetected keypoint or person-frame); no `nan` strings observed |
| Per-keypoint scores obtainable? (option/file) | **No.** `save_pose` writes only the px TRC (no likelihood column or file); scores exist only in-process, so `confidence_kind = "presence"` (conf 1.0 where present, 0 where missing) |
| tqdm progress format on stderr | `\r`-redrawn ` 42%\|████▏     \| 84/200 [00:10<00:13,  8.7it/s]`; parse the last `(\d+)/(\d+) \[` match. An initial `0/N` bar prints before the model loads |
| Headless config keys verified | `[base] show_realtime_results=false, save_vid=false, save_img=false, save_pose=true, calculate_angles=true, save_angles=true (both required: Sports2D 0.8.34 crashes with TypeError at process.py:2117 when deleting a short-lived person if calculate_angles=false, and with IndexError in make_homogeneous if save_angles=false; the extra .mot output is ignored), nb_persons_to_detect='all', person_ordering_method='highest_likelihood', video_dir, video_input, result_dir`; `[px_to_meters_conversion] to_meters=false, make_c3d=false, save_calib=false`; `[post-processing] interpolate=false, reject_outliers=false, filter=false, show_graphs=false, save_graphs=false`; `[kinematics] do_ik=false`; `[pose] mode, device`. Exit code 0, no window opened, `stdin=DEVNULL` is fine |
| e2e trim segment of sample clip | `trim_start_s = 70`, `trim_end_s = 80` (static camera; Xu Xin in blue on the left; the partner and 2 background people are also tracked, giving 4 TRC persons). 4 s variant: 70–74 (3 persons) |
| CPU vs CUDA seconds per frame (720p) | CPU (OpenVINO backend auto-selected): ~0.11 s/frame (500 frames: 54.9 s processing, 59 s wall including ~5 s model load). CUDA: not available, because the tool env has only CPU `onnxruntime` ("No valid CUDA ... using OpenVINO backend with CPU") |
| Starlette FileResponse Range support | starlette 1.7.0 (fastapi 0.142.2): `FileResponse` handles `Range` natively (206) |
| stderr encoding | Run with `PYTHONUTF8=1`; output is decoded as UTF-8 with `errors="replace"`. filterpy prints a harmless `SyntaxWarning` on first import |
| Normalize flag change | `-avoid_negative_ts make_zero` dropped from §5.1 (it causes `start_time` 0.04 s); see the note in §5.1 |

### 8.1 e2e manual checklist results (filled in Step I)
| Check | Result |
|---|---|
| Clip / trim used | Xu Xin sample, `--trim-start 70 --trim-end 80` (1280×720, 50 fps, 500 frames). Sports2D tracked several persons → `awaiting_selection`; `--person auto` picked `p00` (Xu Xin, blue). Restart + `--check-only` on both jobs: E2E OK (AC3) |
| e2e run, `--handedness right` (job `20261004T213210-2c188f`) | done; strokes 0 (FH 0, BH 0); view `oblique` (side_on false, notice shown); feedback info 0 / warning 0 / critical 0; frame_drift 0; PNGs for frames 0/250/499 show the smoothed keypoints on the player's joints |
| e2e run, `--handedness left` (job `20261004T213554-29dc39`; Xu Xin is left-handed) | done; strokes 0 (FH 0, BH 0); view `oblique`; feedback 0 / 0 / 0; frame_drift 0 |
| Why 0 strokes | Placeholder segmentation thresholds (accepted by the user, not tuned): peak smoothed wrist speed on this clip is 2.7 torso_len/s (r_wrist) and 1.5 (l_wrist), with torso ≈ 121 px, both below `min_peak_speed: 4.0`. In this broadcast view the player faces away from the camera at an oblique angle, which shortens the apparent wrist motion |
| AC9 overlay offsets at 5 paused frames (wrist, ankle; px at 1280×720 display size; frame 5 = right after a seek) | _TBD: f1 …, f2 …, f3 …, f4 …, f5 …_ (pass if all ≤ 15 px) |
| AC10 cursor follows playback / chart click seeks | _TBD_ |
| AC11 stroke click seeks + zooms | _TBD_ |
| AC12 notice shown when not side-on | _TBD_ |
| AC8 rules edit → reanalyze changes feedback | _TBD_ |
| frame_drift reported by e2e | 0 (both runs) |
