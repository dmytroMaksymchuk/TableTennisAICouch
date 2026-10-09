import numpy as np
import pytest
from tests.fixtures.synthetic import standing_pose, swing_pose

from ttcoach.coaching import analyze
from ttcoach.coaching.models import VIEW_NOTICE, RuleSet, ViewConfig
from ttcoach.coaching.view import classify_view, view_info, width_ratio
from ttcoach.kinematics.body import J, torso_len

CFG = ViewConfig()
FPS = 50.0


def test_side_view_has_no_notice():
    xy = standing_pose(30, width_ratio=0.2)
    info = view_info(xy, torso_len(xy), CFG)
    assert info.class_ == "side" and info.side_on is True
    assert info.notice is None
    assert info.shoulder_width_ratio == pytest.approx(0.2)


def test_front_view_has_notice():
    xy = standing_pose(30, width_ratio=0.8)
    info = view_info(xy, torso_len(xy), CFG)
    assert info.class_ == "front_or_back" and info.side_on is False
    assert info.notice == VIEW_NOTICE
    assert info.model_dump(by_alias=True)["class"] == "front_or_back"


def test_oblique_boundaries():
    assert classify_view(0.5, 1.0, CFG) == "oblique"
    assert classify_view(0.35, 1.0, CFG) == "oblique"
    assert classify_view(0.60, 1.0, CFG) == "oblique"
    assert classify_view(0.34, 1.0, CFG) == "side"
    assert classify_view(0.61, 1.0, CFG) == "front_or_back"


def test_unknown_when_few_valid_frames_or_no_ratio():
    assert classify_view(0.5, 0.4, CFG) == "unknown"
    assert classify_view(None, 1.0, CFG) == "unknown"
    xy = np.full((10, 26, 2), np.nan)
    info = view_info(xy, float("nan"), CFG)
    assert info.class_ == "unknown" and info.notice is not None and info.shoulder_width_ratio is None


def test_hip_width_averaged_in():
    xy = standing_pose(10, width_ratio=0.4)
    xy[:, J["l_hip"], 0] = 640 + 0.6 * 200 / 2
    xy[:, J["r_hip"], 0] = 640 - 0.6 * 200 / 2
    ratio, valid = width_ratio(xy, 200.0)
    assert valid == 1.0 and ratio == pytest.approx(0.5)


def test_per_stroke_view_class_set_and_report_view():
    n = int(4 * FPS)
    for ratio, cls in ((0.2, "side"), (0.8, "front_or_back")):
        xy = swing_pose(n, FPS, [1.0, 2.5], width_ratio=ratio)
        res = analyze(xy, np.ones(xy.shape[:2]), FPS, "right", RuleSet(ruleset_id="t", ruleset_version="0"))
        assert res.view.class_ == cls
        assert res.strokes and all(s.view_class == cls for s in res.strokes)
        assert (res.view.notice is None) == (cls == "side")
