"""Coaching analysis: segmentation, classification, metrics, rules. Entry point: ``analyze``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..kinematics.angles import SERIES_META
from ..kinematics.body import valid_frames
from ..segmentation import Segment, SegmentationInput, Segmenter, get_segmenter, prepare_input, sanitize
from .classify import classify_stroke
from .metrics import MetricContext, compute_metrics
from .models import (
    AngleSeries,
    AnglesDoc,
    FeedbackCounts,
    Quality,
    RuleSet,
    SkippedRule,
    Stroke,
    StrokeType,
    Summary,
    ViewInfo,
)
from .phases import build_phases, contact_half_frames
from .rules import evaluate_rules, load_ruleset
from .view import classify_view, view_info, width_ratio

__all__ = ["AnalysisResult", "DetectedStroke", "RuleSet", "analyze", "detect_strokes", "load_ruleset"]


@dataclass(frozen=True)
class AnalysisResult:
    xy_smooth: np.ndarray                     # [n, 26, 2] float64
    angles: AnglesDoc                         # analysis_rev = 0; set via model_copy(update=...)
    strokes: list[Stroke]
    view: ViewInfo
    quality: Quality
    summary: Summary
    skipped_rules: list[SkippedRule] = field(default_factory=list)


@dataclass(frozen=True)
class DetectedStroke:
    segment: Segment
    type: StrokeType
    type_confidence: float


def detect_strokes(
    inp: SegmentationInput, ruleset: RuleSet, segmenter: Segmenter | None = None
) -> list[DetectedStroke]:
    """Run ``segmenter`` (default: the ruleset's) and classify each stroke the segmenter left untyped."""
    cfg = ruleset.segmentation
    if segmenter is None:
        segmenter = get_segmenter(cfg.method, cfg.params)
    half = contact_half_frames(cfg.contact_half_window_s, inp.fps)
    dom = "r" if inp.handedness == "right" else "l"
    out: list[DetectedStroke] = []
    for seg in sanitize(segmenter(inp), inp.n_frames, half):
        if seg.type is not None:
            conf = 1.0 if seg.type_confidence is None else seg.type_confidence
            out.append(DetectedStroke(seg, seg.type, conf))
            continue
        stype, tconf = classify_stroke(
            inp.xy, inp.angles[f"{dom}_elbow"], seg, inp.handedness, inp.torso, ruleset.classification
        )
        out.append(DetectedStroke(seg, stype, tconf))
    return out


def _opt(a: np.ndarray, nd: int) -> list[float | None]:
    return [None if np.isnan(x) else round(float(x), nd) for x in a]


def analyze(
    xy: np.ndarray,
    conf: np.ndarray,
    fps: float,
    handedness: str,
    ruleset: RuleSet,
    *,
    frame_drift: int = 0,
    cutoff_hz_max: float = 12.0,
    video_path: Path | None = None,
    segmenter: Segmenter | None = None,
) -> AnalysisResult:
    """Full analysis of one player's dense keypoints ``xy[n, 26, 2]`` (CANONICAL_JOINTS order).

    Missing joints are expected to be NaN in ``xy``. ``segmenter`` overrides the ruleset's
    ``segmentation.method``.
    """
    n = xy.shape[0]
    inp = prepare_input(xy, conf, fps, handedness, cutoff_hz_max=cutoff_hz_max, video_path=video_path)
    valid_ratio = float(valid_frames(inp.xy_raw).mean()) if n else 0.0
    smooth, angles, velocity, torso = inp.xy, inp.angles, inp.velocity, inp.torso
    ctx = MetricContext(smooth, angles, velocity, inp.wrist_speed, fps, torso, handedness)
    half = contact_half_frames(ruleset.segmentation.contact_half_window_s, fps)

    strokes: list[Stroke] = []
    skipped: list[SkippedRule] = []
    for det in detect_strokes(inp, ruleset, segmenter):
        seg, stype, tconf = det.segment, det.type, det.type_confidence
        stroke_id = f"s{len(strokes) + 1:03d}"
        ratio, vr = width_ratio(smooth, torso, seg.start, seg.end)
        metrics = compute_metrics(ctx, seg, half)
        feedback, skip = evaluate_rules(ruleset, stroke_id, stype, metrics)
        skipped.extend(skip)
        strokes.append(
            Stroke(
                stroke_id=stroke_id,
                type=stype,
                type_confidence=round(tconf, 3),
                view_class=classify_view(ratio, vr, ruleset.view),
                start_frame=seg.start,
                contact_frame=seg.contact,
                end_frame=seg.end,
                start_s=round(seg.start / fps, 4),
                contact_s=round(seg.contact / fps, 4),
                end_s=round(seg.end / fps, 4),
                phases=build_phases(seg, half),
                metrics=metrics,
                feedback=feedback,
            )
        )

    counts = FeedbackCounts()
    for s in strokes:
        for f in s.feedback:
            setattr(counts, f.severity, getattr(counts, f.severity) + 1)

    return AnalysisResult(
        xy_smooth=smooth,
        angles=AnglesDoc(
            analysis_rev=0,
            fps=fps,
            n_frames=n,
            t=[round(f / fps, 6) for f in range(n)],
            series=[
                AngleSeries(
                    name=name,
                    label=SERIES_META[name][0],
                    definition=SERIES_META[name][1],
                    values=_opt(angles[name], 2),
                    velocity=_opt(velocity[name], 1),
                )
                for name in angles
            ],
        ),
        strokes=strokes,
        view=view_info(smooth, torso, ruleset.view),
        quality=Quality(
            valid_frame_ratio=round(valid_ratio, 3),
            torso_len_px=None if not np.isfinite(torso) else round(torso, 1),
            frame_drift=frame_drift,
        ),
        summary=Summary(
            n_strokes=len(strokes),
            n_fh=sum(s.type == "FH" for s in strokes),
            n_bh=sum(s.type == "BH" for s in strokes),
            feedback_counts=counts,
        ),
        skipped_rules=skipped,
    )
