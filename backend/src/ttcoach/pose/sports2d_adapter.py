"""Sports2D adapter: runs the isolated Sports2D tool env as a child process and parses its px TRC
output into sparse canonical tracks (DESIGN §6.2, §8). Nothing Sports2D-specific leaves this module.
"""

from __future__ import annotations

import functools
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import tomli_w

from ttcoach.errors import EstimatorUnavailable, PipelineError
from ttcoach.pose.base import PoseRunContext, VideoMeta
from ttcoach.pose.registry import register_estimator
from ttcoach.pose.schema import CANONICAL_JOINTS, JOINT_INDEX, RawPose, RawTrack
from ttcoach.pose.sports2d_names import SPORTS2D_TO_CANONICAL
from ttcoach.pose.trc import TrcData, parse_trc
from ttcoach.procs import run_child

NAME = "sports2d"
DEFAULT_ENTRY = "Sports2D.Sports2D:main"
TRC_FRAME_BASE = 0  # DESIGN §8: Frame# is 0-based
_PROGRESS = re.compile(r"(\d+)/(\d+) \[")
_PERSON_FILE = re.compile(r"_px_person(\d+)\.trc$")


@functools.lru_cache(maxsize=1)
def _uv_tool_dir() -> Path | None:
    uv = shutil.which("uv")
    if uv is None:
        return None
    lines: list[str] = []
    res = run_child([uv, "tool", "dir"], 30, lines.append)
    if not res.ok:
        return None
    out = [ln.strip() for ln in lines if ln.strip()]
    return Path(out[-1]) if out else None


def default_python() -> Path | None:
    tool_dir = _uv_tool_dir()
    if tool_dir is None:
        return None
    env = tool_dir / "sports2d"
    return env / "Scripts" / "python.exe" if sys.platform == "win32" else env / "bin" / "python"


def build_config(video: VideoMeta, result_dir: Path, mode: str, device: str) -> dict[str, Any]:
    """Headless per-job Sports2D config (keys verified in the Step 0 spike, DESIGN §8)."""
    return {
        "base": {
            "video_input": video.path.name,
            "video_dir": video.path.parent.as_posix(),
            "result_dir": result_dir.as_posix(),
            "nb_persons_to_detect": "all",
            "person_ordering_method": "highest_likelihood",
            "visible_side": ["auto"],
            "time_range": [],
            "show_realtime_results": False,
            "save_vid": False,
            "save_img": False,
            "save_pose": True,
            # Both must stay True (Sports2D 0.8.34 bugs): with calculate_angles=False, X_flipped stays a
            # list and process.py:2117 raises TypeError when a short-lived person is deleted; with
            # save_angles=False the angle list is never filled and make_homogeneous([]) raises
            # IndexError. The extra *.mot output is ignored; our kinematics compute angles from the TRC.
            "calculate_angles": True,
            "save_angles": True,
        },
        "pose": {"pose_model": "Body_with_feet", "mode": mode, "device": device, "backend": "auto"},
        "px_to_meters_conversion": {"to_meters": False, "make_c3d": False, "save_calib": False},
        "post-processing": {
            "interpolate": False,
            "reject_outliers": False,
            "filter": False,
            "show_graphs": False,
            "save_graphs": False,
        },
        "kinematics": {"do_ik": False},
    }


def trc_to_track(trc: TrcData, person_id: str, fps: float, frame_base: int = TRC_FRAME_BASE) -> RawTrack:
    """Sparse canonical track from a Sports2D px TRC. Rows with no valid keypoint are dropped."""
    frame_idx = trc.frame - frame_base
    from_time = np.round(trc.time * fps).astype(np.int64)
    mismatch = np.abs(frame_idx - from_time)
    if len(mismatch) and int(mismatch.max()) > 1:
        bad = int(np.argmax(mismatch))
        raise PipelineError(
            "pose_failed",
            f"{person_id}: TRC Frame# {int(trc.frame[bad])} disagrees with Time {trc.time[bad]:.4f} at {fps:g} fps",
            "estimating_pose",
        )
    k = len(frame_idx)
    xy = np.full((k, len(CANONICAL_JOINTS), 2), np.nan, dtype=np.float32)
    for m, marker in enumerate(trc.markers):
        joint = SPORTS2D_TO_CANONICAL.get(marker)
        if joint is not None:
            xy[:, JOINT_INDEX[joint]] = trc.xyz[:, m, :2]  # px, y down: no flip (DESIGN §8)
    xy[np.isnan(xy).any(axis=2)] = np.nan
    present = ~np.isnan(xy).any(axis=2)
    conf = present.astype(np.float32)  # Sports2D TRC carries no scores -> presence
    keep = present.any(axis=1)
    return RawTrack(person_id=person_id, frame_idx=frame_idx[keep], xy=xy[keep], conf=conf[keep])


