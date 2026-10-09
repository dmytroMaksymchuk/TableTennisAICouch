"""Canonical pose schema (DESIGN §4.1, §4.2, §4.3).

All coordinates are pixels of the normalized video (origin top-left, y down). Missing = NaN.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

SCHEMA_VERSION = 1

CANONICAL_JOINTS: tuple[str, ...] = (
    "nose", "l_eye", "r_eye", "l_ear", "r_ear",
    "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
    "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle",
    "head", "neck", "mid_hip",
    "l_big_toe", "r_big_toe", "l_small_toe", "r_small_toe", "l_heel", "r_heel",
)  # fmt: skip

JOINT_INDEX: dict[str, int] = {name: i for i, name in enumerate(CANONICAL_JOINTS)}

SKELETON_EDGES: tuple[tuple[str, str], ...] = (
    ("head", "neck"), ("neck", "l_shoulder"), ("neck", "r_shoulder"),
    ("l_shoulder", "l_elbow"), ("l_elbow", "l_wrist"),
    ("r_shoulder", "r_elbow"), ("r_elbow", "r_wrist"),
    ("neck", "mid_hip"), ("mid_hip", "l_hip"), ("mid_hip", "r_hip"),
    ("l_hip", "l_knee"), ("l_knee", "l_ankle"), ("r_hip", "r_knee"), ("r_knee", "r_ankle"),
    ("l_ankle", "l_heel"), ("l_ankle", "l_big_toe"), ("l_big_toe", "l_small_toe"),
    ("r_ankle", "r_heel"), ("r_ankle", "r_big_toe"), ("r_big_toe", "r_small_toe"),
)  # fmt: skip

# Joints that must be present for analysis (DESIGN §4.1).
REQUIRED_JOINTS: tuple[str, ...] = (
    "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
    "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle",
)  # fmt: skip

ConfidenceKind = Literal["score", "presence"]


@dataclass(frozen=True)
class RawTrack:
    person_id: str
    frame_idx: np.ndarray  # int64 [k], indices into the normalized video
    xy: np.ndarray  # float32 [k, n_joints, 2], NaN = missing
    conf: np.ndarray  # float32 [k, n_joints]


@dataclass(frozen=True)
class RawPose:
    estimator: str
    estimator_version: str
    joints: tuple[str, ...]
    tracks: tuple[RawTrack, ...]
    source_n_frames: int
    confidence_kind: ConfidenceKind


@dataclass(frozen=True)
class PersonTrack:
    person_id: str
    xy: np.ndarray  # float32 [n_frames, n_joints, 2]
    conf: np.ndarray  # float32 [n_frames, n_joints]


@dataclass(frozen=True)
class PoseSequence:
    estimator: str
    estimator_version: str
    fps: float
    n_frames: int
    width: int
    height: int
    joints: tuple[str, ...]
    persons: tuple[PersonTrack, ...]
    confidence_kind: ConfidenceKind
    frame_drift: int

    def person(self, person_id: str) -> PersonTrack:
        for p in self.persons:
            if p.person_id == person_id:
                return p
        raise KeyError(person_id)


def _midpoint(track: PersonTrack, target: str, a: str, b: str) -> PersonTrack:
    t, ia, ib = JOINT_INDEX[target], JOINT_INDEX[a], JOINT_INDEX[b]
    xy = track.xy.copy()
    conf = track.conf.copy()
    missing = np.isnan(xy[:, t, :]).any(axis=1)
    both = ~np.isnan(xy[:, ia, :]).any(axis=1) & ~np.isnan(xy[:, ib, :]).any(axis=1)
    fill = missing & both
    xy[fill, t, :] = (xy[fill, ia, :] + xy[fill, ib, :]) / 2.0
    conf[fill, t] = np.minimum(conf[fill, ia], conf[fill, ib])
    return dataclasses.replace(track, xy=xy, conf=conf)


def complete_derived(track: PersonTrack) -> PersonTrack:
    """Synthesise `neck` (mid-shoulders) and `mid_hip` (mid-hips) where absent."""
    track = _midpoint(track, "neck", "l_shoulder", "r_shoulder")
    return _midpoint(track, "mid_hip", "l_hip", "r_hip")


def _rounded_lists(arr: np.ndarray, decimals: int) -> list[list[float | None]]:
    """[n_frames, n_joints] -> per-joint lists of n_frames with NaN -> None."""
    rounded = np.round(arr.astype(np.float64).T, decimals)
    out = rounded.astype(object)
    out[np.isnan(rounded)] = None
    return out.tolist()


def _array_from_lists(rows: list[list[float | None]]) -> np.ndarray:
    """per-joint lists of n_frames (None = missing) -> float32 [n_frames, n_joints]."""
    return np.array(rows, dtype=np.float64).T.astype(np.float32)


def person_to_columns(track: PersonTrack) -> dict[str, Any]:
    return {
        "x": _rounded_lists(track.xy[:, :, 0], 1),
        "y": _rounded_lists(track.xy[:, :, 1], 1),
        "conf": _rounded_lists(track.conf, 2),
    }


def pose_to_json(seq: PoseSequence) -> dict[str, Any]:
    """Serialize to the `pose/persons.json` document (DESIGN §4.3)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "estimator": seq.estimator,
        "estimator_version": seq.estimator_version,
        "fps": seq.fps,
        "n_frames": seq.n_frames,
        "width": seq.width,
        "height": seq.height,
        "confidence_kind": seq.confidence_kind,
        "frame_drift": seq.frame_drift,
        "joints": list(seq.joints),
        "persons": [{"person_id": p.person_id, **person_to_columns(p)} for p in seq.persons],
    }


def pose_from_json(doc: dict[str, Any]) -> PoseSequence:
    n_frames = int(doc["n_frames"])
    n_joints = len(doc["joints"])
    persons = []
    for p in doc["persons"]:
        if n_frames == 0:
            xy = np.full((0, n_joints, 2), np.nan, dtype=np.float32)
            conf = np.zeros((0, n_joints), dtype=np.float32)
        else:
            xy = np.stack([_array_from_lists(p["x"]), _array_from_lists(p["y"])], axis=-1)
            conf = _array_from_lists(p["conf"])
        persons.append(PersonTrack(person_id=p["person_id"], xy=xy, conf=conf))
    return PoseSequence(
        estimator=doc["estimator"],
        estimator_version=doc["estimator_version"],
        fps=float(doc["fps"]),
        n_frames=n_frames,
        width=int(doc["width"]),
        height=int(doc["height"]),
        joints=tuple(doc["joints"]),
        persons=tuple(persons),
        confidence_kind=doc["confidence_kind"],
        frame_drift=int(doc.get("frame_drift", 0)),
    )
