"""Camera-view heuristic (plan 5.7, AC12)."""

from __future__ import annotations

import numpy as np

from ..kinematics.body import J, valid_frames
from .models import VIEW_NOTICE, ViewConfig, ViewInfo, ViewClass


def width_ratio(xy: np.ndarray, torso: float, lo: int = 0, hi: int | None = None) -> tuple[float | None, float]:
    """(shoulder-width ratio, valid-frame ratio) over frames [lo, hi].

    Ratio = median |l_shoulder - r_shoulder| / torso over valid frames, averaged with the
    hip-width median when available. None when no valid frame or torso is unusable.
    """
    sl = slice(lo, None if hi is None else hi + 1)
    seg = xy[sl]
    valid = valid_frames(seg)
    valid_ratio = float(valid.mean()) if len(valid) else 0.0
    if not valid.any() or not np.isfinite(torso) or torso <= 0:
        return None, valid_ratio
    sh = np.linalg.norm(seg[valid, J["l_shoulder"]] - seg[valid, J["r_shoulder"]], axis=-1)
    parts = [float(np.median(sh))]
    hip = np.linalg.norm(seg[valid, J["l_hip"]] - seg[valid, J["r_hip"]], axis=-1)
    hip = hip[~np.isnan(hip)]
    if hip.size:
        parts.append(float(np.median(hip)))
    return float(np.mean(parts)) / torso, valid_ratio


def classify_view(ratio: float | None, valid_ratio: float, cfg: ViewConfig) -> ViewClass:
    if ratio is None or valid_ratio < cfg.min_valid_frame_ratio:
        return "unknown"
    if ratio < cfg.side_max_ratio:
        return "side"
    if ratio <= cfg.front_min_ratio:
        return "oblique"
    return "front_or_back"


def view_info(xy: np.ndarray, torso: float, cfg: ViewConfig) -> ViewInfo:
    ratio, valid_ratio = width_ratio(xy, torso)
    cls = classify_view(ratio, valid_ratio, cfg)
    side_on = cls == "side"
    return ViewInfo(
        **{"class": cls},
        side_on=side_on,
        shoulder_width_ratio=None if ratio is None else round(ratio, 3),
        notice=None if side_on else VIEW_NOTICE,
    )
