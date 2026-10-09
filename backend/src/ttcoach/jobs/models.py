"""Job document (DESIGN §2, §3.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "awaiting_selection", "done", "failed"]
Stage = Literal["normalizing", "estimating_pose", "analyzing"]
STAGES: tuple[Stage, ...] = ("normalizing", "estimating_pose", "analyzing")
Handedness = Literal["right", "left"]
ErrorCode = Literal[
    "ffmpeg_failed", "pose_failed", "no_person_detected", "analysis_failed", "interrupted", "internal"
]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobSettings(BaseModel):
    handedness: Handedness
    trim_start_s: float | None = None
    trim_end_s: float | None = None
    estimator: str = "sports2d"


class VideoInfo(BaseModel):
    fps: float
    n_frames: int
    width: int
    height: int
    duration_s: float


class JobError(BaseModel):
    code: ErrorCode
    stage: Stage | None = None
    message: str


class Job(BaseModel):
    schema_version: int = 1
    id: str
    status: JobStatus = "queued"
    stage: Stage = "normalizing"
    resume_from: Stage = "normalizing"
    progress: float | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    settings: JobSettings
    original_filename: str
    video: VideoInfo | None = None
    analysis_rev: int = 0
    person_ids: list[str] | None = None
    ruleset_version: str | None = None
    recovery_attempts: int = 0
    error: JobError | None = None
