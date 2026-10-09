"""Per-stroke metrics (plan 4.3, 5.6). Unavailable metrics are omitted from the result."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from ..kinematics.body import J, sides
from ..segmentation import Segment
from .models import METRIC_UNITS, MetricValue


@dataclass(frozen=True)
class MetricContext:
    xy: np.ndarray                      # smoothed [n, 26, 2]
    angles: dict[str, np.ndarray]       # smoothed-angle series, deg
    velocity: dict[str, np.ndarray]     # deg/s
    wrist_speed: np.ndarray             # torso_len/s
    fps: float
    torso: float
    handedness: str


def _foot_points(xy: np.ndarray, side: str) -> np.ndarray:
    """Mean of available {ankle, heel, big_toe} per frame -> [n, 2] (NaN if none)."""
    pts = np.stack([xy[:, J[f"{side}_{n}"]] for n in ("ankle", "heel", "big_toe")], axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(pts, axis=1)


def _nan_stat(fn, a: np.ndarray) -> float:
    a = a[~np.isnan(a)]
    return float(fn(a)) if a.size else float("nan")


def circular_range(deg: np.ndarray) -> float:
    """max - min of an angle series (deg) that may wrap at +-180; NaN samples are ignored."""
    a = deg[~np.isnan(deg)]
    if not a.size:
        return float("nan")
    u = np.degrees(np.unwrap(np.radians(a)))
    return float(u.max() - u.min())


def compute_metrics(ctx: MetricContext, seg: Segment, half: int) -> dict[str, MetricValue]:
    dom, nd = sides(ctx.handedness)
    s, c, e = seg.start, seg.contact, seg.end
    win = slice(s, e + 1)
    fps, torso = ctx.fps, ctx.torso
    a, v = ctx.angles, ctx.velocity
    raw: dict[str, float] = {}

    back_s = (c - s) / fps
    follow_s = (e - c) / fps
    raw["duration_s"] = (e - s) / fps
    raw["backswing_duration_s"] = back_s
    raw["follow_through_duration_s"] = follow_s
    raw["backswing_to_follow_ratio"] = back_s / follow_s if follow_s > 0 else float("nan")

    raw["elbow_angle_at_backswing_start"] = a[f"{dom}_elbow"][s]
    raw["elbow_angle_at_contact"] = a[f"{dom}_elbow"][c]
    raw["elbow_angle_at_follow_end"] = a[f"{dom}_elbow"][e]
    raw["shoulder_angle_at_contact"] = a[f"{dom}_shoulder"][c]
    raw["knee_angle_min_dom"] = _nan_stat(np.min, a[f"{dom}_knee"][win])
    raw["knee_angle_min_nd"] = _nan_stat(np.min, a[f"{nd}_knee"][win])
    knees = np.array([a[f"{dom}_knee"][c], a[f"{nd}_knee"][c]])
    raw["knee_angle_at_contact_mean"] = _nan_stat(np.mean, knees)
    raw["trunk_lean_at_contact"] = a["trunk_lean"][c]
    raw["shoulder_hip_separation_range"] = circular_range(a["shoulder_hip_separation"][win])
    raw["wrist_peak_speed"] = _nan_stat(np.max, ctx.wrist_speed[win])
    raw["elbow_peak_angular_velocity"] = _nan_stat(np.max, np.abs(v[f"{dom}_elbow"][win]))

    if np.isfinite(torso) and torso > 0:
        feet = {side: _foot_points(ctx.xy, side) for side in ("l", "r")}
        disp = [
            _nan_stat(np.max, np.linalg.norm(f[win] - f[s], axis=-1)) for f in feet.values()
        ]
        raw["foot_displacement_max"] = _nan_stat(np.max, np.array(disp)) / torso
        raw["base_width_ratio"] = float(np.linalg.norm(feet["l"][c] - feet["r"][c])) / torso
        hip_x = ctx.xy[win, J["mid_hip"], 0]
        raw["hip_sway"] = (_nan_stat(np.max, hip_x) - _nan_stat(np.min, hip_x)) / torso

    return {
        name: MetricValue(value=round(float(val), 4), unit=METRIC_UNITS[name])
        for name, val in raw.items()
        if np.isfinite(val)
    }
