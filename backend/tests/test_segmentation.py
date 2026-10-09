import numpy as np
import pytest
from tests.fixtures.synthetic import swing_pose

from ttcoach import coaching
from ttcoach.coaching.models import SegmentationConfig
from ttcoach.coaching.phases import build_phases
from ttcoach.segmentation import (
    Segment,
    available_segmenters,
    get_segmenter,
    prepare_input,
    register_segmenter,
    sanitize,
)
from ttcoach.segmentation import registry as seg_registry
from ttcoach.segmentation.wrist_peaks import WristPeaksParams, peaks_to_segments

FPS = 50.0
CONTACTS_S = [1.0, 2.5, 4.0]


def _inp(xy, handedness="right"):
    return prepare_input(xy, np.ones(xy.shape[:2]), FPS, handedness)


def _wrist_peaks(xy, **params):
    return get_segmenter("wrist_peaks", params)(_inp(xy))


def test_three_bursts_three_strokes():
    n = int(6 * FPS)
    segs = _wrist_peaks(swing_pose(n, FPS, CONTACTS_S))
    assert len(segs) == 3
    for seg, c in zip(segs, CONTACTS_S):
        assert abs(seg.contact - c * FPS) <= 2
        assert seg.start < seg.contact < seg.end


def test_no_motion_no_strokes():
    assert _wrist_peaks(swing_pose(200, FPS, [])) == []


def test_missing_wrist_dropped():
    n = int(6 * FPS)
    sp = _inp(swing_pose(n, FPS, CONTACTS_S)).wrist_speed.copy()
    sp[int(2.0 * FPS) : int(3.0 * FPS)] = np.nan  # middle stroke mostly missing
    sp[int(2.5 * FPS)] = 30.0  # peak survives only as a lone sample
    assert len(peaks_to_segments(sp, FPS, 4.0, 2.0, WristPeaksParams())) == 2


def test_close_peaks_respect_min_gap_and_windows_do_not_overlap():
    i = np.arange(200)
    bump = lambda c, h: h * np.exp(-(((i - c) / 3.0) ** 2))  # noqa: E731
    sp = bump(50, 10.0) + bump(60, 9.0) + bump(120, 10.0)  # 50 and 60 are within 0.35 s * 50 frames
    segs = peaks_to_segments(sp, FPS, 4.0, 1.0, WristPeaksParams())
    assert [s.contact for s in segs] == [50, 120]
    assert all(a.end < b.start for a, b in zip(segs, segs[1:]))


def test_adaptive_finds_slow_strokes_fixed_threshold_misses():
    n = int(6 * FPS)
    inp = _inp(swing_pose(n, FPS, CONTACTS_S))
    slow = prepare_input(inp.xy_raw, inp.conf, FPS / 3, "right")  # same frames played 3x slower
    assert get_segmenter("wrist_peaks")(slow) == []
    segs = get_segmenter("wrist_peaks_adaptive")(slow)
    assert len(segs) == 3


def test_phases_contiguous_ordered():
    n = int(6 * FPS)
    for seg in _wrist_peaks(swing_pose(n, FPS, CONTACTS_S)):
        ph = build_phases(seg, 2)
        assert [p.name for p in ph] == ["backswing", "contact", "follow_through"]
        assert ph[0].start_frame == seg.start and ph[-1].end_frame == seg.end
        for a, b in zip(ph, ph[1:]):
            assert b.start_frame == a.end_frame + 1
        assert all(p.end_frame >= p.start_frame for p in ph)


def test_phase_values():
    ph = build_phases(Segment(10, 30, 50), 2)
    assert [(p.start_frame, p.end_frame) for p in ph] == [(10, 27), (28, 32), (33, 50)]


def test_sanitize_sorts_and_drops_too_short_or_out_of_range():
    segs = [Segment(40, 50, 60), Segment(10, 20, 30), Segment(18, 20, 30), Segment(90, 95, 100)]
    assert sanitize(segs, 100, 2) == [Segment(10, 20, 30), Segment(40, 50, 60)]


# ---- registry / config ---------------------------------------------------------------------

def test_builtins_registered_with_defaults():
    names = [s.name for s in available_segmenters()]
    assert {"wrist_peaks", "wrist_peaks_adaptive"} <= set(names)
    spec = seg_registry.segmenter_spec("wrist_peaks")
    assert spec.defaults()["min_peak_speed"] == 4.0 and spec.description


def test_unknown_segmenter_and_bad_params_rejected():
    with pytest.raises(ValueError, match="unknown segmenter"):
        get_segmenter("nope")
    with pytest.raises(ValueError, match="invalid params"):
        get_segmenter("wrist_peaks", {"min_peak_sped": 1.0})
    with pytest.raises(ValueError):
        SegmentationConfig(method="nope")


@pytest.fixture
def scratch_segmenter():
    yield
    seg_registry._REGISTRY.pop("test_fixed", None)


def test_plugin_segmenter_with_type_drives_analysis(scratch_segmenter):
    @register_segmenter("test_fixed")
    def fixed(inp, params):
        """Two hard-coded strokes."""
        return [Segment(60, 75, 90, type="BH", type_confidence=0.9), Segment(10, 25, 40)]

    n = int(6 * FPS)
    xy = swing_pose(n, FPS, CONTACTS_S)
    rs = coaching.load_ruleset(_rules_path())
    rs = rs.model_copy(update={"segmentation": SegmentationConfig(method="test_fixed")})
    res = coaching.analyze(xy, np.ones(xy.shape[:2]), FPS, "right", rs)
    assert [s.contact_frame for s in res.strokes] == [25, 75]  # sorted by contact
    assert res.strokes[1].type == "BH" and res.strokes[1].type_confidence == 0.9
    with pytest.raises(ValueError, match="already registered"):
        register_segmenter("test_fixed")(fixed)


def test_analyze_segmenter_override():
    n = int(6 * FPS)
    xy = swing_pose(n, FPS, CONTACTS_S)
    rs = coaching.load_ruleset(_rules_path())
    res = coaching.analyze(xy, np.ones(xy.shape[:2]), FPS, "right", rs, segmenter=lambda inp: [])
    assert res.strokes == []


def test_analyze_end_to_end_shape():
    n = int(6 * FPS)
    xy = swing_pose(n, FPS, CONTACTS_S)
    res = coaching.analyze(xy, np.ones(xy.shape[:2]), FPS, "right", coaching.load_ruleset(_rules_path()))
    assert res.summary.n_strokes == 3
    assert [s.stroke_id for s in res.strokes] == ["s001", "s002", "s003"]
    assert res.angles.n_frames == n and len(res.angles.series) == 10
    assert res.quality.valid_frame_ratio == pytest.approx(1.0)
    assert res.view.shoulder_width_ratio == pytest.approx(0.5, abs=0.01)
    for s in res.strokes:  # contact_s consistent with frame / fps
        assert s.contact_s == pytest.approx(s.contact_frame / FPS, abs=1e-3)


def _rules_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "rules" / "default.yaml"
