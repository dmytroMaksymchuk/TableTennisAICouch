"""Rule loading and evaluation (plan 4.4)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Feedback, MetricValue, Rule, RuleSet, SkippedRule, StrokeType, Threshold


def load_ruleset(path: str | Path) -> RuleSet:
    """Read and validate a rules YAML file. Raises ValueError with a readable message on any problem."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"cannot read rules file {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"rules file {path} must contain a mapping")
    try:
        return RuleSet.model_validate(data)
    except ValidationError as e:  # ValidationError is a ValueError; re-raise with the path for context
        raise ValueError(f"invalid rules file {path}:\n{e}") from e


def violates(rule: Rule, value: float) -> bool:
    lo, hi = rule.min, rule.max
    match rule.op:
        case "lt":
            return value >= hi
        case "lte":
            return value > hi
        case "gt":
            return value <= lo
        case "gte":
            return value < lo
        case "between":
            return value < lo or value > hi
        case "outside":
            return lo <= value <= hi
    raise ValueError(f"unknown op {rule.op!r}")


def evaluate_rules(
    ruleset: RuleSet, stroke_id: str, stroke_type: StrokeType, metrics: dict[str, MetricValue]
) -> tuple[list[Feedback], list[SkippedRule]]:
    """Feedback for violated rules; rules whose metric is unavailable are returned as skipped."""
    feedback: list[Feedback] = []
    skipped: list[SkippedRule] = []
    for rule in ruleset.rules:
        if stroke_type not in rule.applies_to:
            continue
        m = metrics.get(rule.metric)
        if m is None:
            skipped.append(SkippedRule(stroke_id=stroke_id, rule_id=rule.id, reason="metric_unavailable"))
            continue
        if not violates(rule, m.value):
            continue
        feedback.append(
            Feedback(
                rule_id=rule.id,
                metric=rule.metric,
                value=m.value,
                threshold=Threshold(op=rule.op, min=rule.min, max=rule.max),
                severity=rule.severity,
                message=rule.message.format(value=m.value, min=rule.min, max=rule.max, unit=m.unit),
                phase=rule.phase,
            )
        )
    return feedback, skipped
