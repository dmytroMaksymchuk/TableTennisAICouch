from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import _make_clip, wait_for
from ttcoach.api.app import create_app
from ttcoach.config import Settings
from ttcoach.pipeline.stages import RESULT_MODELS

TERMINAL = {"done", "failed", "awaiting_selection"}


def upload(client: TestClient, path: Path, name: str | None = None, **fields: Any):
    data = {"handedness": "right", **{k: str(v) for k, v in fields.items()}}
    with open(path, "rb") as f:
        return client.post("/api/jobs", files={"file": (name or path.name, f, "application/octet-stream")}, data=data)


def wait_job(client: TestClient, job_id: str, statuses=TERMINAL, timeout_s: float = 90.0) -> dict[str, Any]:
    def check():
        job = client.get(f"/api/jobs/{job_id}").json()
        return job if job["status"] in statuses else None

    return wait_for(check, timeout_s)


@pytest.fixture
def client(settings: Settings, stub) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.mark.parametrize("ext", ["mp4", "mkv", "mov"])
def test_upload_each_container_to_done(client: TestClient, clips: dict[str, Path], ext: str, settings: Settings) -> None:
    r = upload(client, clips[ext], name=f"My Clip.{ext.upper()}")
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued" and job["settings"]["handedness"] == "right"
    job = wait_job(client, job["id"])
    assert job["status"] == "done", job
    assert job["analysis_rev"] == 1 and job["person_ids"] == ["p00"] and job["ruleset_version"]
    v = job["video"]
    assert v["fps"] == 25.0 and abs(v["n_frames"] - 50) <= 1 and (v["width"], v["height"]) == (320, 240)
    probe = json.loads((settings.data_dir / "jobs" / job["id"] / "video" / "probe.json").read_text())
    vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert abs(float(vs["start_time"])) < 1e-3
    for artifact, model in RESULT_MODELS.items():
        r = client.get(f"/api/jobs/{job['id']}/results/{artifact}")
        assert r.status_code == 200, (artifact, r.text)
        model.model_validate(r.json())
        assert r.json()["analysis_rev"] == 1 and r.headers["x-analysis-rev"] == "1"
    assert client.get("/api/jobs").json()["jobs"][0]["id"] == job["id"]


def test_trim_is_frame_accurate(client: TestClient, tmp_path: Path) -> None:
    clip = _make_clip(tmp_path / "long.mp4", 4.0)
    r = upload(client, clip, trim_start_s=1.0, trim_end_s=2.6)
    assert r.status_code == 201, r.text
    job = wait_job(client, r.json()["id"])
    assert job["status"] == "done"
    assert abs(job["video"]["n_frames"] - round(1.6 * 25)) <= 1


