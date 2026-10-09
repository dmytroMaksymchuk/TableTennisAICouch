"""Gap filling and zero-lag Butterworth smoothing (plan 5.1)."""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt

ORDER = 2
_PADLEN = 3 * (2 * ORDER + 1)  # scipy filtfilt default: 3 * max(len(a), len(b))


def cutoff_hz(fps: float, cutoff_hz_max: float = 12.0) -> float:
    """fc = min(cutoff_hz_max, 0.2 * fps): 6 / 10 / 12 / 12 Hz at 30 / 50 / 60 / 120 fps."""
    return min(cutoff_hz_max, 0.2 * fps)


def max_gap_frames(fps: float) -> int:
    return int(round(0.10 * fps))


def fill_gaps(x: np.ndarray, max_gap: int) -> np.ndarray:
    """Linearly interpolate NaN runs of length <= max_gap that have valid samples on both sides."""
    out = np.array(x, dtype=np.float64, copy=True)
    isnan = np.isnan(out)
    n = out.size
    i = 0
    while i < n:
        if not isnan[i]:
            i += 1
            continue
        j = i
        while j < n and isnan[j]:
            j += 1
        if i > 0 and j < n and (j - i) <= max_gap:
            out[i:j] = np.interp(np.arange(i, j), [i - 1, j], [out[i - 1], out[j]])
        i = j
    return out


def smooth_series(
    x: np.ndarray, fps: float, cutoff_hz_max: float = 12.0, max_gap: int | None = None
) -> np.ndarray:
    """Fill short gaps, then filtfilt each contiguous non-NaN run longer than the pad length.

    Longer gaps stay NaN; shorter runs are returned unsmoothed.
    """
    gap = max_gap_frames(fps) if max_gap is None else max_gap
    out = fill_gaps(x, gap)
    b, a = butter(ORDER, cutoff_hz(fps, cutoff_hz_max) / (fps / 2.0))
    valid = ~np.isnan(out)
    n = out.size
    i = 0
    while i < n:
        if not valid[i]:
            i += 1
            continue
        j = i
        while j < n and valid[j]:
            j += 1
        if j - i > _PADLEN:
            out[i:j] = filtfilt(b, a, out[i:j])
        i = j
    return out


def smooth_xy(xy: np.ndarray, fps: float, cutoff_hz_max: float = 12.0) -> np.ndarray:
    """Smooth every joint coordinate of ``xy[n, J, 2]``."""
    out = np.empty(xy.shape, dtype=np.float64)
    for j in range(xy.shape[1]):
        for c in range(2):
            out[:, j, c] = smooth_series(xy[:, j, c], fps, cutoff_hz_max)
    return out
