"""Pluggable stroke segmentation.

A segmenter is a function ``fn(inp: SegmentationInput, params) -> list[Segment]`` registered with
``@register_segmenter(name, ParamsModel)``. Adding one = a module in this package plus one import
line below. Select it in the rules YAML (``segmentation.method`` / ``segmentation.params``) or
compare it against the others with ``ttcoach-seg``.
"""

from .base import Params, Segment, SegmentationInput, Segmenter, StrokeType, prepare_input, sanitize
from .features import wrist_speed
from .registry import available_segmenters, get_segmenter, register_segmenter, segmenter_spec

from . import wrist_peaks as _wrist_peaks  # noqa: E402, F401  (registration)

__all__ = [
    "Params",
    "Segment",
    "SegmentationInput",
    "Segmenter",
    "StrokeType",
    "available_segmenters",
    "get_segmenter",
    "prepare_input",
    "register_segmenter",
    "sanitize",
    "segmenter_spec",
    "wrist_speed",
]
