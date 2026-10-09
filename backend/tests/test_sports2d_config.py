"""Generated Sports2D config must stay headless and avoid known Sports2D 0.8.34 crash paths."""

from __future__ import annotations

from pathlib import Path

from ttcoach.pose.base import VideoMeta
from ttcoach.pose.sports2d_adapter import build_config


def _config() -> dict:
    video = VideoMeta(path=Path("C:/data/jobs/x/video/normalized.mp4"), fps=50.0, n_frames=500,
                      width=1280, height=720, duration_s=10.0)
    return build_config(video, Path("C:/data/jobs/x/pose/sports2d/out"), "balanced", "auto")


def test_angle_flags_avoid_sports2d_person_deletion_crash() -> None:
    # calculate_angles=False -> TypeError at process.py:2117 when a short-lived person is deleted;
    # save_angles=False with calculate_angles=True -> IndexError in make_homogeneous([]).
    base = _config()["base"]
    assert base["calculate_angles"] is True
    assert base["save_angles"] is True


def test_config_is_headless() -> None:
    cfg = _config()
    assert cfg["base"]["show_realtime_results"] is False
    assert cfg["base"]["person_ordering_method"] == "highest_likelihood"
    assert cfg["base"]["save_vid"] is False and cfg["base"]["save_img"] is False
    assert cfg["post-processing"]["show_graphs"] is False
