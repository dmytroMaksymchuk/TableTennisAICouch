"""Shared API helpers: error type and request-scoped accessors."""

from __future__ import annotations

from fastapi import Request

from ttcoach.config import Settings
from ttcoach.errors import JobNotFound
from ttcoach.jobs.models import Job
from ttcoach.jobs.repository import FileJobRepository
from ttcoach.pipeline.worker import JobWorker


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def repo(request: Request) -> FileJobRepository:
    return request.app.state.repo


def settings(request: Request) -> Settings:
    return request.app.state.settings


def worker(request: Request) -> JobWorker:
    return request.app.state.worker


def get_job(request: Request, job_id: str) -> Job:
    try:
        return repo(request).get(job_id)
    except JobNotFound:
        raise ApiError(404, "job_not_found", f"job {job_id} not found") from None
