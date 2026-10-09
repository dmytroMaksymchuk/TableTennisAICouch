from __future__ import annotations

import pytest

from ttcoach.config import REPO_ROOT, Settings
from ttcoach.timebase import frame_to_seek_time, frame_to_time, time_to_frame


@pytest.mark.parametrize("cwd", [REPO_ROOT, REPO_ROOT / "backend"])
def test_paths_independent_of_cwd(monkeypatch: pytest.MonkeyPatch, cwd) -> None:
    monkeypatch.chdir(cwd)
    s = Settings()
    assert s.data_dir == REPO_ROOT / "data"
    assert s.rules_path == REPO_ROOT / "backend" / "rules" / "default.yaml"
    assert s.data_dir.is_absolute() and s.data_dir.parent.exists()
    assert s.rules_path.parent.parent.exists()  # backend/


def test_rules_file_exists_from_both_cwds(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = []
    for cwd in (REPO_ROOT, REPO_ROOT / "backend"):
        monkeypatch.chdir(cwd)
        s = Settings()
        if not s.rules_path.exists():
            pytest.fail(f"rules file missing at {s.rules_path} (cwd={cwd})")
        resolved.append((s.rules_path, s.data_dir))
    assert resolved[0] == resolved[1]


def test_env_override_and_relative_paths(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TTCOACH_DATA_DIR", "somewhere/data")
    monkeypatch.setenv("TTCOACH_MAX_DURATION_S", "12")
    monkeypatch.setenv("TTCOACH_ESTIMATOR_OPTIONS", '{"sports2d": {"mode": "lightweight"}}')
    s = Settings()
    assert s.data_dir == (REPO_ROOT / "somewhere" / "data").resolve()
    assert s.max_duration_s == 12
    assert s.options_for("sports2d") == {"mode": "lightweight"}
    assert s.options_for("other") == {}


@pytest.mark.parametrize("fps", [25.0, 29.97, 30.0, 50.0, 60.0])
def test_frame_time_rule(fps: float) -> None:
    n = 300
    for f in range(n):
        assert time_to_frame(frame_to_time(f, fps), fps, n) == f
        assert time_to_frame(frame_to_seek_time(f, fps), fps, n) == f
    assert time_to_frame(-1.0, fps, n) == 0
    assert time_to_frame(1e6, fps, n) == n - 1
