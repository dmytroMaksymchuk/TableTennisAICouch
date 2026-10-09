import numpy as np
import pytest

from ttcoach.kinematics.smoothing import cutoff_hz, fill_gaps, smooth_series, smooth_xy


@pytest.mark.parametrize("fps,expected", [(30, 6.0), (50, 10.0), (60, 12.0), (120, 12.0)])
def test_cutoff(fps, expected):
    assert cutoff_hz(fps) == pytest.approx(expected)


def _amp(x):
    mid = x[len(x) // 4 : -len(x) // 4]  # ignore edges
    return (mid.max() - mid.min()) / 2.0


@pytest.mark.parametrize("fps", [30, 50, 120])
def test_keeps_2hz(fps):
    t = np.arange(0, 4, 1 / fps)
    x = np.sin(2 * np.pi * 2 * t)
    assert _amp(smooth_series(x, fps)) > 0.98


@pytest.mark.parametrize("fps,hz", [(50, 20), (120, 20), (30, 13)])
def test_attenuates_high_freq(fps, hz):
    t = np.arange(0, 4, 1 / fps)
    x = np.sin(2 * np.pi * hz * t)
    assert _amp(smooth_series(x, fps)) < 0.2


def test_gap_fill_and_long_gap():
    x = np.arange(100, dtype=float)
    x[10:15] = np.nan  # 5 frames <= max_gap
    x[40:60] = np.nan  # 20 frames > max_gap
    filled = fill_gaps(x, 5)
    assert np.allclose(filled[10:15], np.arange(10, 15))
    assert np.isnan(filled[40:60]).all()
    sm = smooth_series(x, 50, max_gap=5)
    assert not np.isnan(sm[:40]).any()
    assert np.isnan(sm[40:60]).all()
    assert not np.isnan(sm[60:]).any()


def test_edge_gap_not_filled():
    x = np.arange(50, dtype=float)
    x[:3] = np.nan
    assert np.isnan(fill_gaps(x, 5)[:3]).all()


def test_short_run_unsmoothed():
    x = np.full(40, np.nan)
    x[5:10] = [0, 5, 0, 5, 0]  # 5-sample run, shorter than padlen
    out = smooth_series(x, 50, max_gap=0)
    assert np.array_equal(out[5:10], x[5:10])


def test_smooth_xy_shape():
    xy = np.random.default_rng(0).normal(size=(60, 26, 2))
    assert smooth_xy(xy, 30).shape == xy.shape
