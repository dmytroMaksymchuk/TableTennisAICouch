from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ttcoach.errors import PipelineError
from ttcoach.pose.align import align_pose
from ttcoach.pose.base import VideoMeta
from ttcoach.pose.schema import CANONICAL_JOINTS, RawPose, RawTrack

J = len(CANONICAL_JOINTS)


def track(frames, pid: str = "p00") -> RawTrack:
    idx = np.asarray(frames, dtype=np.int64)
    xy = np.zeros((len(idx), J, 2), dtype=np.float32)
    xy[:, :, 0] = idx[:, None]  # x encodes the source frame index
    xy[:, :, 1] = 7.0
    return RawTrack(pid, idx, xy, np.ones((len(idx), J), dtype=np.float32))


def raw(tracks, source_n: int) -> RawPose:
    return RawPose("stub", "1", CANONICAL_JOINTS, tuple(tracks), source_n, "presence")


def meta(n: int = 500) -> VideoMeta:
    return VideoMeta(Path("v.mp4"), 50.0, n, 640, 480, n / 50.0)


def test_drift_0_exact() -> None:
    seq = align_pose(raw([track(range(500))], 500), meta())
    assert seq.frame_drift == 0
    x = seq.persons[0].xy[:, 0, 0]
    assert (x == np.arange(500)).all()
    assert seq.n_frames == 500 and seq.persons[0].xy.shape == (500, J, 2)


def test_drift_2_silent_and_padded(caplog: pytest.LogCaptureFixture) -> None:
    logs: list[str] = []
    seq = align_pose(raw([track(range(498))], 498), meta(), logs.append)
    assert seq.frame_drift == 0 and not logs
    assert np.isnan(seq.persons[0].xy[498:]).all()
    assert (seq.persons[0].conf[498:] == 0).all()


def test_drift_4_recorded_and_warned() -> None:
    logs: list[str] = []
    seq = align_pose(raw([track(range(496))], 496), meta(), logs.append)
    assert seq.frame_drift == 4
    assert any("WARNING" in m and "drift 4" in m for m in logs)


def test_drift_6_fails() -> None:
    with pytest.raises(PipelineError) as e:
        align_pose(raw([track(range(494))], 494), meta())
    assert e.value.code == "pose_failed" and "frame drift 6" in e.value.message


def test_drift_15_of_2000_warns_only() -> None:
    logs: list[str] = []
    seq = align_pose(raw([track(range(1985))], 1985), meta(2000), logs.append)
    assert seq.frame_drift == 15 and logs


def test_out_of_range_index_counts_as_drift() -> None:
    # source claims 500 frames but rows reach index 503 -> drift 4, rows >= n dropped
    logs: list[str] = []
    seq = align_pose(raw([track(range(504))], 500), meta(), logs.append)
    assert seq.frame_drift == 4
    assert seq.persons[0].xy[499, 0, 0] == 499


def test_gap_in_frames_not_shifted() -> None:
    frames = [f for f in range(500) if not 100 <= f < 110]
    seq = align_pose(raw([track(frames)], 500), meta())
    xy = seq.persons[0].xy
    assert np.isnan(xy[100:110]).all()
    assert xy[110, 0, 0] == 110 and xy[99, 0, 0] == 99


def test_duplicate_index_last_row_wins() -> None:
    t = track([0, 1, 1, 2])
    t.xy[2, :, 1] = 99.0  # second row for frame 1
    logs: list[str] = []
    seq = align_pose(raw([t], 500), meta(500), logs.append)
    assert seq.persons[0].xy[1, 0, 1] == 99.0
    assert any("duplicate" in m for m in logs)


def test_joint_order_remapped() -> None:
    joints = tuple(reversed(CANONICAL_JOINTS))
    t = track(range(500))
    t.xy[:, 0, 1] = 123.0  # first source joint = last canonical joint
    r = RawPose("stub", "1", joints, (t,), 500, "score")
    seq = align_pose(r, meta())
    assert seq.persons[0].xy[0, J - 1, 1] == 123.0
