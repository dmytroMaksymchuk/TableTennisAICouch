"""Stroke phases (plan 5.4)."""

from __future__ import annotations

from ..segmentation import Segment
from .models import Phase


def contact_half_frames(contact_half_window_s: float, fps: float) -> int:
    return max(1, round(contact_half_window_s * fps))


def build_phases(seg: Segment, half: int) -> list[Phase]:
    """backswing [start, c-h-1], contact [c-h, c+h], follow_through [c+h+1, end] — contiguous, each >= 1 frame."""
    c = seg.contact
    return [
        Phase(name="backswing", start_frame=seg.start, end_frame=c - half - 1),
        Phase(name="contact", start_frame=c - half, end_frame=c + half),
        Phase(name="follow_through", start_frame=c + half + 1, end_frame=seg.end),
    ]
