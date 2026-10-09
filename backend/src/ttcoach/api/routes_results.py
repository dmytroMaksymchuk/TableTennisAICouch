"""Result artifacts (DESIGN §3.10): served from precompressed files, never compressed per request."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from ttcoach.api import deps
from ttcoach.api.deps import ApiError, get_job
from ttcoach.pipeline import stages

router = APIRouter(prefix="/api/jobs")


@router.get("/{job_id}/results/{artifact}")
def read_result(request: Request, job_id: str, artifact: str, rev: int | None = None) -> FileResponse:
    job = get_job(request, job_id)
    if artifact not in stages.ARTIFACTS:
        raise ApiError(404, "unknown_artifact", f"unknown artifact {artifact!r}; one of {', '.join(stages.ARTIFACTS)}")
    if rev is None:
        if job.analysis_rev == 0:
            raise ApiError(404, "artifact_not_ready", "no analysis yet")
        rev = job.analysis_rev
    if rev < 1 or rev > job.analysis_rev:
        raise ApiError(404, "rev_not_found", f"analysis revision {rev} not found")
    d = stages.rev_dir(deps.repo(request), job_id, rev)
    plain = d / f"{artifact}.json"
    if not plain.is_file():
        if rev == job.analysis_rev:
            raise ApiError(404, "artifact_not_ready", f"{artifact} for revision {rev} is missing")
        raise ApiError(404, "rev_not_found", f"analysis revision {rev} not found (pruned)")
    headers = {"X-Analysis-Rev": str(rev), "Vary": "Accept-Encoding", "Cache-Control": "no-cache"}
    gz = d / f"{artifact}.json.gz"
    if "gzip" in request.headers.get("accept-encoding", "").lower() and gz.is_file():
        headers["Content-Encoding"] = "gzip"
        return FileResponse(gz, media_type="application/json", headers=headers)
    return FileResponse(plain, media_type="application/json", headers=headers)
