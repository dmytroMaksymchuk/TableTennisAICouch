"""Signal helpers shared by segmenters and per-stroke metrics."""

from __future__ import annotations

import numpy as np

from ..kinematics.body import J, sides


def wrist_speed(xy: np.ndarray, handedness: str, fps: float, torso: float) -> np.ndarray:
    """|d/dt wrist_dom| in torso lengths per second; NaN where the wrist is missing."""
    dom, _ = sides(handedness)
    w = xy[:, J[f"{dom}_wrist"]]
    out = np.full(len(w), np.nan)
    ok = ~np.isnan(w).any(axis=-1)
    if ok.sum() < 2 or not np.isfinite(torso) or torso <= 0:
        return out
    idx = np.flatnonzero(ok)
    vel = np.gradient(w[idx], idx.astype(float) / fps, axis=0)
    out[idx] = np.linalg.norm(vel, axis=-1) / torso
    return out