def test_upload_rejections(client: TestClient, clips: dict[str, Path], tmp_path: Path, settings: Settings) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    r = upload(client, txt)
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"

    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(b"\x00" * 4096)
    r = upload(client, bad)
    assert r.status_code == 415 and r.json()["error"]["code"] == "unreadable_media"

    r = upload(client, clips["mp4"], trim_start_s=1.0, trim_end_s=1.0)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = upload(client, clips["mp4"], trim_start_s=5.0)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = client.post("/api/jobs", files={"file": ("a.mp4", b"x")}, data={"handedness": "both"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"

    settings.max_duration_s = 1.0
    r = upload(client, clips["mp4"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "video_too_long"
    assert client.get("/api/jobs").json()["jobs"] == []
    assert list((settings.data_dir / "jobs").iterdir()) == []


def test_video_range_and_result_gzip(client: TestClient, clips: dict[str, Path], settings: Settings) -> None:
    job = wait_job(client, upload(client, clips["mp4"]).json()["id"])
    r = client.get(f"/api/jobs/{job['id']}/video", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert "content-encoding" not in r.headers and r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"

    rev_dir = settings.data_dir / "jobs" / job["id"] / "analysis" / "rev-0001"
    for artifact in ("keypoints", "report"):
        r = client.get(
            f"/api/jobs/{job['id']}/results/{artifact}", headers={"Accept-Encoding": "gzip"}
        )
        assert r.headers["content-encoding"] == "gzip" and r.headers["x-analysis-rev"] == "1"
        assert "Accept-Encoding" in r.headers["vary"]
        assert r.content == (rev_dir / f"{artifact}.json").read_bytes()  # httpx decodes the gzip body
        raw = gzip.decompress((rev_dir / f"{artifact}.json.gz").read_bytes())
        assert raw == (rev_dir / f"{artifact}.json").read_bytes()
        r = client.get(f"/api/jobs/{job['id']}/results/{artifact}", headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in r.headers
        assert r.content == (rev_dir / f"{artifact}.json").read_bytes()

    assert client.get(f"/api/jobs/{job['id']}/results/nope").status_code == 404
    r = client.get(f"/api/jobs/{job['id']}/results/report?rev=7")
    assert r.status_code == 404 and r.json()["error"]["code"] == "rev_not_found"
    assert client.get("/api/jobs/nope").json()["error"]["code"] == "job_not_found"


def test_multi_person_selection_and_revisions(client: TestClient, clips: dict[str, Path], stub) -> None:
    stub["persons"] = 2
    job_id = upload(client, clips["mp4"]).json()["id"]
    job = wait_job(client, job_id)
    assert job["status"] == "awaiting_selection"
    assert client.get(f"/api/jobs/{job_id}/results/report").json()["error"]["code"] == "artifact_not_ready"

    cands = client.get(f"/api/jobs/{job_id}/candidates").json()
    assert sorted(c["person_id"] for c in cands["candidates"]) == ["p00", "p01"]
    assert cands["suggested_person_ids"] == ["p00"]
    preview = cands["candidates"][0]["preview_frame"]
    r = client.get(f"/api/jobs/{job_id}/frames/{preview}.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert client.get(f"/api/jobs/{job_id}/frames/9999.jpg").status_code == 404

    r = client.post(f"/api/jobs/{job_id}/selection", json={"person_ids": ["p09"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_person_id"
    r = client.post(f"/api/jobs/{job_id}/selection", json={"person_ids": ["p00"]})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job = wait_job(client, job_id, {"done", "failed"})
    assert job["status"] == "done" and job["analysis_rev"] == 1 and job["person_ids"] == ["p00"]

    r = client.post(f"/api/jobs/{job_id}/selection", json={"person_ids": ["p01", "p00"]})
    assert r.status_code == 202
    job = wait_job(client, job_id, {"done", "failed"})
    assert job["analysis_rev"] == 2 and job["person_ids"] == ["p01", "p00"]
    assert client.get(f"/api/jobs/{job_id}/results/keypoints").json()["person_ids"] == ["p01", "p00"]
    old = client.get(f"/api/jobs/{job_id}/results/keypoints?rev=1")
    assert old.status_code == 200 and old.json()["person_ids"] == ["p00"] and old.headers["x-analysis-rev"] == "1"
    assert stub["calls"] == 1  # selection never re-runs pose


def test_no_person_and_pose_failure_then_retry(client: TestClient, clips: dict[str, Path], stub) -> None:
    stub["persons"] = 0
    job = wait_job(client, upload(client, clips["mp4"]).json()["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "no_person_detected"

    stub["persons"] = 1
    stub["raise"] = RuntimeError("model exploded")
    job_id = upload(client, clips["mp4"]).json()["id"]
    job = wait_job(client, job_id)
    assert job["status"] == "failed"
    assert job["error"] == {"code": "pose_failed", "stage": "estimating_pose", "message": job["error"]["message"]}
    assert "model exploded" in job["error"]["message"]
    r = client.post(f"/api/jobs/{job_id}/reanalyze")
    assert r.status_code == 409 and r.json()["error"]["code"] == "pose_unavailable"

    stub["raise"] = None
    r = client.post(f"/api/jobs/{job_id}/retry")
    assert r.status_code == 202 and r.json()["resume_from"] == "estimating_pose" and r.json()["error"] is None
    job = wait_job(client, job_id, {"done", "failed"})
    assert job["status"] == "done"
    # the stale work file from the failed run was cleared before the retry
    assert stub["work_files"][-1] == []
    assert client.post(f"/api/jobs/{job_id}/retry").json()["error"]["code"] == "invalid_state"


def test_reanalyze_after_rules_edit(client: TestClient, clips: dict[str, Path], settings: Settings, tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    shutil.copy(settings.rules_path, rules)
    settings.rules_path = rules
    job_id = upload(client, clips["mp4"]).json()["id"]
    job = wait_job(client, job_id)
    assert job["status"] == "done"
    strokes = client.get(f"/api/jobs/{job_id}/results/strokes").json()
    assert strokes["strokes"], "stub motion should yield strokes"
    before = {f["rule_id"] for s in strokes["strokes"] for f in s["feedback"]}
    assert "all.legs.knee_bend" in before  # straight stub legs violate the knee-bend rule

    doc = yaml.safe_load(rules.read_text(encoding="utf-8"))
    for rule in doc["rules"]:
        if rule["id"] == "all.legs.knee_bend":
            rule["max"] = 190.0
    doc["ruleset_version"] = "2026.10.1-test"
    rules.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    r = client.post(f"/api/jobs/{job_id}/reanalyze")
    assert r.status_code == 202
    job = wait_job(client, job_id, {"done", "failed"})
    assert job["analysis_rev"] == 2 and job["ruleset_version"] == "2026.10.1-test"
    strokes = client.get(f"/api/jobs/{job_id}/results/strokes").json()
    after = {f["rule_id"] for s in strokes["strokes"] for f in s["feedback"]}
    assert "all.legs.knee_bend" not in after
    assert strokes["ruleset_version"] == "2026.10.1-test"
    report = client.get(f"/api/jobs/{job_id}/results/report").json()
    assert report["ruleset"]["version"] == "2026.10.1-test"


def test_reanalyze_state_rules(client: TestClient, clips: dict[str, Path], stub) -> None:
    stub["persons"] = 2
    job_id = upload(client, clips["mp4"]).json()["id"]
    wait_job(client, job_id)
    r = client.post(f"/api/jobs/{job_id}/reanalyze")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"
    r = client.post(f"/api/jobs/{job_id}/retry")
    assert r.status_code == 409


def test_rules_and_health(client: TestClient, settings: Settings) -> None:
    r = client.get("/api/rules").json()
    assert r["path"] == str(settings.rules_path) and r["ruleset"]["ruleset_id"] == "default"
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["ffmpeg"] is True and h["default_estimator"] == "stub"
    assert h["estimators"]["stub"]["available"] is True
    assert "sports2d" in h["estimators"]


def test_normalize_command_matches_contract(clips: dict[str, Path]) -> None:
    from ttcoach.media.ffmpeg import normalize_cmd

    cmd = normalize_cmd("ffmpeg", Path("in.mkv"), Path("out.mp4"), fps=50, max_height=720,
                        trim_start_s=30.0, trim_end_s=40.0)  # fmt: skip
    joined = " ".join(cmd)
    assert cmd.index("-ss") < cmd.index("-i") and cmd.index("-to") < cmd.index("-i")
    for part in ("-r 50", "-fps_mode cfr", "-map 0:v:0", "-map 0:a:0?", "-pix_fmt yuv420p", "+faststart"):
        assert part in joined
    assert subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0


def test_upload_too_large_returns_413(client: TestClient, clips: dict[str, Path], settings: Settings) -> None:
    settings.max_upload_bytes = 1000
    r = upload(client, clips["mp4"])
    assert r.status_code == 413 and r.json()["error"]["code"] == "upload_too_large"
    assert list((settings.data_dir / "jobs").iterdir()) == []


def test_unexpected_error_after_upload_leaves_no_job(
    settings: Settings, stub, clips: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ttcoach.api import routes_jobs

    def boom(*a, **kw):
        raise OSError("disk went away")

    monkeypatch.setattr(routes_jobs.ffmpeg, "probe", boom)
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        r = upload(c, clips["mp4"])
        assert r.status_code == 500
        assert c.get("/api/jobs").json()["jobs"] == []
    assert list((settings.data_dir / "jobs").iterdir()) == []


def test_unknown_duration_is_unreadable(
    client: TestClient, clips: dict[str, Path], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ttcoach.api import routes_jobs

    real_probe = routes_jobs.ffmpeg.probe

    def no_duration(*a, **kw):
        doc = real_probe(*a, **kw)
        doc["format"].pop("duration", None)
        for s in doc["streams"]:
            for k in ("duration", "nb_frames", "nb_read_frames"):
                s.pop(k, None)
        return doc

    monkeypatch.setattr(routes_jobs.ffmpeg, "probe", no_duration)
    r = upload(client, clips["mp4"])
    assert r.status_code == 415 and r.json()["error"]["code"] == "unreadable_media"
    assert list((settings.data_dir / "jobs").iterdir()) == []


def test_duration_fallbacks() -> None:
    from ttcoach.media.ffmpeg import duration_s

    v = {"codec_type": "video", "avg_frame_rate": "25/1"}
    assert duration_s({"format": {"duration": "12.5"}, "streams": [v]}) == 12.5
    assert duration_s({"format": {}, "streams": [{**v, "duration": "3.0"}]}) == 3.0
    assert duration_s({"format": {"duration": "0.000"}, "streams": [{**v, "nb_frames": "250"}]}) == 10.0
    assert duration_s({"format": {}, "streams": [v]}) == 0.0


def test_concurrent_frame_requests(client: TestClient, clips: dict[str, Path], settings: Settings) -> None:
    from concurrent.futures import ThreadPoolExecutor

    job = wait_job(client, upload(client, clips["mp4"]).json()["id"])
    frames_dir = settings.data_dir / "jobs" / job["id"] / "video" / "frames"
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.get(f"/api/jobs/{job['id']}/frames/7.jpg"), range(16)))
    assert [r.status_code for r in responses] == [200] * 16
    assert all(r.content == responses[0].content and r.content[:2] == b"\xff\xd8" for r in responses)
    assert sorted(p.name for p in frames_dir.iterdir()) == ["000007.jpg"]  # no leftover temp files


def test_content_length_over_limit_rejected_before_route(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ttcoach.jobs.repository import FileJobRepository

    def must_not_run(*a, **kw):
        raise AssertionError("route body must not be processed")

    monkeypatch.setattr(FileJobRepository, "create", must_not_run)
    settings.max_upload_bytes = 1000
    big = b"\x00" * (2 * 1024 * 1024)
    r = client.post("/api/jobs", files={"file": ("big.mp4", big)}, data={"handedness": "right"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "upload_too_large"
    assert list((settings.data_dir / "jobs").iterdir()) == []
