from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tests.conftest import FIXTURES
from ttcoach.errors import PipelineError
from ttcoach.pose.align import align_pose
from ttcoach.pose.base import VideoMeta
from ttcoach.pose.schema import CANONICAL_JOINTS, JOINT_INDEX
from ttcoach.pose.sports2d_adapter import collect_tracks, trc_to_track
from ttcoach.pose.sports2d_names import SPORTS2D_TO_CANONICAL
from ttcoach.pose.trc import parse_trc

FIXTURE = FIXTURES / "sports2d_sample_px_person00.trc"
MARKERS = ["Hip", "RHip", "RKnee", "RAnkle", "RBigToe", "RSmallToe", "RHeel", "LHip", "LKnee", "LAnkle",
           "LBigToe", "LSmallToe", "LHeel", "Neck", "Head", "Nose", "RShoulder", "RElbow", "RWrist",
           "LShoulder", "LElbow", "LWrist"]  # fmt: skip


def write_trc(path: Path, frames: list[int], fps: float, *, base: int = 0, empty_rows: set[int] = frozenset(),
              num_frames: int | None = None, time_offset: float = 0.0) -> Path:  # fmt: skip
    """Sports2D-shaped px TRC; marker m of frame f at (100 + m, 50 + f)."""
    n_m = len(MARKERS)
    lines = [
        f"PathFileType\t4\t(X/Y/Z)\t{path.name}",
        "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames",
        f"{fps}\t{fps}\t{num_frames or len(frames)}\t{n_m}\tm\t{fps}\t0\t{num_frames or len(frames)}",
        "Frame#\tTime\t" + "\t\t\t".join(MARKERS) + "\t\t\t",
        "\t\t" + "\t".join(f"X{i}\tY{i}\tZ{i}" for i in range(1, n_m + 1)),
    ]
    for f in frames:
        t = f / fps + time_offset
        if f in empty_rows:
            cells = [""] * (3 * n_m)
        else:
            cells = [v for m in range(n_m) for v in (f"{100 + m}.5", f"{50 + f}.25", "0.0")]
            cells[3 * 5 : 3 * 5 + 3] = ["", "", ""]  # RSmallToe missing in every row
        lines.append(f"{f + base}\t{t:.4f}\t" + "\t".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_fixture_parses_to_canonical() -> None:
    trc = parse_trc(FIXTURE)
    assert trc.markers == tuple(MARKERS)
    assert trc.num_frames == 50 and trc.data_rate == 50.0
    assert set(trc.markers) <= set(SPORTS2D_TO_CANONICAL)
    track = trc_to_track(trc, "p00", 50.0)
    assert track.xy.shape == (len(track.frame_idx), len(CANONICAL_JOINTS), 2)
    assert len(CANONICAL_JOINTS) == 26
    assert track.frame_idx[0] == 0 and (np.diff(track.frame_idx) > 0).all()
    pts = track.xy[~np.isnan(track.xy).any(axis=2)]
    assert len(pts) > 0
    assert (pts[:, 0] >= 0).all() and (pts[:, 0] <= 1280).all()
    assert (pts[:, 1] >= 0).all() and (pts[:, 1] <= 720).all()
    # y down: ankles below hips in image coordinates
    assert np.nanmean(track.xy[:, JOINT_INDEX["r_ankle"], 1]) > np.nanmean(track.xy[:, JOINT_INDEX["r_hip"], 1])
    for eye in ("l_eye", "r_eye", "l_ear", "r_ear"):
        assert np.isnan(track.xy[:, JOINT_INDEX[eye]]).all()
    assert set(np.unique(track.conf)) <= {0.0, 1.0}


def test_empty_cells_are_missing_and_empty_rows_dropped(tmp_path: Path) -> None:
    path = write_trc(tmp_path / "x_px_person00.trc", list(range(10)), 50.0, empty_rows={3, 4})
    track = trc_to_track(parse_trc(path), "p00", 50.0)
    assert track.frame_idx.tolist() == [0, 1, 2, 5, 6, 7, 8, 9]
    assert np.isnan(track.xy[:, JOINT_INDEX["r_small_toe"]]).all()
    assert track.conf[:, JOINT_INDEX["r_small_toe"]].max() == 0.0
    assert track.xy[0, JOINT_INDEX["mid_hip"]].tolist() == [100.5, 50.25]


def test_frame_base_one(tmp_path: Path) -> None:
    path = write_trc(tmp_path / "x_px_person00.trc", list(range(5)), 50.0, base=1)
    track = trc_to_track(parse_trc(path), "p00", 50.0, frame_base=1)
    assert track.frame_idx.tolist() == [0, 1, 2, 3, 4]


def test_time_frame_mismatch_fails(tmp_path: Path) -> None:
    path = write_trc(tmp_path / "x_px_person00.trc", list(range(5)), 50.0, time_offset=0.1)  # 5 frames off
    with pytest.raises(PipelineError) as e:
        trc_to_track(parse_trc(path), "p00", 50.0)
    assert e.value.code == "pose_failed"


@pytest.mark.parametrize(
    ("trc_frames", "expect_drift", "fails"),
    [(500, 0, False), (498, 0, False), (496, 4, False), (494, None, True)],
)
def test_collect_and_align_drift_cases(tmp_path: Path, trc_frames: int, expect_drift, fails: bool) -> None:
    out = tmp_path / "out" / "clip_Sports2D"
    out.mkdir(parents=True)
    write_trc(out / "clip_Sports2D_px_person00.trc", list(range(trc_frames)), 50.0)
    write_trc(out / "clip_Sports2D_px_person01.trc", list(range(0, trc_frames, 2)), 50.0, num_frames=trc_frames)
    tracks, n = collect_tracks(tmp_path / "out", 50.0)
    assert [t.person_id for t in tracks] == ["p00", "p01"] and n == trc_frames
    from ttcoach.pose.schema import RawPose

    raw = RawPose("sports2d", "x", CANONICAL_JOINTS, tuple(tracks), n, "presence")
    meta = VideoMeta(Path("v.mp4"), 50.0, 500, 1280, 720, 10.0)
    if fails:
        with pytest.raises(PipelineError):
            align_pose(raw, meta)
        return
    seq = align_pose(raw, meta)
    assert seq.frame_drift == expect_drift
    p1 = seq.person("p01")
    assert not np.isnan(p1.xy[2, JOINT_INDEX["mid_hip"]]).any()
    assert np.isnan(p1.xy[3]).all()  # missing rows stay NaN, nothing shifted
    assert p1.xy[4, JOINT_INDEX["mid_hip"], 1] == pytest.approx(54.25)
