"""Joint angle series (plan 4.2). Image coordinates: origin top-left, y down."""

from __future__ import annotations

import numpy as np

from .body import J

# name -> (label, definition)
SERIES_META: dict[str, tuple[str, str]] = {
    "l_elbow": ("Left elbow flexion", "interior angle l_shoulder-l_elbow-l_wrist; 180 = straight"),
    "r_elbow": ("Right elbow flexion", "interior angle r_shoulder-r_elbow-r_wrist; 180 = straight"),
    "l_shoulder": ("Left shoulder angle", "interior angle l_hip-l_shoulder-l_elbow; 0 = arm along trunk"),
    "r_shoulder": ("Right shoulder angle", "interior angle r_hip-r_shoulder-r_elbow; 0 = arm along trunk"),
    "l_hip": ("Left hip angle", "interior angle l_shoulder-l_hip-l_knee; 180 = straight"),
    "r_hip": ("Right hip angle", "interior angle r_shoulder-r_hip-r_knee; 180 = straight"),
    "l_knee": ("Left knee flexion", "interior angle l_hip-l_knee-l_ankle; 180 = straight"),
    "r_knee": ("Right knee flexion", "interior angle r_hip-r_knee-r_ankle; 180 = straight"),
    "trunk_lean": (
        "Trunk lean",
        "signed angle of mid_hip->neck vs image up; + = trunk top toward image +x",
    ),
    "shoulder_hip_separation": (
        "Shoulder-hip separation",
        "angle(r_shoulder->l_shoulder) - angle(r_hip->l_hip), each vs image +x, wrapped; "
        "2D proxy, low reliability",
    ),
}
SERIES_NAMES: tuple[str, ...] = tuple(SERIES_META)

_TRIPLES = {  # (a, b, c): interior angle at b between b->a and b->c
    "elbow": ("shoulder", "elbow", "wrist"),
    "shoulder": ("hip", "shoulder", "elbow"),
    "hip": ("shoulder", "hip", "knee"),
    "knee": ("hip", "knee", "ankle"),
}


def interior_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Angle in degrees at ``b`` between b->a and b->c for ``[..., 2]`` points.

    NaN if any point is NaN or either vector is shorter than 1 px.
    """
    u = a - b
    v = c - b
    nu = np.linalg.norm(u, axis=-1)
    nv = np.linalg.norm(v, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.einsum("...i,...i->...", u, v) / (nu * nv)
        ang = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    return np.where((nu < 1.0) | (nv < 1.0), np.nan, ang)


def _wrap(deg: np.ndarray) -> np.ndarray:
    return (deg + 180.0) % 360.0 - 180.0


def _vec_angle(p_from: np.ndarray, p_to: np.ndarray) -> np.ndarray:
    d = p_to - p_from
    return np.degrees(np.arctan2(d[..., 1], d[..., 0]))


def compute_angles(xy: np.ndarray) -> dict[str, np.ndarray]:
    """All v1 angle series, each ``[n_frames]`` degrees (NaN where undefined)."""
    out: dict[str, np.ndarray] = {}
    for side in ("l", "r"):
        for kind, (a, b, c) in _TRIPLES.items():
            out[f"{side}_{kind}"] = interior_angle(
                xy[:, J[f"{side}_{a}"]], xy[:, J[f"{side}_{b}"]], xy[:, J[f"{side}_{c}"]]
            )
    v = xy[:, J["neck"]] - xy[:, J["mid_hip"]]
    out["trunk_lean"] = np.degrees(np.arctan2(v[:, 0], -v[:, 1]))
    out["shoulder_hip_separation"] = _wrap(
        _vec_angle(xy[:, J["r_shoulder"]], xy[:, J["l_shoulder"]])
        - _vec_angle(xy[:, J["r_hip"]], xy[:, J["l_hip"]])
    )
    return {name: out[name] for name in SERIES_NAMES}


def angular_velocity(angle_deg: np.ndarray, fps: float) -> np.ndarray:
    """deg/s = gradient(unwrap(rad(angle))) per contiguous non-NaN run; NaN elsewhere."""
    out = np.full(angle_deg.shape, np.nan)
    valid = ~np.isnan(angle_deg)
    n = angle_deg.size
    i = 0
    while i < n:
        if not valid[i]:
            i += 1
            continue
        j = i
        while j < n and valid[j]:
            j += 1
        if j - i >= 2:
            seg = np.unwrap(np.radians(angle_deg[i:j]))
            out[i:j] = np.degrees(np.gradient(seg, 1.0 / fps))
        i = j
    return out
