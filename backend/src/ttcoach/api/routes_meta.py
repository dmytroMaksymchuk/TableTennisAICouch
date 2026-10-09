"""Meta routes: rules and health (DESIGN §3.11, §3.12)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from ttcoach.api import deps
from ttcoach.api.deps import ApiError
from ttcoach.coaching import load_ruleset
from ttcoach.errors import EstimatorUnavailable
from ttcoach.pose.registry import available_estimators, get_estimator

router = APIRouter(prefix="/api")
HEALTH_TTL_S = 60.0
_health_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@router.get("/rules")
def read_rules(request: Request) -> dict[str, Any]:
    path = deps.settings(request).rules_path
    try:
        ruleset = load_ruleset(path)
    except ValueError as e:
        raise ApiError(500, "invalid_rules", str(e)) from None
    return {"path": str(path), "ruleset": ruleset.model_dump(mode="json")}


def _estimator_status(name: str, options: dict[str, Any]) -> dict[str, Any]:
    cached = _health_cache.get(name)
    if cached and time.monotonic() - cached[0] < HEALTH_TTL_S:
        return cached[1]
    try:
        est = get_estimator(name, options)
        est.check_available()
        python = getattr(est, "python", None)
        detail = f"{name} {est.version()}" + (f" at {python}" if python else "")
        status = {"available": True, "detail": detail}
    except EstimatorUnavailable as e:
        status = {"available": False, "detail": e.detail}
    except Exception as e:  # a broken adapter must not break /health
        status = {"available": False, "detail": f"{type(e).__name__}: {e}"}
    _health_cache[name] = (time.monotonic(), status)
    return status


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    s = deps.settings(request)
    return {
        "status": "ok",
        "ffmpeg": bool(s.ffmpeg_path and Path(s.ffmpeg_path).is_file()),
        "estimators": {name: _estimator_status(name, s.options_for(name)) for name in available_estimators()},
        "default_estimator": s.pose_estimator,
    }
