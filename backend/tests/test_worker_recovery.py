from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import wait_for
from tests.test_api import upload, wait_job
from tests.test_procs import PidCollector, TREE_SCRIPT, wait_dead
from ttcoach.api.app import create_app
from ttcoach.config import Settings
from ttcoach.jobs.repository import FileJobRepository
from ttcoach.pipeline import stages
from ttcoach.pipeline.worker import JobWorker


def job_dir(settings: Settings, job_id: str) -> Path:
    return settings.data_dir / "jobs" / job_id


def finished_job(settings: Settings, clip: Path) -> str:
    with TestClient(create_app(settings)) as c:
        job = wait_job(c, upload(c, clip).json()["id"])
        assert job["status"] == "done"
        return job["id"]


def test_running_pose_job_resumes_after_restart(settings: Settings, clips, stub) -> None:
    job_id = finished_job(settings, clips["mp4"])
    d = job_dir(settings, job_id)
    repo = FileJobRepository(settings.data_dir)
    normalized_mtime = (d / "video" / "normalized.mp4").stat().st_mtime_ns
    for p in (d / "pose").glob("*.json"):
        p.unlink()
    shutil.rmtree(d / "analysis")
    (d / "pose" / "stub" / "stale.bin").write_bytes(b"half-written")
    repo.update(job_id, status="running", stage="estimating_pose", resume_from="estimating_pose", analysis_rev=0)
    calls_before = stub["calls"]

    JobWorker(repo, settings).recover()  # what app startup does first
    recovered = repo.get(job_id)
    assert (recovered.status, recovered.resume_from, recovered.recovery_attempts) == ("queued", "estimating_pose", 1)
    repo.update(job_id, status="running")  # simulate the crash state again for the real app start

    with TestClient(create_app(settings)) as c:
        job = wait_job(c, job_id, {"done", "failed"})
    assert job["status"] == "done" and job["analysis_rev"] == 1
    assert job["recovery_attempts"] == 0  # reset once the job completes
    assert "resuming at estimating_pose (attempt 2)" in (d / "logs" / "pipeline.log").read_text(encoding="utf-8")
    assert stub["calls"] == calls_before + 1
    assert stub["work_files"][-1] == []  # stale file removed before the estimator ran
    assert (d / "video" / "normalized.mp4").stat().st_mtime_ns == normalized_mtime  # normalize not re-run


def test_recovery_limits_and_untouched_states(settings: Settings, clips, stub) -> None:
    exhausted = finished_job(settings, clips["mp4"])
    waiting = finished_job(settings, clips["mp4"])
    queued = finished_job(settings, clips["mp4"])
    repo = FileJobRepository(settings.data_dir)
    repo.update(exhausted, status="running", stage="analyzing", recovery_attempts=2)
    repo.update(waiting, status="awaiting_selection", resume_from="analyzing")
    repo.update(queued, status="queued", resume_from="analyzing")
    rev_before = repo.get(queued).analysis_rev

    with TestClient(create_app(settings)) as c:
        q = wait_job(c, queued, {"done", "failed"})
        assert q["status"] == "done" and q["analysis_rev"] == rev_before + 1
        e = c.get(f"/api/jobs/{exhausted}").json()
        assert e["status"] == "failed" and e["recovery_attempts"] == 3
        assert e["error"]["code"] == "interrupted" and e["error"]["stage"] == "analyzing"
        assert c.get(f"/api/jobs/{waiting}").json()["status"] == "awaiting_selection"
        # /retry on the interrupted job resumes at the first incomplete stage (analysis)
        r = c.post(f"/api/jobs/{exhausted}/retry")
        assert r.status_code == 202 and r.json()["resume_from"] == "analyzing" and r.json()["recovery_attempts"] == 0
        assert wait_job(c, exhausted, {"done", "failed"})["status"] == "done"


