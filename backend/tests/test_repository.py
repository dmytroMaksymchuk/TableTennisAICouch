from __future__ import annotations

import io
import os
import threading

import pytest

from ttcoach.errors import JobNotFound
from ttcoach.jobs.models import JobSettings
from ttcoach.jobs.repository import FileJobRepository


def test_create_and_reload(tmp_path) -> None:
    repo = FileJobRepository(tmp_path)
    job = repo.create(JobSettings(handedness="left", trim_start_s=1.0), io.BytesIO(b"x" * 3_000_000), "My Clip.MKV")
    assert job.status == "queued" and job.resume_from == "normalizing"
    assert repo.input_path(job.id).name == "original.mkv"
    assert repo.input_path(job.id).stat().st_size == 3_000_000

    again = FileJobRepository(tmp_path)  # AC3: a new repository instance sees the same job
    assert again.get(job.id) == job
    assert [j.id for j in again.list()] == [job.id]


def test_update_is_atomic_and_persisted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FileJobRepository(tmp_path)
    job = repo.create(JobSettings(handedness="right"), io.BytesIO(b"x"), "a.mp4")
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((str(a), str(b))), real_replace(a, b))[1])
    repo.update(job.id, status="running", stage="estimating_pose", progress=0.5)
    assert replaced and replaced[-1][0].endswith("job.json.tmp") and replaced[-1][1].endswith("job.json")
    got = FileJobRepository(tmp_path).get(job.id)
    assert (got.status, got.stage, got.progress) == ("running", "estimating_pose", 0.5)


def test_concurrent_updates_are_serialized(tmp_path) -> None:
    repo = FileJobRepository(tmp_path)
    job = repo.create(JobSettings(handedness="right"), io.BytesIO(b"x"), "a.mp4")
    errors: list[BaseException] = []

    def bump(i: int) -> None:
        try:
            for k in range(20):
                repo.update(job.id, progress=(i * 20 + k) / 1000)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=bump, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert 0 <= FileJobRepository(tmp_path).get(job.id).progress < 0.08


def test_list_newest_first_and_missing(tmp_path) -> None:
    repo = FileJobRepository(tmp_path)
    a = repo.create(JobSettings(handedness="right"), io.BytesIO(b"x"), "a.mp4")
    b = repo.create(JobSettings(handedness="right"), io.BytesIO(b"x"), "b.mp4")
    repo.update(a.id, created_at="2020-01-01T00:00:00Z")
    assert [j.id for j in repo.list()] == [b.id, a.id]
    with pytest.raises(JobNotFound):
        repo.get("nope")
    with pytest.raises(JobNotFound):
        repo.get("../etc")


def test_json_helpers(tmp_path) -> None:
    repo = FileJobRepository(tmp_path)
    job = repo.create(JobSettings(handedness="right"), io.BytesIO(b"x"), "a.mp4")
    repo.write_json(job.id, "pose/selection.json", {"person_ids": ["p00"], "selected_by": "auto"})
    assert repo.read_json(job.id, "pose/selection.json")["person_ids"] == ["p00"]
    assert not list(repo.path(job.id, "pose").glob("*.tmp"))


def test_id_collision_never_touches_other_upload(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = FileJobRepository(tmp_path)
    # Another request has just claimed "20260101T000000-aaaaaa" and is still streaming (no job.json yet).
    other = repo.root / "20260101T000000-aaaaaa"
    (other / "input").mkdir(parents=True)
    (other / "input" / "original.mp4").write_bytes(b"in flight")
    ids = iter(["20260101T000000-aaaaaa", "20260101T000000-bbbbbb"])
    monkeypatch.setattr(FileJobRepository, "new_id", staticmethod(lambda: next(ids)))

    job = repo.create(JobSettings(handedness="right"), io.BytesIO(b"mine"), "a.mp4")
    assert job.id == "20260101T000000-bbbbbb"
    assert (other / "input" / "original.mp4").read_bytes() == b"in flight"

    # a failing create cleans up only its own dir
    ids2 = iter(["20260101T000000-aaaaaa", "20260101T000000-cccccc"])
    monkeypatch.setattr(FileJobRepository, "new_id", staticmethod(lambda: next(ids2)))

    def fail(job_id, path):
        raise ValueError("invalid")

    with pytest.raises(ValueError):
        repo.create(JobSettings(handedness="right"), io.BytesIO(b"x"), "a.mp4", before_commit=fail)
    assert (other / "input" / "original.mp4").read_bytes() == b"in flight"
    assert not (repo.root / "20260101T000000-cccccc").exists()
