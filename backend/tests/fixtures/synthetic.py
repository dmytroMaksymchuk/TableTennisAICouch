"""Synthetic skeleton builders for kinematics / coaching tests. Pure numpy, float64, [n, 26, 2]."""

from __future__ import annotations

import math

import numpy as np
from scipy.special import erf

from ttcoach.kinematics.body import J

TORSO_PX = 200.0
CENTER_X = 640.0
NECK_Y = 300.0
HIP_Y = NECK_Y + TORSO_PX


def standing_pose(n: int, width_ratio: float = 0.5) -> np.ndarray:
    """Static upright player facing the camera. The right side is image-left (frontal view).

    ``width_ratio`` = shoulder width / torso length (hip width equals shoulder width).
    """
    xy = np.zeros((n, 26, 2))
    sw = width_ratio * TORSO_PX
    half = sw / 2.0

    def put(name: str, x: float, y: float) -> None:
        xy[:, J[name]] = (x, y)

    put("neck", CENTER_X, NECK_Y)
    put("head", CENTER_X, NECK_Y - 60)
    put("nose", CENTER_X, NECK_Y - 50)
    for s, sign in (("r", -1.0), ("l", 1.0)):
        x = CENTER_X + sign * half
        put(f"{s}_eye", CENTER_X + sign * 10, NECK_Y - 60)
        put(f"{s}_ear", CENTER_X + sign * 25, NECK_Y - 55)
        put(f"{s}_shoulder", x, NECK_Y)
        put(f"{s}_elbow", x, NECK_Y + 100)
        put(f"{s}_wrist", x, NECK_Y + 190)
        put(f"{s}_hip", x, HIP_Y)
        put(f"{s}_knee", x, HIP_Y + 150)
        put(f"{s}_ankle", x, HIP_Y + 300)
        put(f"{s}_heel", x - sign * 5, HIP_Y + 310)
        put(f"{s}_big_toe", x + sign * 10, HIP_Y + 320)
        put(f"{s}_small_toe", x + sign * 20, HIP_Y + 320)
    put("mid_hip", CENTER_X, HIP_Y)
    return xy


def place_angle(xy: np.ndarray, a: str, b: str, c: str, theta_deg, length: float = 100.0) -> None:
    """Set joints a, b, c so the interior angle at b between b->a and b->c equals ``theta_deg``
    (scalar or per-frame array). b is fixed at (500, 400); b->a points image-up."""
    theta = np.radians(np.broadcast_to(np.asarray(theta_deg, dtype=float), (xy.shape[0],)))
    bpt = np.array([500.0, 400.0])
    xy[:, J[b]] = bpt
    xy[:, J[a]] = bpt + np.array([0.0, -length])
    xy[:, J[c], 0] = bpt[0] + length * np.sin(theta)
    xy[:, J[c], 1] = bpt[1] - length * np.cos(theta)


def swing_pose(
    n: int,
    fps: float,
    contacts_s: list[float],
    *,
    handedness: str = "right",
    contact_side: int = 1,
    width_ratio: float = 0.5,
    amplitude_px: float = 300.0,
    sigma_s: float = 0.05,
) -> np.ndarray:
    """Standing pose whose dominant wrist makes one fast horizontal sweep per entry of ``contacts_s``.

    The wrist speed is a Gaussian (peak at the given time, ~12 torso/s with the defaults), followed by a
    slow linear return over 1 s. ``contact_side`` = +1 puts the wrist on the dominant-shoulder side of the
    body midline at contact, -1 on the other side.
    """
    xy = standing_pose(n, width_ratio)
    dom = "r" if handedness == "right" else "l"
    dom_sign = -1.0 if dom == "r" else 1.0  # image-x direction from midline towards the dominant shoulder
    direction = -1.0  # sweep direction (image x)
    t = np.arange(n) / fps
    pos = np.zeros(n)
    for c in contacts_s:
        rise = 0.5 * (1.0 + erf((t - c) / (sigma_s * math.sqrt(2.0))))
        ret = 1.0 - np.clip((t - (c + 0.4)) / 1.0, 0.0, 1.0)
        pos += rise * ret
    contact_x = CENTER_X + contact_side * dom_sign * 100.0
    rest_x = contact_x - direction * amplitude_px / 2.0
    xy[:, J[f"{dom}_wrist"], 0] = rest_x + direction * amplitude_px * pos
    return xy
