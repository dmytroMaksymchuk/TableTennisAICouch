"""Segmenters based on peaks of the dominant-wrist speed (plan 5.3)."""

from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks

from .base import Params, Segment, SegmentationInput
from .registry import register_segmenter

MAX_MISSING_FRACTION = 0.30


class WindowParams(Params):
    min_stroke_gap_s: float = 0.35      # min time between two contacts
    max_backswing_s: float = 0.8        # stroke start searched in [contact - this, contact)
    max_follow_through_s: float = 0.6   # stroke end searched in (contact, contact + this]


class WristPeaksParams(WindowParams):
    min_peak_speed: float = 4.0         # torso_len/s
    min_peak_prominence: float = 2.0    # torso_len/s


class AdaptiveWristPeaksParams(WindowParams):
    reference_percentile: float = 99.0  # clip speed scale = this percentile of wrist speed
    height_frac: float = 0.5            # peak height >= height_frac * scale
    prominence_frac: float = 0.3        # peak prominence >= prominence_frac * scale
    min_peak_speed_floor: float = 0.5   # torso_len/s; absolute floor so a still clip yields nothing


def peaks_to_segments(
    speed: np.ndarray, fps: float, height: float, prominence: float, p: WindowParams
) -> list[Segment]:
    """Speed peaks -> (start, contact, end) windows; start/end are the speed minima around each peak.

    Overlapping neighbours are split at the midpoint between contacts; windows with more than
    MAX_MISSING_FRACTION missing samples are dropped.
    """
    n = len(speed)
    missing = np.isnan(speed)
    s = np.where(missing, 0.0, speed)
    peaks, _ = find_peaks(
        s, height=height, prominence=prominence, distance=max(1, round(p.min_stroke_gap_s * fps))
    )
    back = round(p.max_backswing_s * fps)
    follow = round(p.max_follow_through_s * fps)

    bounds: list[list[int]] = []
    for c in (int(i) for i in peaks):
        lo = max(0, c - back)
        hi = min(n - 1, c + follow)
        if c - lo < 1 or hi - c < 1:
            continue
        start = lo + int(np.argmin(s[lo:c]))
        end = c + 1 + int(np.argmin(s[c + 1 : hi + 1]))
        bounds.append([start, c, end])

    for prev, nxt in zip(bounds, bounds[1:]):
        if prev[2] >= nxt[0]:
            mid = (prev[1] + nxt[1]) // 2
            prev[2] = mid
            nxt[0] = mid + 1

    return [
        Segment(start, c, end)
        for start, c, end in bounds
        if missing[start : end + 1].mean() <= MAX_MISSING_FRACTION
    ]


@register_segmenter("wrist_peaks", WristPeaksParams)
def wrist_peaks(inp: SegmentationInput, params: WristPeaksParams) -> list[Segment]:
    """Peaks of dominant-wrist speed above fixed thresholds (torso lengths per second)."""
    return peaks_to_segments(
        inp.wrist_speed, inp.fps, params.min_peak_speed, params.min_peak_prominence, params
    )


@register_segmenter("wrist_peaks_adaptive", AdaptiveWristPeaksParams)
def wrist_peaks_adaptive(inp: SegmentationInput, params: AdaptiveWristPeaksParams) -> list[Segment]:
    """Peaks of dominant-wrist speed with thresholds relative to the clip's own speed scale,
    so the camera distance / angle matters less than with fixed thresholds."""
    speed = inp.wrist_speed
    valid = speed[~np.isnan(speed)]
    if valid.size == 0:
        return []
    scale = float(np.percentile(valid, params.reference_percentile))
    height = max(params.min_peak_speed_floor, params.height_frac * scale)
    return peaks_to_segments(speed, inp.fps, height, params.prominence_frac * scale, params)
