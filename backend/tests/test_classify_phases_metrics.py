import numpy as np
import pytest
from tests.fixtures.synthetic import standing_pose, swing_pose

from ttcoach.coaching import analyze
from ttcoach.coaching.classify import classify_stroke
from ttcoach.coaching.models import METRIC_UNITS, ClassificationConfig, RuleSet
from ttcoach.segmentation import Segment
from ttcoach.kinematics.body import J, torso_len

FPS = 50.0
CONTACTS_S = [1.0, 2.5]
N = int(4 * FPS)
EMPTY_RULES = RuleSet(ruleset_id="t", ruleset_version="0")


def _run(xy, handedness="right", ruleset=EMPTY_RULES):
    return analyze(xy, np.ones(xy.shape[:2]), FPS, handedness, ruleset)


@pytest.mark.parametrize(
    "handedness,side,expected",
    [
        ("right", 1, "FH"),
        ("right", -1, "BH"),
        ("left", 1, "FH"),
        ("left", -1, "BH"),
    ],
)
def test_fh_bh_by_lateral_offset(handedness, side, expected):
    xy = swing_pose(N, FPS, CONTACTS_S, handedness=handedness, contact_side=side)
    strokes = _run(xy, handedness).strokes
    assert [s.type for s in strokes] == [expected] * 2
    assert all(s.type_confidence > 0.3 for s in strokes)


def test_handedness_flips_classification():
    right = swing_pose(N, FPS, CONTACTS_S, handedness="right", contact_side=1)
    left = swing_pose(N, FPS, CONTACTS_S, handedness="left", contact_side=1)
    assert _run(right, "right").strokes[0].type == "FH"
    assert _run(left, "left").strokes[0].type == "FH"
    assert _run(left, "right").strokes == []  # the right wrist never moves in the left-hander clip


def test_side_on_fallback_uses_elbow_angle():
    xy = standing_pose(60, width_ratio=0.2)
    seg = Segment(10, 30, 50)
    cfg = ClassificationConfig()
    torso = torso_len(xy)
    bent = np.full(60, 60.0)
    straight = np.full(60, 170.0)
    assert classify_stroke(xy, bent, seg, "right", torso, cfg) == ("BH", 0.3)
    assert classify_stroke(xy, straight, seg, "right", torso, cfg) == ("FH", 0.3)


def test_fixed_feet_zero_displacement():
    res = _run(swing_pose(N, FPS, CONTACTS_S))
    for s in res.strokes:
        assert s.metrics["foot_displacement_max"].value < 0.01
        assert s.metrics["hip_sway"].value < 0.01


def test_moving_feet_detected():
    xy = swing_pose(N, FPS, CONTACTS_S)
    shift = np.zeros(N)
    shift[int(0.9 * FPS) :] = 100.0  # step during the first stroke window
    for name in ("r_ankle", "r_heel", "r_big_toe"):
        xy[:, J[name], 0] += shift
    m = _run(xy).strokes[0].metrics["foot_displacement_max"].value
    assert m > 0.05


def test_metrics_catalog_and_units():
    res = _run(swing_pose(N, FPS, CONTACTS_S))
    s = res.strokes[0]
    assert set(s.metrics) <= set(METRIC_UNITS)
    assert all(m.unit == METRIC_UNITS[k] for k, m in s.metrics.items())
    assert s.metrics["wrist_peak_speed"].value > 4.0
    assert s.metrics["backswing_duration_s"].value == pytest.approx((s.contact_frame - s.start_frame) / FPS)
    elbow = next(a for a in res.angles.series if a.name == "r_elbow").values
    assert s.metrics["elbow_angle_at_contact"].value == pytest.approx(elbow[s.contact_frame], abs=0.01)
    assert s.metrics["knee_angle_min_dom"].value == pytest.approx(180.0, abs=1.0)


def test_stroke_view_class_and_unavailable_metric_omitted():
    xy = swing_pose(N, FPS, CONTACTS_S)
    xy[:, J["r_knee"]] = np.nan  # knee unavailable -> frames invalid, knee metrics omitted
    res = _run(xy)
    for s in res.strokes:
        assert "knee_angle_min_dom" not in s.metrics
        assert s.view_class == "unknown"  # no valid frames in window
    assert res.quality.valid_frame_ratio == 0.0


def test_empty_when_all_nan():
    xy = np.full((N, 26, 2), np.nan)
    res = _run(xy)
    assert res.strokes == [] and res.summary.n_strokes == 0
    assert res.quality.torso_len_px is None


def test_standing_pose_has_no_strokes():
    assert _run(standing_pose(N)).strokes == []


def test_circular_range_across_wrap():
    from ttcoach.coaching.metrics import circular_range

    assert circular_range(np.array([175.0, 179.0, -178.0, -175.0])) == pytest.approx(10.0)
    assert circular_range(np.array([-10.0, np.nan, 20.0])) == pytest.approx(30.0)
    assert np.isnan(circular_range(np.array([np.nan])))
