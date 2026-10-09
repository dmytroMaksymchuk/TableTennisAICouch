"""Estimator registry (DESIGN §6.1)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ttcoach.errors import UnknownEstimator
from ttcoach.pose.base import EstimatorFactory, PoseEstimator

_REGISTRY: dict[str, EstimatorFactory] = {}


def register_estimator(name: str) -> Callable[[EstimatorFactory], EstimatorFactory]:
    def decorator(factory: EstimatorFactory) -> EstimatorFactory:
        _REGISTRY[name] = factory
        return factory

    return decorator


def get_estimator(name: str, options: Mapping[str, Any]) -> PoseEstimator:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise UnknownEstimator(name) from None
    return factory(options)


def available_estimators() -> list[str]:
    return sorted(_REGISTRY)
