"""Pydantic models for rules and result documents (DESIGN 4.5-4.8). Source of truth for api/types.ts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ttcoach.segmentation import StrokeType, get_segmenter
ViewClass = Literal["side", "oblique", "front_or_back", "unknown"]
PhaseName = Literal["backswing", "contact", "follow_through"]
Severity = Literal["info", "warning", "critical"]
Op = Literal["lt", "lte", "gt", "gte", "between", "outside"]

# Metric catalog v1 (plan 4.3): name -> unit
METRIC_UNITS: dict[str, str] = {
    "duration_s": "s",
    "backswing_duration_s": "s",
    "follow_through_duration_s": "s",
    "backswing_to_follow_ratio": "ratio",
    "elbow_angle_at_backswing_start": "deg",
    "elbow_angle_at_contact": "deg",
    "elbow_angle_at_follow_end": "deg",
    "shoulder_angle_at_contact": "deg",
    "knee_angle_min_dom": "deg",
    "knee_angle_min_nd": "deg",
    "knee_angle_at_contact_mean": "deg",
    "trunk_lean_at_contact": "deg",
    "shoulder_hip_separation_range": "deg",
    "wrist_peak_speed": "torso_len/s",
    "elbow_peak_angular_velocity": "deg/s",
    "foot_displacement_max": "torso_len",
    "base_width_ratio": "torso_len",
    "hip_sway": "torso_len",
}

VIEW_NOTICE = (
    "Camera is not side-on: joint angles measured in 2D may be distorted. "
    "Treat angle feedback as approximate."
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- rule set -----------------------------------------------------------------------------

class SegmentationConfig(_Model):
    method: str = "wrist_peaks"                              # a registered segmenter (ttcoach.segmentation)
    params: dict[str, Any] = Field(default_factory=dict)     # validated against the segmenter's params model
    contact_half_window_s: float = 0.03

    @model_validator(mode="after")
    def _check_segmenter(self) -> SegmentationConfig:
        get_segmenter(self.method, self.params)  # ValueError on unknown method / bad params
        return self


class ClassificationConfig(_Model):
    fh_lateral_offset_min: float = 0.0
    min_shoulder_width_ratio: float = 0.35
    side_fallback_bh_elbow_max: float = 100.0


class ViewConfig(_Model):
    side_max_ratio: float = 0.35
    front_min_ratio: float = 0.60
    min_valid_frame_ratio: float = 0.5


class Rule(_Model):
    id: str
    applies_to: list[StrokeType]
    phase: PhaseName | None = None
    metric: str
    op: Op
    min: float | None = None
    max: float | None = None
    severity: Severity
    message: str

    @model_validator(mode="after")
    def _check(self) -> Rule:
        if self.metric not in METRIC_UNITS:
            raise ValueError(
                f"rule {self.id!r}: unknown metric {self.metric!r}; valid: {', '.join(METRIC_UNITS)}"
            )
        if self.op in ("gt", "gte", "between", "outside") and self.min is None:
            raise ValueError(f"rule {self.id!r}: op {self.op!r} needs 'min'")
        if self.op in ("lt", "lte", "between", "outside") and self.max is None:
            raise ValueError(f"rule {self.id!r}: op {self.op!r} needs 'max'")
        try:
            self.message.format(value=0.0, min=self.min, max=self.max, unit=METRIC_UNITS[self.metric])
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(f"rule {self.id!r}: bad message template: {e!r}") from e
        return self


class RuleSet(_Model):
    schema_version: int = 1
    ruleset_id: str
    ruleset_version: str
    description: str = ""
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    view: ViewConfig = Field(default_factory=ViewConfig)
    rules: list[Rule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> RuleSet:
        ids = [r.id for r in self.rules]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate rule ids: {sorted(dup)}")
        return self


# ---- strokes ------------------------------------------------------------------------------

class Phase(_Model):
    name: PhaseName
    start_frame: int
    end_frame: int


class MetricValue(_Model):
    value: float
    unit: str


class Threshold(_Model):
    op: Op
    min: float | None = None
    max: float | None = None


class Feedback(_Model):
    rule_id: str
    metric: str
    value: float
    threshold: Threshold
    severity: Severity
    message: str
    phase: PhaseName | None = None


class Stroke(_Model):
    stroke_id: str
    type: StrokeType
    type_confidence: float
    view_class: ViewClass
    start_frame: int
    contact_frame: int
    end_frame: int
    start_s: float
    contact_s: float
    end_s: float
    phases: list[Phase]
    metrics: dict[str, MetricValue]
    feedback: list[Feedback]


class SkippedRule(_Model):
    stroke_id: str
    rule_id: str
    reason: str


class StrokesDoc(_Model):
    schema_version: int = 1
    analysis_rev: int
    ruleset_id: str
    ruleset_version: str
    handedness: Literal["right", "left"]
    strokes: list[Stroke]


# ---- angles -------------------------------------------------------------------------------

class AngleSeries(_Model):
    name: str
    label: str
    unit: str = "deg"
    definition: str
    values: list[float | None]
    velocity: list[float | None]
    velocity_unit: str = "deg/s"


class AnglesDoc(_Model):
    schema_version: int = 1
    analysis_rev: int
    fps: float
    n_frames: int
    t: list[float]
    series: list[AngleSeries]


# ---- report -------------------------------------------------------------------------------

class ViewInfo(_Model):
    class_: ViewClass = Field(alias="class")
    side_on: bool
    shoulder_width_ratio: float | None
    notice: str | None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Quality(_Model):
    valid_frame_ratio: float
    torso_len_px: float | None
    frame_drift: int = 0


class FeedbackCounts(_Model):
    info: int = 0
    warning: int = 0
    critical: int = 0


class Summary(_Model):
    n_strokes: int = 0
    n_fh: int = 0
    n_bh: int = 0
    feedback_counts: FeedbackCounts = Field(default_factory=FeedbackCounts)


class EstimatorInfo(_Model):
    name: str
    version: str
    confidence_kind: Literal["score", "presence"]


class RulesetInfo(_Model):
    id: str
    version: str
    path: str


class VideoInfo(_Model):
    fps: float
    n_frames: int
    width: int
    height: int
    duration_s: float


class ReportDoc(_Model):
    schema_version: int = 1
    analysis_rev: int
    job_id: str
    generated_at: str
    handedness: Literal["right", "left"]
    estimator: EstimatorInfo
    ruleset: RulesetInfo
    video: VideoInfo
    person_ids: list[str]
    view: ViewInfo
    quality: Quality
    summary: Summary
    skipped_rules: list[SkippedRule]
