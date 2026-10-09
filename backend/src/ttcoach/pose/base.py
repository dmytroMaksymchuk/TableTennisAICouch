"""PoseEstimator seam (DESIGN §6.1)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ttcoach.pose.schema import RawPose
from ttcoach.procs import RunChild


@dataclass(frozen=True)
class VideoMeta:
    path: Path
    fps: float
    n_frames: int
    width: int
    height: int
    duration_s: float


@dataclass(frozen=True)
class PoseRunContext:
    work_dir: Path  # estimator-private, empty at call time
    log: Callable[[str], None]  # appends to logs/pipeline.log
    progress: Callable[[float | None], None]
    run_child: RunChild  # tree-kill aware child launcher bound to the worker


class PoseEstimator(Protocol):
    name: str

    def version(self) -> str: ...

    def check_available(self) -> None: ...  # raises EstimatorUnavailable(detail)

    def estimate(self, video: VideoMeta, ctx: PoseRunContext) -> RawPose: ...


EstimatorFactory = Callable[[Mapping[str, Any]], PoseEstimator]
