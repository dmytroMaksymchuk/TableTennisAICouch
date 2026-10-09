"""FastAPI application factory (DESIGN §1, §3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import ttcoach.pose  # noqa: F401  (registers built-in estimators)
from ttcoach.api import routes_jobs, routes_meta, routes_results
from ttcoach.api.deps import ApiError
from ttcoach.config import Settings
from ttcoach.jobs.repository import FileJobRepository
from ttcoach.pipeline.worker import JobWorker


MULTIPART_OVERHEAD_BYTES = 1024 * 1024  # form fields + multipart boundaries on top of the file itself


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    settings = settings or Settings()
    repo = FileJobRepository(settings.data_dir)
    worker = JobWorker(repo, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            worker.stop(timeout_s=10.0)

    app = FastAPI(title="TT AI Coach", lifespan=lifespan)
    app.state.settings = settings
    app.state.repo = repo
    app.state.worker = worker

    @app.middleware("http")
    async def _upload_size_guard(request: Request, call_next):  # noqa: ANN202
        # Reject oversize uploads from Content-Length before the multipart body is spooled to disk;
        # the byte count in FileJobRepository.create still enforces the limit when the header lies or is absent.
        if request.method == "POST" and request.url.path.rstrip("/") == "/api/jobs":
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                length = -1
            limit = settings.max_upload_bytes
            if length > limit + MULTIPART_OVERHEAD_BYTES:
                return _error(413, "upload_too_large", f"upload exceeds {limit} bytes")
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors()]
        return _error(422, "validation_error", "; ".join(parts))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail))

    app.include_router(routes_jobs.router)
    app.include_router(routes_results.router)
    app.include_router(routes_meta.router)
    return app


def main() -> None:
    import uvicorn

    uvicorn.run("ttcoach.api.app:create_app", factory=True, host="127.0.0.1", port=8000)
