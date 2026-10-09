"""Forehand / backhand classification (plan 5.5)."""

from __future__ import annotations

import numpy as np

from ..kinematics.body import J, sides
from ..segmentation import Segment
from .models import ClassificationConfig, StrokeType
from .view import width_ratio


def classify_stroke(
    xy: np.ndarray,
    elbow_dom: np.ndarray,
    seg: Segment,
    handedness: str,
    torso: float,
    cfg: ClassificationConfig,
) -> tuple[StrokeType, float]:
    """(type, confidence). Lateral wrist offset at contact when the camera sees the shoulder line,
    otherwise an elbow-angle fallback with fixed low confidence."""
    dom, nd = sides(handedness)
    ratio, _ = width_ratio(xy, torso, seg.start, seg.end)
    sh_dom = xy[seg.contact, J[f"{dom}_shoulder"]]
    sh_nd = xy[seg.contact, J[f"{nd}_shoulder"]]
    axis = sh_dom - sh_nd
    width = float(np.linalg.norm(axis))
    d = float("nan")
    if width > 0 and not np.isnan(axis).any():
        rel = xy[seg.contact, J[f"{dom}_wrist"]] - (sh_dom + sh_nd) / 2.0
        d = float(np.dot(rel, axis / width) / width)

    if ratio is not None and ratio >= cfg.min_shoulder_width_ratio and not np.isnan(d):
        stroke_type: StrokeType = "FH" if d > cfg.fh_lateral_offset_min else "BH"
        return stroke_type, float(np.clip(abs(d), 0.0, 1.0))

    elbow = elbow_dom[seg.start]
    stroke_type = "BH" if (not np.isnan(elbow) and elbow <= cfg.side_fallback_bh_elbow_max) else "FH"
    return stroke_type, 0.3
