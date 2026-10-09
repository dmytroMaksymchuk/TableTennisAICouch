"""Segmenter registry (mirrors ttcoach.pose.registry)."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from pydantic import ValidationError

from .base import Params, Segment, SegmentationInput, Segmenter

SegmentFn = Callable[[SegmentationInput, Any], list[Segment]]


@dataclass(frozen=True)
class SegmenterSpec:
    name: str
    fn: SegmentFn
    params: type[Params]

    @property
    def description(self) -> str:
        return inspect.getdoc(self.fn) or ""

    def defaults(self) -> dict[str, Any]:
        return self.params().model_dump()


_REGISTRY: dict[str, SegmenterSpec] = {}


def register_segmenter(name: str, params: type[Params] = Params) -> Callable[[SegmentFn], SegmentFn]:
    """Register ``fn(inp, params) -> list[Segment]`` under ``name``; ``params`` is its pydantic model."""

    def decorator(fn: SegmentFn) -> SegmentFn:
        if name in _REGISTRY:
            raise ValueError(f"segmenter {name!r} is already registered")
        _REGISTRY[name] = SegmenterSpec(name, fn, params)
        return fn

    return decorator


def segmenter_spec(name: str) -> SegmenterSpec:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown segmenter {name!r}; available: {', '.join(sorted(_REGISTRY))}") from None


def get_segmenter(name: str, params: Mapping[str, Any] | None = None) -> Segmenter:
    """The segmenter ``name`` bound to validated ``params`` (missing keys take the defaults).

    Raises ValueError for an unknown name or invalid params.
    """
    spec = segmenter_spec(name)
    try:
        p = spec.params.model_validate(dict(params or {}))
    except ValidationError as e:
        raise ValueError(f"invalid params for segmenter {name!r}:\n{e}") from None
    return partial(spec.fn, params=p)


def available_segmenters() -> list[SegmenterSpec]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
