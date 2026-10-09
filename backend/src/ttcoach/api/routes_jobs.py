"""Job routes (DESIGN §3.1–§3.9)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ttcoach.api import deps
from ttcoach.api.deps import ApiError, get_job
from ttcoach.errors import PipelineError, UploadTooLarge
from ttcoach.jobs.models import Job, JobSettings
from ttcoach.media import ffmpeg
from ttcoach.pipeline import stages

router = APIRouter(prefix="/api/jobs")
ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".mov"}


def _opt_float(name: str, value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        v = float(value)
    except ValueError:
        raise ApiError(422, "validation_error", f"{name} must be a number") from None
    if not math.isfinite(v):
        raise ApiError(422, "validation_error", f"{name} must be finite")
    return v


@router.post("", status_code=201)
def create_job(
    request: Request,
    file: Annotated[UploadFile, File()],
    handedness: Annotated[Literal["right", "left"], Form()],
    trim_start_s: Annotated[str | None, Form()] = None,
    trim_end_s: Annotated[str | None, Form()] = None,
) -> Job:
    s = deps.settings(request)
    repo = deps.repo(request)
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ApiError(415, "unsupported_media_type", f"unsupported file type {filename!r}; use .mp4, .mkv or .mov")
    start = _opt_float("trim_start_s", trim_start_s)
    end = _opt_float("trim_end_s", trim_end_s)
    if start is not None and start < 0:
        raise ApiError(422, "validation_error", "trim_start_s must be >= 0")
    if end is not None and end <= (start or 0.0):
        raise ApiError(422, "validation_error", "trim_end_s must be greater than trim_start_s")
    if not s.ffprobe_path:
        raise ApiError(500, "internal", "ffprobe not found; set TTCOACH_FFPROBE_PATH")

    def validate(job_id: str, input_path: Path) -> None:
        try:
            probe = ffmpeg.probe(s.ffprobe_path, input_path)
        except ffmpeg.ProbeError as e:
            raise ApiError(415, "unreadable_media", f"cannot read video: {e}") from None
        duration = ffmpeg.duration_s(probe)
        if duration <= 0:
            raise ApiError(415, "unreadable_media", "cannot determine the video duration")
        if start is not None and start >= duration:
            raise ApiError(422, "validation_error", f"trim_start_s {start} is beyond the video end ({duration:.2f} s)")
        effective = (min(end, duration) if end is not None else duration) - (start or 0.0)
        if effective > s.max_duration_s:
            raise ApiError(
                422, "video_too_long",
                f"video segment is {effective:.0f} s; the limit is {s.max_duration_s:.0f} s — trim it",
            )  # fmt: skip
        repo.write_json(job_id, "input/probe.json", probe)

    settings_ = JobSettings(handedness=handedness, trim_start_s=start, trim_end_s=end, estimator=s.pose_estimator)
    try:
        # The repository removes the job dir on any exception (including from `validate`).
        job = repo.create(settings_, file.file, filename, max_bytes=s.max_upload_bytes, before_commit=validate)
    except UploadTooLarge as e:
        raise ApiError(413, "upload_too_large", f"upload exceeds {e.limit} bytes") from None
    try:
        deps.worker(request).enqueue(job.id)
    except BaseException:
        repo.delete(job.id)
        raise
    return job


@router.get("")
def list_jobs(request: Request) -> dict[str, list[Job]]:
    return {"jobs": deps.repo(request).list()}


@router.get("/{job_id}")
def read_job(request: Request, job_id: str) -> Job:
    return get_job(request, job_id)


@router.get("/{job_id}/candidates")
def read_candidates(request: Request, job_id: str) -> dict:
    get_job(request, job_id)
    repo = deps.repo(request)
    if not stages.pose_complete(repo, job_id):
        raise ApiError(404, "candidates_not_ready", "pose estimation has not completed")
    return repo.read_json(job_id, "pose/candidates.json")


@router.get("/{job_id}/frames/{n:int}.jpg")
def read_frame(request: Request, job_id: str, n: int) -> FileResponse:
    job = get_job(request, job_id)
    repo = deps.repo(request)
    if job.video is None or not stages.normalize_complete(repo, job_id):
        raise ApiError(404, "video_not_ready", "normalized video not available yet")
    if not 0 <= n < job.video.n_frames:
        raise ApiError(404, "frame_not_found", f"frame {n} outside 0..{job.video.n_frames - 1}")
    s = deps.settings(request)
    try:
        path = ffmpeg.extract_frame(
            s.ffmpeg_path or "ffmpeg", repo.path(job_id, "video/normalized.mp4"),
            n, repo.path(job_id, f"video/frames/{n:06d}.jpg"),
        )  # fmt: skip
    except PipelineError as e:
        raise ApiError(500, "ffmpeg_failed", e.message) from None
    return FileResponse(path, media_type="image/jpeg")


class SelectionBody(BaseModel):
    person_ids: list[str] = Field(min_length=1)


@router.post("/{job_id}/selection", status_code=202)
def select_persons(request: Request, job_id: str, body: SelectionBody) -> Job:
    job = get_job(request, job_id)
    repo = deps.repo(request)
    if job.status not in ("awaiting_selection", "done"):
        raise ApiError(409, "invalid_state", f"selection not allowed while job is {job.status}")
    known = {c["person_id"] for c in repo.read_json(job_id, "pose/candidates.json")["candidates"]}
    unknown = [p for p in body.person_ids if p not in known]
    if unknown:
        raise ApiError(422, "unknown_person_id", f"unknown person ids: {', '.join(unknown)}")
    ids = list(dict.fromkeys(body.person_ids))
    repo.write_json(job_id, "pose/selection.json", {"person_ids": ids, "selected_by": "user"})
    job = repo.update(job_id, status="queued", resume_from="analyzing", progress=None, error=None)
    deps.worker(request).enqueue(job_id)
    return job


@router.post("/{job_id}/reanalyze", status_code=202)
def reanalyze(request: Request, job_id: str) -> Job:
    job = get_job(request, job_id)
    repo = deps.repo(request)
    if job.status in ("queued", "running", "awaiting_selection"):
        raise ApiError(409, "invalid_state", f"cannot re-analyze while job is {job.status}")
    if not stages.pose_complete(repo, job_id) or not repo.path(job_id, "pose/selection.json").is_file():
        raise ApiError(409, "pose_unavailable", "pose estimation is not complete; use POST /retry")
    if job.status == "failed" and (job.error is None or job.error.stage != "analyzing"):
        raise ApiError(409, "invalid_state", "only jobs that failed during analyzing can be re-analyzed")
    job = repo.update(
        job_id, status="queued", resume_from="analyzing", progress=None, error=None, recovery_attempts=0
    )
    deps.worker(request).enqueue(job_id)
    return job


@router.post("/{job_id}/retry", status_code=202)
def retry(request: Request, job_id: str) -> Job:
    job = get_job(request, job_id)
    repo = deps.repo(request)
    if job.status != "failed":
        raise ApiError(409, "invalid_state", f"retry is only allowed for failed jobs (job is {job.status})")
    job = repo.update(
        job_id, status="queued", resume_from=stages.first_incomplete_stage(repo, job),
        recovery_attempts=0, progress=None, error=None,
    )  # fmt: skip
    deps.worker(request).enqueue(job_id)
    return job


@router.get("/{job_id}/video")
def read_video(request: Request, job_id: str) -> FileResponse:
    get_job(request, job_id)
    repo = deps.repo(request)
    if not stages.normalize_complete(repo, job_id):
        raise ApiError(404, "video_not_ready", "normalized video not available yet")
    return FileResponse(repo.path(job_id, "video/normalized.mp4"), media_type="video/mp4")

