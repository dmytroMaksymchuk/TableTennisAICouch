"""Segmenter plugin interface: what a segmenter receives and what it returns."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from ..kinematics.angles import angular_velocity, compute_angles
from ..kinematics.body import derive_neck_mid_hip, torso_len
from ..kinematics.smoothing import smooth_xy
from .features import wrist_speed

StrokeType = Literal["FH", "BH"]


@dataclass(frozen=True)
class Segment:
    """One stroke as frame indices ``start < contact < end`` (inclusive).

    A segmenter that also recognises the stroke type may set ``type`` (and ``type_confidence``);
    the analysis then uses it instead of the built-in FH/BH classifier.
    """

    start: int
    contact: int
    end: int
    type: StrokeType | None = None
    type_confidence: float | None = None


@dataclass(frozen=True)
class SegmentationInput:
    """Everything a segmenter may look at. Arrays are ``[n_frames, 26, 2]`` px in CANONICAL_JOINTS order."""

    xy: np.ndarray                      # smoothed keypoints (NaN = missing)
    xy_raw: np.ndarray                  # unsmoothed keypoints (neck / mid_hip derived)
    conf: np.ndarray                    # [n_frames, 26]
    fps: float
    handedness: str                     # "right" | "left"
    torso: float                        # median torso length, px (NaN if unknown)
    angles: dict[str, np.ndarray]       # smoothed joint angles, deg
    velocity: dict[str, np.ndarray]     # angular velocities, deg/s
    video_path: Path | None = None      # normalized video, for audio/vision-based segmenters

    @property
    def n_frames(self) -> int:
        return self.xy.shape[0]

    @cached_property
    def wrist_speed(self) -> np.ndarray:
        """Dominant-wrist speed in torso lengths per second (NaN where missing)."""
        return wrist_speed(self.xy, self.handedness, self.fps, self.torso)


def prepare_input(
    xy: np.ndarray,
    conf: np.ndarray,
    fps: float,
    handedness: str,
    *,
    cutoff_hz_max: float = 12.0,
    video_path: Path | None = None,
) -> SegmentationInput:
    """Smooth raw keypoints and derive the shared signals (same preprocessing as the analysis)."""
    raw = derive_neck_mid_hip(xy)
    smooth = smooth_xy(raw, fps, cutoff_hz_max)
    angles = compute_angles(smooth)
    return SegmentationInput(
        xy=smooth,
        xy_raw=raw,
        conf=conf,
        fps=fps,
        handedness=handedness,
        torso=torso_len(smooth),
        angles=angles,
        velocity={k: angular_velocity(v, fps) for k, v in angles.items()},
        video_path=video_path,
    )


class Params(BaseModel):
    """Base class for segmenter parameters; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")


Segmenter = Callable[[SegmentationInput], list[Segment]]


def sanitize(segments: list[Segment], n_frames: int, contact_half: int) -> list[Segment]:
    """Sort by contact and keep segments whose three phases each get >= 1 frame inside the clip.

    Applied to every segmenter's output so phases, metrics and evaluation see the same strokes.
    """
    out = []
    for s in sorted(segments, key=lambda s: s.contact):
        if 0 <= s.start <= s.contact - contact_half - 1 and s.contact + contact_half + 1 <= s.end < n_frames:
            out.append(s)
    return out
