from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ttcoach.config import Settings
from ttcoach.pose.base import PoseRunContext, VideoMeta
from ttcoach.pose.registry import register_estimator
from ttcoach.pose.schema import CANONICAL_JOINTS, JOINT_INDEX, RawPose, RawTrack

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    # Also registered in backend/pyproject.toml; repeated here because `uv run --project backend pytest`
    # from the repo root does not pick up backend/pyproject.toml as the ini file.
    config.addinivalue_line("markers", "sports2d: needs the Sports2D tool env (check_available)")

# ---------------------------------------------------------------- stub estimator (AC4: registered like any adapter)
# Standing skeleton, offsets from mid_hip in px (y down); torso (neck - mid_hip) = 60 px, straight legs.
SKELETON = {
    "nose": (0, -75), "l_eye": (-3, -78), "r_eye": (3, -78), "l_ear": (-6, -76), "r_ear": (6, -76),
    "head": (0, -85), "neck": (0, -60), "l_shoulder": (-15, -60), "r_shoulder": (15, -60),
    "l_elbow": (-20, -35), "r_elbow": (25, -35), "l_wrist": (-20, -12), "r_wrist": (35, -15),
    "l_hip": (-10, 0), "r_hip": (10, 0), "mid_hip": (0, 0), "l_knee": (-10, 30), "r_knee": (10, 30),
    "l_ankle": (-10, 60), "r_ankle": (10, 60), "l_big_toe": (-16, 64), "r_big_toe": (16, 64),
    "l_small_toe": (-20, 64), "r_small_toe": (20, 64), "l_heel": (-8, 64), "r_heel": (8, 64),
}  # fmt: skip

STUB: dict[str, Any] = {}


def reset_stub(**overrides: Any) -> dict[str, Any]:
    STUB.clear()
    STUB.update({"persons": 1, "raise": None, "calls": 0, "work_files": []}, **overrides)
    return STUB


reset_stub()


def stub_person(n: int, fps: float, cx: float, cy: float, scale: float = 1.0, bursts=(0.6, 1.4)) -> np.ndarray:
    xy = np.zeros((n, len(CANONICAL_JOINTS), 2), dtype=np.float32)
    for name, (dx, dy) in SKELETON.items():
        xy[:, JOINT_INDEX[name]] = (cx + dx * scale, cy + dy * scale)
    t = np.arange(n) / fps
    swing = sum(100.0 * np.exp(-(((t - tc) / 0.1) ** 2)) for tc in bursts)
    xy[:, JOINT_INDEX["r_wrist"], 0] += swing * scale
    return xy


class StubEstimator:
    name = "stub"

    def __init__(self, options: Mapping[str, Any]) -> None:
        self.options = dict(options)

    def version(self) -> str:
        return "stub-1"

    def check_available(self) -> None:
        return None

    def estimate(self, video: VideoMeta, ctx: PoseRunContext) -> RawPose:
        STUB["calls"] += 1
        STUB["work_files"].append(sorted(p.name for p in ctx.work_dir.iterdir()))
        (ctx.work_dir / "scratch.txt").write_text("stub work file")
        ctx.progress(0.5)
        if STUB["raise"] is not None:
            raise STUB["raise"]
        if STUB.get("child_cmd"):
            ctx.run_child(STUB["child_cmd"], 120, STUB.setdefault("child_lines", []).append)
        n = video.n_frames
        frames = np.arange(n, dtype=np.int64)
        tracks = []
        for i in range(STUB["persons"]):
            xy = stub_person(n, video.fps, 100 + 140 * i, 130, scale=1.0 if i == 0 else 0.6)
            tracks.append(RawTrack(f"p{i:02d}", frames, xy, np.ones(xy.shape[:2], dtype=np.float32)))
        return RawPose("stub", "stub-1", CANONICAL_JOINTS, tuple(tracks), n, "score")


@register_estimator("stub")
def _stub_factory(options: Mapping[str, Any]) -> StubEstimator:
    return StubEstimator(options)


@pytest.fixture
def stub() -> Iterator[dict[str, Any]]:
    yield reset_stub()
    reset_stub()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", pose_estimator="stub")


def wait_for(fn, timeout_s: float = 60.0, interval_s: float = 0.05):
    deadline = time.monotonic() + timeout_s
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(interval_s)


def _make_clip(path: Path, seconds: float, fps: int = 25, size: str = "320x240", audio: bool = True) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "ffmpeg is required for the test suite"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={fps}"]  # fmt: skip
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """2 s testsrc clips in each supported container."""
    d = tmp_path_factory.mktemp("clips")
    return {ext: _make_clip(d / f"clip.{ext}", 2.0) for ext in ("mp4", "mkv", "mov")}