def collect_tracks(result_dir: Path, fps: float) -> tuple[list[RawTrack], int]:
    tracks, n_frames = [], 0
    files = sorted(result_dir.rglob("*_px_person*.trc"))
    for path in files:
        m = _PERSON_FILE.search(path.name)
        if m is None:
            continue
        trc = parse_trc(path)
        n_frames = max(n_frames, trc.num_frames)
        tracks.append(trc_to_track(trc, f"p{int(m.group(1)):02d}", fps))
    return tracks, n_frames


class Sports2DEstimator:
    name = NAME

    def __init__(self, options: Mapping[str, Any]) -> None:
        python = options.get("python")
        self.python: Path | None = Path(python) if python else default_python()
        self.entry: str = options.get("entry", DEFAULT_ENTRY)
        self.mode: str = options.get("mode", "balanced")
        self.device: str = options.get("device", "auto")
        self.timeout_s: float = float(options.get("timeout_s", 3600))

    def _site_packages(self) -> Path | None:
        if self.python is None:
            return None
        env = self.python.parent.parent
        if sys.platform == "win32":
            return env / "Lib" / "site-packages"
        found = sorted(env.glob("lib/python3*/site-packages"))
        return found[0] if found else None

    def version(self) -> str:
        sp = self._site_packages()
        if sp is not None:
            for d in sp.glob("sports2d-*.dist-info"):
                return d.name[len("sports2d-") : -len(".dist-info")]
        return "unknown"

    def check_available(self) -> None:
        if self.python is None:
            raise EstimatorUnavailable("Sports2D tool env not found (`uv tool dir` unavailable); set estimator_options.sports2d.python")
        if not self.python.is_file():
            raise EstimatorUnavailable(f"Sports2D python not found at {self.python}; run `uv tool install sports2d --python 3.12`")
        res = run_child([str(self.python), "-c", "import Sports2D"], 120)
        if not res.ok:
            raise EstimatorUnavailable(f"`import Sports2D` failed with {self.python}: {res.tail_text()[-300:]}")

    def launcher_code(self) -> str:
        module, _, func = self.entry.partition(":")
        return f"import sys; from {module} import {func}; sys.exit({func}())"

    def estimate(self, video: VideoMeta, ctx: PoseRunContext) -> RawPose:
        if self.python is None or not self.python.is_file():
            raise PipelineError("pose_failed", f"Sports2D python not found: {self.python}", "estimating_pose")
        result_dir = ctx.work_dir / "out"
        config_path = ctx.work_dir / "config.toml"
        config_path.write_bytes(tomli_w.dumps(build_config(video, result_dir, self.mode, self.device)).encode())
        version = self.version()
        ctx.log(f"sports2d {version} at {self.python}, mode={self.mode}, device={self.device}")

        def on_line(line: str) -> None:
            matches = _PROGRESS.findall(line)
            if matches:
                done, total = (int(v) for v in matches[-1])
                ctx.progress(done / total if total else None)
            elif line.strip():
                ctx.log(f"sports2d: {line.rstrip()}")

        cmd = [str(self.python), "-c", self.launcher_code(), "--config", str(config_path)]
        res = ctx.run_child(cmd, self.timeout_s, on_line, cwd=ctx.work_dir)
        if res.timed_out:
            raise PipelineError("pose_failed", f"Sports2D timed out after {self.timeout_s:g} s", "estimating_pose")
        if res.returncode != 0:
            raise PipelineError(
                "pose_failed", f"Sports2D exit {res.returncode}: {res.tail_text()[-800:]}", "estimating_pose"
            )
        tracks, n_frames = collect_tracks(result_dir, video.fps)
        return RawPose(
            estimator=NAME,
            estimator_version=version,
            joints=CANONICAL_JOINTS,
            tracks=tuple(tracks),
            source_n_frames=n_frames if tracks else video.n_frames,
            confidence_kind="presence",
        )


@register_estimator(NAME)
def _factory(options: Mapping[str, Any]) -> Sports2DEstimator:
    return Sports2DEstimator(options)
