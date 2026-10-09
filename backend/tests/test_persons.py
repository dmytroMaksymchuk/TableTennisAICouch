from __future__ import annotations

import numpy as np
import pytest

from ttcoach.errors import PipelineError
from ttcoach.pose.persons import candidates, merge_tracks
from ttcoach.pose.schema import CANONICAL_JOINTS, JOINT_INDEX, PersonTrack, PoseSequence

N, J = 100, len(CANONICAL_JOINTS)


def person(pid: str, x0: float, y0: float, w: float, h: float, frames=slice(None)) -> PersonTrack:
    xy = np.full((N, J, 2), np.nan, dtype=np.float32)
    rng = np.linspace(0, 1, J, dtype=np.float32)
    xy[frames, :, 0] = x0 + rng * w
    xy[frames, :, 1] = y0 + rng[::-1] * h
    conf = (~np.isnan(xy).any(axis=2)).astype(np.float32)
    return PersonTrack(pid, xy, conf)


def seq(*persons: PersonTrack) -> PoseSequence:
    return PoseSequence("stub", "1", 50.0, N, 640, 480, CANONICAL_JOINTS, persons, "presence", 0)


def test_two_candidates_with_bboxes_and_suggestion() -> None:
    small = person("p00", 10, 20, 100, 200)
    big = person("p01", 300, 100, 200, 300, frames=slice(0, 60))
    doc = candidates(seq(small, big))
    by_id = {c["person_id"]: c for c in doc["candidates"]}
    assert set(by_id) == {"p00", "p01"}
    assert by_id["p00"]["coverage"] == 1.0 and by_id["p01"]["coverage"] == 0.6
    # joint extents +-5 %
    assert by_id["p00"]["bbox_at_preview"] == pytest.approx([5.0, 10.0, 115.0, 230.0], abs=0.11)
    assert by_id["p01"]["bbox_at_preview"] == pytest.approx([290.0, 85.0, 510.0, 415.0], abs=0.11)
    assert 0 <= by_id["p01"]["preview_frame"] < 60
    # 0.6 * (220*330) > 1.0 * (110*220)
    assert doc["suggested_person_ids"] == ["p01"]


def test_low_coverage_dropped_and_none_raises() -> None:
    tiny = person("p00", 0, 0, 10, 10, frames=slice(0, 10))  # 10 % < 15 %
    with pytest.raises(PipelineError) as e:
        candidates(seq(tiny))
    assert e.value.code == "no_person_detected"
    with pytest.raises(PipelineError):
        candidates(seq())


def test_bbox_clipped_to_image() -> None:
    p = person("p00", 600, 400, 100, 100)
    (c,) = candidates(seq(p))["candidates"]
    assert c["bbox_at_preview"][2] <= 640 and c["bbox_at_preview"][3] <= 480


def test_merge_takes_first_valid_id_per_frame() -> None:
    a = person("p00", 0, 0, 10, 10, frames=slice(0, 50))
    b = person("p03", 100, 100, 10, 10)
    merged = merge_tracks(seq(a, b), ["p00", "p03"])
    w = JOINT_INDEX["r_wrist"]
    assert merged.xy[10, w, 0] == a.xy[10, w, 0]
    assert merged.xy[70, w, 0] == b.xy[70, w, 0]
    merged_rev = merge_tracks(seq(a, b), ["p03", "p00"])
    assert merged_rev.xy[10, w, 0] == b.xy[10, w, 0]
    assert merged.xy.shape == (N, J, 2)