def test_second_app_serves_finished_job(settings: Settings, clips, stub) -> None:
    job_id = finished_job(settings, clips["mov"])
    with TestClient(create_app(settings, start_worker=False)) as c:
        assert c.get(f"/api/jobs/{job_id}").json()["status"] == "done"
        for artifact in stages.ARTIFACTS:
            r = c.get(f"/api/jobs/{job_id}/results/{artifact}")
            assert r.status_code == 200 and r.headers["x-analysis-rev"] == "1"
        assert c.get(f"/api/jobs/{job_id}/video").status_code == 200
        assert [j["id"] for j in c.get("/api/jobs").json()["jobs"]] == [job_id]


def test_rev_crash_window_never_reuses_revision(settings: Settings, clips, stub) -> None:
    job_id = finished_job(settings, clips["mp4"])
    d = job_dir(settings, job_id) / "analysis"
    with TestClient(create_app(settings)) as c:
        assert c.post(f"/api/jobs/{job_id}/reanalyze").status_code == 202
        assert wait_job(c, job_id, {"done", "failed"})["analysis_rev"] == 2
    # crash between rename and job.json update: rev-0003 on disk, job.json still says 2; plus a stale tmp dir
    shutil.copytree(d / "rev-0002", d / "rev-0003")
    (d / "rev-0003" / "marker").write_text("orphan rev 3")
    (d / "rev-0004.tmp").mkdir()
    (d / "rev-0004.tmp" / "report.json").write_text("{half")

    with TestClient(create_app(settings, start_worker=False)) as c:
        r = c.get(f"/api/jobs/{job_id}/results/report")
        assert r.headers["x-analysis-rev"] == "2" and r.json()["analysis_rev"] == 2
        assert c.get(f"/api/jobs/{job_id}/results/report?rev=3").json()["error"]["code"] == "rev_not_found"

    with TestClient(create_app(settings)) as c:
        assert c.post(f"/api/jobs/{job_id}/reanalyze").status_code == 202
        job = wait_job(c, job_id, {"done", "failed"})
        assert job["status"] == "done" and job["analysis_rev"] == 4
        assert c.get(f"/api/jobs/{job_id}/results/report").json()["analysis_rev"] == 4
    assert not (d / "rev-0004.tmp").exists()
    assert (d / "rev-0003" / "marker").read_text() == "orphan rev 3"  # never overwritten
    assert sorted(p.name for p in d.iterdir()) == ["rev-0003", "rev-0004"]


