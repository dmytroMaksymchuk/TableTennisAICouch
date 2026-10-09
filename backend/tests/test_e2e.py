from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ttcoach.api.app import create_app
from ttcoach.config import Settings
from ttcoach.tools.e2e import frame_numbers, main


@pytest.fixture
def client(settings: Settings, stub) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


def test_upload_select_check_and_render(client: TestClient, clips, stub, tmp_path: Path, capsys) -> None:
    stub["persons"] = 2
    out = tmp_path / "e2e"
    rc = main(
        ["--video", str(clips["mkv"]), "--handedness", "right", "--trim-start", "0.2", "--trim-end", "1.8",
         "--person", "auto", "--render-frames", "first,mid,last", "--out", str(out), "--poll-interval", "0.05"],
        client=client,
    )  # fmt: skip
    text = capsys.readouterr().out
    assert rc == 0, text
    assert "selecting ['p00']" in text and "frame_drift: 0" in text and "E2E OK" in text
    pngs = sorted(p.name for p in out.glob("*.png"))
    assert len(pngs) == 3 and all((out / p).read_bytes()[:4] == b"\x89PNG" for p in pngs)

    job_id = client.get("/api/jobs").json()["jobs"][0]["id"]
    assert main(["--job", job_id, "--check-only"], client=client) == 0


def test_explicit_person_and_failures(client: TestClient, clips, stub, capsys) -> None:
    stub["persons"] = 2
    rc = main(["--video", str(clips["mp4"]), "--person", "p01", "--poll-interval", "0.05"], client=client)
    assert rc == 0
    job = client.get("/api/jobs").json()["jobs"][0]
    assert job["person_ids"] == ["p01"]

    stub["persons"] = 0
    rc = main(["--video", str(clips["mp4"]), "--poll-interval", "0.05"], client=client)
    assert rc == 1 and "no_person_detected" in capsys.readouterr().err
    assert main(["--job", "nope", "--check-only"], client=client) == 1


def test_frame_spec() -> None:
    assert frame_numbers("first,mid,last", 100) == [0, 50, 99]
    assert frame_numbers("3, 7", 10) == [3, 7]
    with pytest.raises(Exception):
        frame_numbers("10", 10)
    with pytest.raises(SystemExit):
        main(["--check-only"])
