"""Candidate persons and multi-id merge (plan §5.8, DESIGN §3.4)."""

from __future__ import annotations

from typing import Any

import numpy as np

from ttcoach.errors import PipelineError
from ttcoach.pose.schema import JOINT_INDEX, REQUIRED_JOINTS, PersonTrack, PoseSequence

MIN_COVERAGE = 0.15
BBOX_PAD = 0.05
_REQ = np.array([JOINT_INDEX[j] for j in REQUIRED_JOINTS])


def _joint_valid(track: PersonTrack) -> np.ndarray:
    """bool [n_frames, n_joints]"""
    return ~np.isnan(track.xy).any(axis=2)


def frame_valid(track: PersonTrack) -> np.ndarray:
    """A frame counts as a detection when >= 50 % of the required joints are present."""
    return _joint_valid(track)[:, _REQ].sum(axis=1) >= len(_REQ) / 2


def _bbox(xy: np.ndarray, width: int, height: int) -> list[float] | None:
    pts = xy[~np.isnan(xy).any(axis=1)]
    if len(pts) == 0:
        return None
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    px, py = (x1 - x0) * BBOX_PAD, (y1 - y0) * BBOX_PAD
    return [
        round(float(max(0.0, x0 - px)), 1),
        round(float(max(0.0, y0 - py)), 1),
        round(float(min(width, x1 + px)), 1),
        round(float(min(height, y1 + py)), 1),
    ]


def candidates(seq: PoseSequence) -> dict[str, Any]:
    """`pose/candidates.json` payload. Raises PipelineError(no_person_detected) if none qualifies."""
    items: list[tuple[float, dict[str, Any]]] = []
    n = max(1, seq.n_frames)
    for p in seq.persons:
        jv = _joint_valid(p)
        fv = frame_valid(p)
        coverage = float(fv.sum()) / n
        if coverage < MIN_COVERAGE:
            continue
        preview = int(np.argmax(jv.sum(axis=1)))
        confs = p.conf[jv]
        areas = []
        for f in np.flatnonzero(fv):
            b = _bbox(p.xy[f], seq.width, seq.height)
            if b is not None:
                areas.append((b[2] - b[0]) * (b[3] - b[1]))
        score = coverage * (float(np.median(areas)) if areas else 0.0)
        items.append((
            score,
            {
                "person_id": p.person_id,
                "coverage": round(coverage, 3),
                "mean_confidence": round(float(confs.mean()), 3) if confs.size else 0.0,
                "preview_frame": preview,
                "bbox_at_preview": _bbox(p.xy[preview], seq.width, seq.height),
            },
        ))  # fmt: skip
    if not items:
        raise PipelineError("no_person_detected", "no person tracked in enough frames", "estimating_pose")
    best = max(items, key=lambda it: it[0])
    return {
        "schema_version": 1,
        "candidates": [c for _, c in items],
        "suggested_person_ids": [best[1]["person_id"]],
    }


def merge_tracks(seq: PoseSequence, person_ids: list[str]) -> PersonTrack:
    """Per frame, take the first listed id with a detection (>= 50 % required joints, else any joint)."""
    tracks = [seq.person(pid) for pid in person_ids]
    xy = np.full_like(tracks[0].xy, np.nan)
    conf = np.zeros_like(tracks[0].conf)
    filled = np.zeros(seq.n_frames, dtype=bool)
    for strict in (True, False):
        for t in tracks:
            ok = (frame_valid(t) if strict else _joint_valid(t).any(axis=1)) & ~filled
            xy[ok] = t.xy[ok]
            conf[ok] = t.conf[ok]
            filled |= ok
    return PersonTrack(person_id="+".join(person_ids), xy=xy, conf=conf)
