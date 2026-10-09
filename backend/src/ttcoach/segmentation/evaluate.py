"""Scoring segmenter output against hand-labelled strokes. Pure; no job / file-system access except label loading."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .base import StrokeType

DEFAULT_TOLERANCE_S = 0.15


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelledStroke(_Model):
    contact_s: float                    # seconds on the job's normalized video (= results-page timeline)
    type: StrokeType | None = None
    start_s: float | None = None
    end_s: float | None = None

    @model_validator(mode="after")
    def _order(self) -> LabelledStroke:
        if (self.start_s is None) != (self.end_s is None):
            raise ValueError("give both start_s and end_s, or neither")
        if self.start_s is not None and not (self.start_s <= self.contact_s <= self.end_s):
            raise ValueError(f"need start_s <= contact_s <= end_s (contact_s={self.contact_s})")
        return self


class Labels(_Model):
    job: str | None = None
    handedness: Literal["right", "left"] | None = None   # overrides the job's setting
    notes: str = ""
    strokes: list[LabelledStroke] = Field(default_factory=list)


class Experiment(_Model):
    name: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class ExperimentsFile(_Model):
    experiments: list[Experiment]

    @model_validator(mode="after")
    def _unique(self) -> ExperimentsFile:
        names = [e.name for e in self.experiments]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate experiment names: {dup}")
        return self


class Benchmark(_Model):
    tolerance_s: float = DEFAULT_TOLERANCE_S
    labels: list[str]                   # label files or glob patterns, relative to the benchmark file


def load_yaml_model(path: str | Path, model: type[BaseModel]) -> Any:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"cannot read {path}: {e}") from e
    try:
        return model.model_validate(data or {})
    except ValidationError as e:
        raise ValueError(f"invalid {path}:\n{e}") from e


def benchmark_label_paths(bench_path: Path, bench: Benchmark) -> list[Path]:
    base = bench_path.parent
    out: list[Path] = []
    for pattern in bench.labels:
        hits = sorted(base.glob(pattern)) if any(ch in pattern for ch in "*?[") else [base / pattern]
        if not hits:
            raise ValueError(f"benchmark {bench_path}: no label files match {pattern!r}")
        out.extend(hits)
    return out


# ---- scoring -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Predicted:
    start_s: float
    contact_s: float
    end_s: float
    type: StrokeType


@dataclass
class Score:
    n_pred: int = 0
    n_gt: int = 0
    tp: int = 0
    abs_err_sum_s: float = 0.0
    type_n: int = 0
    type_correct: int = 0
    iou_n: int = 0
    iou_sum: float = 0.0
    matches: list[tuple[int, int]] = field(default_factory=list)   # (pred index, gt index)

    @property
    def fp(self) -> int:
        return self.n_pred - self.tp

    @property
    def fn(self) -> int:
        return self.n_gt - self.tp

    @property
    def precision(self) -> float:
        return self.tp / self.n_pred if self.n_pred else math.nan

    @property
    def recall(self) -> float:
        return self.tp / self.n_gt if self.n_gt else math.nan

    @property
    def f1(self) -> float:
        d = self.n_pred + self.n_gt
        return 2 * self.tp / d if d else math.nan

    @property
    def contact_mae_ms(self) -> float:
        return 1000 * self.abs_err_sum_s / self.tp if self.tp else math.nan

    @property
    def type_accuracy(self) -> float:
        return self.type_correct / self.type_n if self.type_n else math.nan

    @property
    def mean_iou(self) -> float:
        return self.iou_sum / self.iou_n if self.iou_n else math.nan

    def __add__(self, other: Score) -> Score:
        return Score(
            self.n_pred + other.n_pred, self.n_gt + other.n_gt, self.tp + other.tp,
            self.abs_err_sum_s + other.abs_err_sum_s, self.type_n + other.type_n,
            self.type_correct + other.type_correct, self.iou_n + other.iou_n, self.iou_sum + other.iou_sum,
        )  # fmt: skip

    def summary(self) -> dict[str, float | int | None]:
        def r(x: float, nd: int) -> float | None:
            return None if math.isnan(x) else round(x, nd)

        return {
            "n_pred": self.n_pred, "n_gt": self.n_gt, "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": r(self.precision, 3), "recall": r(self.recall, 3), "f1": r(self.f1, 3),
            "contact_mae_ms": r(self.contact_mae_ms, 1), "type_accuracy": r(self.type_accuracy, 3),
            "mean_iou": r(self.mean_iou, 3),
        }  # fmt: skip


def _iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def score(pred: list[Predicted], gt: list[LabelledStroke], tolerance_s: float = DEFAULT_TOLERANCE_S) -> Score:
    """One-to-one matching of predicted to labelled contacts, closest pairs first, within ``tolerance_s``."""
    pairs = sorted(
        (abs(p.contact_s - g.contact_s), i, j)
        for i, p in enumerate(pred)
        for j, g in enumerate(gt)
        if abs(p.contact_s - g.contact_s) <= tolerance_s
    )
    s = Score(n_pred=len(pred), n_gt=len(gt))
    used_p: set[int] = set()
    used_g: set[int] = set()
    for dt, i, j in pairs:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        s.tp += 1
        s.abs_err_sum_s += dt
        s.matches.append((i, j))
        g, p = gt[j], pred[i]
        if g.type is not None:
            s.type_n += 1
            s.type_correct += p.type == g.type
        if g.start_s is not None and g.end_s is not None:
            s.iou_n += 1
            s.iou_sum += _iou(p.start_s, p.end_s, g.start_s, g.end_s)
    s.matches.sort()
    return s
