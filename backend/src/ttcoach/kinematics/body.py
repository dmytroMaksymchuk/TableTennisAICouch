"""Body-frame helpers over dense keypoint arrays ``xy[n_frames, n_joints, 2]`` (DESIGN 4.1)."""

from __future__ import annotations

import numpy as np

# Same order as ttcoach.pose.schema.CANONICAL_JOINTS (DESIGN 4.1); kept local so kinematics stays pure.
JOINTS: tuple[str, ...] = (
    "nose", "l_eye", "r_eye", "l_ear", "r_ear",
    "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
    "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle",
    "head", "neck", "mid_hip",
    "l_big_toe", "r_big_toe", "l_small_toe", "r_small_toe", "l_heel", "r_heel",
)
J = {name: i for i, name in enumerate(JOINTS)}

REQUIRED_JOINTS: tuple[str, ...] = (
    "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
    "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle",
)

_REQUIRED_IDX = [J[n] for n in REQUIRED_JOINTS]


def sides(handedness: str) -> tuple[str, str]:
    """(dominant, non-dominant) joint-name prefixes, e.g. ("r", "l") for a right-hander."""
    if handedness not in ("right", "left"):
        raise ValueError(f"handedness must be 'right' or 'left', got {handedness!r}")
    return ("r", "l") if handedness == "right" else ("l", "r")


def derive_neck_mid_hip(xy: np.ndarray) -> np.ndarray:
    """Copy of ``xy`` with NaN ``neck`` / ``mid_hip`` synthesised from shoulders / hips."""
    out = np.array(xy, dtype=np.float64, copy=True)
    for target, a, b in (("neck", "l_shoulder", "r_shoulder"), ("mid_hip", "l_hip", "r_hip")):
        mid = (out[:, J[a]] + out[:, J[b]]) / 2.0
        missing = np.isnan(out[:, J[target]]).any(axis=-1)
        out[missing, J[target]] = mid[missing]
    return out


def valid_frames(xy: np.ndarray) -> np.ndarray:
    """Boolean [n]: every required joint present."""
    return ~np.isnan(xy[:, _REQUIRED_IDX]).any(axis=(1, 2))


def torso_len(xy: np.ndarray) -> float:
    """Median |neck - mid_hip| (px) over frames where both exist; NaN when there are none."""
    d = np.linalg.norm(xy[:, J["neck"]] - xy[:, J["mid_hip"]], axis=-1)
    d = d[~np.isnan(d)]
    return float(np.median(d)) if d.size else float("nan")
