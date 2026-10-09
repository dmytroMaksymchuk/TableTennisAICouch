"""Core alignment of sparse estimator tracks to normalized-video frames (DESIGN §4.2.1)."""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from ttcoach.errors import PipelineError
from ttcoach.pose.base import VideoMeta
from ttcoach.pose.schema import CANONICAL_JOINTS, JOINT_INDEX, PersonTrack, PoseSequence, RawPose, RawTrack

logger = logging.getLogger(__name__)
SILENT_DRIFT = 2


def drift_limit(n_frames: int) -> float:
    return max(5.0, 0.01 * n_frames)


def _to_canonical(raw: RawPose, track: RawTrack) -> tuple[np.ndarray, np.ndarray]:
    if tuple(raw.joints) == CANONICAL_JOINTS:
        return track.xy, track.conf
    k = len(track.frame_idx)
    xy = np.full((k, len(CANONICAL_JOINTS), 2), np.nan, dtype=np.float32)
    conf = np.zeros((k, len(CANONICAL_JOINTS)), dtype=np.float32)
    for src, name in enumerate(raw.joints):
        if name in JOINT_INDEX:
            xy[:, JOINT_INDEX[name]] = track.xy[:, src]
            conf[:, JOINT_INDEX[name]] = track.conf[:, src]
    return xy, conf


def align_pose(raw: RawPose, meta: VideoMeta, log: Callable[[str], None] | None = None) -> PoseSequence:
    n = meta.n_frames

    def warn(msg: str) -> None:
        logger.warning(msg)
        if log is not None:
            log(f"WARNING: {msg}")

    drift = abs(raw.source_n_frames - n)
    for t in raw.tracks:
        if len(t.frame_idx):
            drift = max(drift, int(t.frame_idx.max()) - (n - 1), -int(t.frame_idx.min()))
    limit = drift_limit(n)
    if drift > limit:
        raise PipelineError("pose_failed", f"frame drift {drift} > {limit:g}", "estimating_pose")
    frame_drift = 0
    if drift > SILENT_DRIFT:
        frame_drift = drift
        warn(f"pose frame drift {drift} (estimator {raw.source_n_frames} frames, video {n})")

    persons = []
    for t in raw.tracks:
        src_xy, src_conf = _to_canonical(raw, t)
        xy = np.full((n, len(CANONICAL_JOINTS), 2), np.nan, dtype=np.float32)
        conf = np.zeros((n, len(CANONICAL_JOINTS)), dtype=np.float32)
        idx = t.frame_idx.astype(np.int64)
        rows = np.flatnonzero((idx >= 0) & (idx < n))
        # Last row wins for duplicate frame indices: take the first occurrence in reversed order.
        rev = rows[::-1]
        uniq, first_in_rev = np.unique(idx[rev], return_index=True)
        if len(uniq) < len(rows):
            vals, counts = np.unique(idx[rows], return_counts=True)
            dup = vals[counts > 1]
            warn(f"{t.person_id}: duplicate frame indices {dup[:10].tolist()} (last row wins)")
        chosen = rev[first_in_rev]
        xy[idx[chosen]] = src_xy[chosen]
        conf[idx[chosen]] = src_conf[chosen]
        persons.append(PersonTrack(person_id=t.person_id, xy=xy, conf=conf))

    return PoseSequence(
        estimator=raw.estimator,
        estimator_version=raw.estimator_version,
        fps=meta.fps,
        n_frames=n,
        width=meta.width,
        height=meta.height,
        joints=CANONICAL_JOINTS,
        persons=tuple(persons),
        confidence_kind=raw.confidence_kind,
        frame_drift=frame_drift,
    )
