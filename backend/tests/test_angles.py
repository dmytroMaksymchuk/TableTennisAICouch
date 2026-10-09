import numpy as np
import pytest
from tests.fixtures.synthetic import place_angle, standing_pose

from ttcoach.kinematics.angles import SERIES_NAMES, angular_velocity, compute_angles, interior_angle
from ttcoach.kinematics.body import J, JOINTS, derive_neck_mid_hip, sides, torso_len, valid_frames
from ttcoach.kinematics.smoothing import smooth_xy


def test_joint_order_matches_schema():
    schema = pytest.importorskip("ttcoach.pose.schema")
    assert tuple(schema.CANONICAL_JOINTS) == JOINTS


def test_series_names():
    xy = standing_pose(3)
    assert tuple(compute_angles(xy)) == SERIES_NAMES


@pytest.mark.parametrize("side", ["l", "r"])
@pytest.mark.parametrize(
    "kind,triple",
    [
        ("elbow", ("shoulder", "elbow", "wrist")),
        ("shoulder", ("hip", "shoulder", "elbow")),
        ("hip", ("shoulder", "hip", "knee")),
        ("knee", ("hip", "knee", "ankle")),
    ],
)
@pytest.mark.parametrize("theta", [0.0, 90.0, 180.0])
def test_exact_interior_angles(side, kind, triple, theta):
    xy = standing_pose(1)
    place_angle(xy, *(f"{side}_{j}" for j in triple), theta)
    assert compute_angles(xy)[f"{side}_{kind}"][0] == pytest.approx(theta, abs=1e-6)


def test_trunk_lean_sign():
    xy = standing_pose(3)
    xy[:, J["mid_hip"]] = (500, 500)
    xy[0, J["neck"]] = (500, 400)  # upright
    xy[1, J["neck"]] = (600, 400)  # leaning to +x
    xy[2, J["neck"]] = (400, 400)  # leaning to -x
    lean = compute_angles(xy)["trunk_lean"]
    assert lean == pytest.approx([0.0, 45.0, -45.0])


def test_shoulder_hip_separation_and_wrap():
    xy = standing_pose(3)

    def line(a, b, deg, length=200.0):
        r = np.radians(deg)
        xy[:, J[a]] = (500, 500)
        xy[:, J[b]] = (500 + length * np.cos(r), 500 + length * np.sin(r))

    line("r_shoulder", "l_shoulder", 30)
    line("r_hip", "l_hip", 0)
    assert compute_angles(xy)["shoulder_hip_separation"][0] == pytest.approx(30.0)
    line("r_shoulder", "l_shoulder", 170)
    line("r_hip", "l_hip", -170)
    assert compute_angles(xy)["shoulder_hip_separation"][0] == pytest.approx(-20.0)


def test_nan_propagation_and_short_vector():
    xy = standing_pose(4)
    xy[1, J["r_wrist"]] = np.nan
    xy[2, J["r_wrist"]] = xy[2, J["r_elbow"]]  # zero-length vector
    a = compute_angles(xy)
    assert np.isnan(a["r_elbow"][1]) and np.isnan(a["r_elbow"][2])
    assert not np.isnan(a["r_elbow"][0]) and not np.isnan(a["l_elbow"][1])
    assert np.isnan(interior_angle(np.array([np.nan, 0.0]), np.zeros(2), np.array([1.0, 1.0])))


def test_sinusoidal_elbow_accuracy_and_velocity():
    fps, n = 50.0, 200
    t = np.arange(n) / fps
    theta = 90 + 45 * np.sin(2 * np.pi * 1.0 * t)
    xy = standing_pose(n)
    place_angle(xy, "r_shoulder", "r_elbow", "r_wrist", theta)
    # unsmoothed angle within 0.5 deg of truth
    assert np.abs(compute_angles(xy)["r_elbow"] - theta).max() < 0.5
    # smoothed velocity within 3 deg/s RMS of the analytic derivative, excluding 5 edge frames
    ang = compute_angles(smooth_xy(xy, fps))["r_elbow"]
    vel = angular_velocity(ang, fps)
    truth = 45 * 2 * np.pi * np.cos(2 * np.pi * t)
    err = (vel - truth)[5:-5]
    assert np.sqrt(np.mean(err**2)) < 3.0


def test_angular_velocity_nan_and_wrap():
    a = np.array([170.0, 175.0, np.nan, 10.0, 15.0, 20.0])
    v = angular_velocity(a, 10.0)
    assert np.isnan(v[2])
    assert v[3:] == pytest.approx([50.0, 50.0, 50.0])
    wrap = angular_velocity(np.array([170.0, 180.0, -170.0, -160.0]), 1.0)
    assert wrap == pytest.approx([10.0] * 4)


def test_body_helpers():
    assert sides("right") == ("r", "l") and sides("left") == ("l", "r")
    with pytest.raises(ValueError):
        sides("both")
    xy = standing_pose(5)
    xy[0, J["neck"]] = np.nan
    xy[:, J["mid_hip"]] = np.nan
    d = derive_neck_mid_hip(xy)
    assert d[0, J["neck"]] == pytest.approx((640, 300))
    assert torso_len(d) == pytest.approx(200.0)
    assert valid_frames(d).all()
    xy[2, J["l_knee"]] = np.nan
    assert valid_frames(xy).tolist() == [True, True, False, True, True]
