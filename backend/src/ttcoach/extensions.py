"""Extension points for later versions (plan §9). Signatures only — no implementations in v1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class MetricDistribution:
    mean: float
    std: float
    p10: float
    p90: float
    n: int
    source: str


@dataclass(frozen=True)
class AdviceItem:
    stroke_id: str | None
    text: str
    cites: list[str]  # rule_ids


class ReferenceProvider(Protocol):
    def reference_metrics(self, stroke_type: str) -> Mapping[str, MetricDistribution]: ...


class AdviceGenerator(Protocol):
    def advise(self, report: Mapping[str, Any], strokes: Sequence[Mapping[str, Any]]) -> list[AdviceItem]: ...