def test_prune_failure_is_logged_and_retried(settings: Settings, clips, stub, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = finished_job(settings, clips["mp4"])
    d = job_dir(settings, job_id)
    real_rmtree = shutil.rmtree

    def locked_rmtree(path, *a, **kw):
        if Path(path).name == "rev-0001":
            raise PermissionError(32, "The process cannot access the file", str(path))
        return real_rmtree(path, *a, **kw)

    with TestClient(create_app(settings)) as c:
        c.post(f"/api/jobs/{job_id}/reanalyze")
        wait_job(c, job_id, {"done", "failed"})
        monkeypatch.setattr(stages.shutil, "rmtree", locked_rmtree)
        c.post(f"/api/jobs/{job_id}/reanalyze")
        job = wait_job(c, job_id, {"done", "failed"})
        assert job["status"] == "done" and job["analysis_rev"] == 3
        assert (d / "analysis" / "rev-0001").is_dir()
        assert "could not prune" in (d / "logs" / "pipeline.log").read_text(encoding="utf-8")
        monkeypatch.setattr(stages.shutil, "rmtree", real_rmtree)
        c.post(f"/api/jobs/{job_id}/reanalyze")
        assert wait_job(c, job_id, {"done", "failed"})["analysis_rev"] == 4
    assert sorted(p.name for p in (d / "analysis").iterdir()) == ["rev-0003", "rev-0004"]


def test_pose_cleanup_failure_fails_job_but_not_worker(
    settings: Settings, clips, stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = finished_job(settings, clips["mp4"])
    work = job_dir(settings, job_id) / "pose" / "stub"
    repo = FileJobRepository(settings.data_dir)
    (work.parent / "candidates.json").unlink()
    repo.update(
        job_id, status="failed",
        error={"code": "pose_failed", "stage": "estimating_pose", "message": "earlier failure"},
    )  # fmt: skip
    real_rmtree = shutil.rmtree

    def locked_rmtree(path, *a, **kw):
        if Path(path) == work:
            raise PermissionError(32, "The process cannot access the file", str(path))
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(stages.shutil, "rmtree", locked_rmtree)
    with TestClient(create_app(settings)) as c:
        assert c.post(f"/api/jobs/{job_id}/retry").status_code == 202
        job = wait_job(c, job_id, {"done", "failed"})
        assert job["status"] == "failed" and job["error"]["code"] == "pose_failed"
        assert str(work) in job["error"]["message"] and "POST /retry" in job["error"]["message"]
        assert c.app.state.worker.alive
        other = wait_job(c, upload(c, clips["mkv"]).json()["id"])
        assert other["status"] == "done"


def test_shutdown_kills_child_tree_and_job_stays_running(settings: Settings, clips, stub) -> None:
    stub["child_cmd"] = [sys.executable, "-c", TREE_SCRIPT]
    col = PidCollector()
    with TestClient(create_app(settings)) as c:
        job_id = upload(c, clips["mp4"]).json()["id"]
        wait_for(lambda: any(line.startswith("PIDS ") for line in stub.get("child_lines", [])), 60)
        col(next(line for line in stub["child_lines"] if line.startswith("PIDS ")))
        t0 = time.monotonic()
    assert time.monotonic() - t0 < 10
    wait_dead(col.pids)
    job = FileJobRepository(settings.data_dir).get(job_id)
    assert job.status == "running" and job.stage == "estimating_pose"

    stub["child_cmd"] = None
    with TestClient(create_app(settings)) as c:
        job = wait_job(c, job_id, {"done", "failed"})
        assert job["status"] == "done" and job["recovery_attempts"] == 0
    log = (job_dir(settings, job_id) / "logs" / "pipeline.log").read_text(encoding="utf-8")
    assert "resuming at estimating_pose (attempt 1)" in log


def test_missing_job_video_is_rebuilt_from_probe(settings: Settings, clips, stub) -> None:
    """Crash after the normalize markers were written but before job.video was saved."""
    job_id = finished_job(settings, clips["mp4"])
    repo = FileJobRepository(settings.data_dir)
    expected = repo.get(job_id).video
    d = job_dir(settings, job_id)
    for p in (d / "pose").glob("*.json"):
        p.unlink()
    repo.update(job_id, status="running", stage="normalizing", resume_from="normalizing", video=None)
    with TestClient(create_app(settings)) as c:
        job = wait_job(c, job_id, {"done", "failed"})
    assert job["status"] == "done", job
    assert job["video"] == expected.model_dump()
    assert "rebuilt job.video" in (d / "logs" / "pipeline.log").read_text(encoding="utf-8")

    # same state reached through /retry and through /reanalyze (analysis needs job.video too)
    repo.update(job_id, status="failed", video=None,
                error={"code": "analysis_failed", "stage": "analyzing", "message": "x"})  # fmt: skip
    with TestClient(create_app(settings)) as c:
        assert c.post(f"/api/jobs/{job_id}/reanalyze").status_code == 202
        job = wait_job(c, job_id, {"done", "failed"})
    assert job["status"] == "done" and job["video"] == expected.model_dump()


def test_reanalyze_and_retry_reset_recovery_attempts(settings: Settings, clips, stub) -> None:
    job_id = finished_job(settings, clips["mp4"])
    repo = FileJobRepository(settings.data_dir)
    repo.update(job_id, recovery_attempts=2)
    with TestClient(create_app(settings, start_worker=False)) as c:
        r = c.post(f"/api/jobs/{job_id}/reanalyze")
        assert r.status_code == 202 and r.json()["recovery_attempts"] == 0
        repo.update(job_id, status="failed", recovery_attempts=2,
                    error={"code": "interrupted", "stage": "analyzing", "message": "x"})  # fmt: skip
        r = c.post(f"/api/jobs/{job_id}/retry")
        assert r.status_code == 202 and r.json()["recovery_attempts"] == 0
