"""Domain errors shared by pipeline stages, estimators and the API."""

from __future__ import annotations


class PipelineError(Exception):
    """A stage failure that becomes `job.error` (DESIGN §3.2 error codes)."""

    def __init__(self, code: str, message: str, stage: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage


class EstimatorUnavailable(Exception):
    """The estimator cannot run on this machine (missing tool env, interpreter, ...)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnknownEstimator(KeyError):
    pass


class JobNotFound(KeyError):
    pass


class ChildCancelled(Exception):
    """A child process was killed because the worker is shutting down."""


class UploadTooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"upload exceeds {limit} bytes")
        self.limit = limit
