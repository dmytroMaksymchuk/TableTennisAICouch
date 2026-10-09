"""Real Sports2D run through the adapter and core alignment (plan Step 0 item 7, B2)."""

from __future__ import annotations

from pathlib import Path

import pytest

import ttcoach.pose  # noqa: F401  (registers sports2d)
from ttcoach.config import REPO_ROOT, Settings
from ttcoach.errors import EstimatorUnavailable
from ttcoach.jobs.models import JobSettings
from ttcoach.jobs.repository import FileJobRepository
from ttcoach.pipeline import stages
from ttcoach.pose.registry import get_estimator
from ttcoach.procs import ChildRunner

SAMPLE = REPO_ROOT / "Xu Xin Training ｜ Swedish Open 2017 [A5lmr4FoqH8].mkv"
TRIM = (70.0, 74.0)  # DESIGN §8: static-camera segment, 4 s variant


@pytest.mark.sports2d
def test_sports2d_run_pose_stage(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", pose_estimator="sports2d")
    estimator = get_estimator("sports2d", settings.options_for("sports2d"))
    try:
        estimator.check_available()
    except EstimatorUnavailable as e:
        pytest.skip(f"Sports2D unavailable: {e.detail}")
    if not SAMPLE.is_file():
        pytest.fail(f"sample clip missing: {SAMPLE}")

    repo = FileJobRepository(settings.data_dir)
    with open(SAMPLE, "rb") as f:
        job = repo.create(
            JobSettings(handedness="right", trim_start_s=TRIM[0], trim_end_s=TRIM[1], estimator="sports2d"),
            f,
            SAMPLE.name,
        )
    ctx = stages.StageContext(repo, settings, job.id, ChildRunner())
    job = stages.run_normalize(ctx)
    assert job.video is not None and job.video.n_frames == 200 and job.video.fps == 50.0

    doc = stages.run_pose(ctx)
    assert len(doc["candidates"]) >= 1
    persons = repo.read_json(job.id, "pose/persons.json")
    assert persons["frame_drift"] == 0
    assert persons["estimator"] == "sports2d" and persons["confidence_kind"] == "presence"
    assert persons["n_frames"] == 200 and len(persons["joints"]) == 26
    assert stages.pose_complete(repo, job.id)
    log = repo.path(job.id, "logs/pipeline.log").read_text(encoding="utf-8")
    assert "sports2d 0.8.34" in log
