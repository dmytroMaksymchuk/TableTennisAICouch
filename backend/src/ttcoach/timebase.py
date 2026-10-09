"""Frame/time rule (DESIGN §0): the single source of truth for frame <-> time conversion."""

from __future__ import annotations

import math


def time_to_frame(t: float, fps: float, n_frames: int) -> int:
    f = math.floor(t * fps + 1e-6)
    return max(0, min(f, n_frames - 1))


def frame_to_time(f: int, fps: float) -> float:
    """Reported time of frame f (all `*_s` fields)."""
    return f / fps


def frame_to_seek_time(f: int, fps: float) -> float:
    """Middle of the frame interval; used for seeking."""
    return (f + 0.5) / fps
