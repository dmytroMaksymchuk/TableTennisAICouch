"""AC4: a new estimator is registered without editing any other ttcoach module."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from ttcoach.config import Settings
from ttcoach.errors import UnknownEstimator
from ttcoach.pose.base import PoseRunContext, VideoMeta
from ttcoach.pose.registry import available_estimators, get_estimator, register_estimator
from ttcoach.pose.schema import CANONICAL_JOINTS, RawPose


class StubEstimator:
    name = "stub-registry"

    def __init__(self, options: Mapping[str, Any]) -> None:
        self.options = dict(options)

    def version(self) -> str:
        return "0"

    def check_available(self) -> None:
        return None

    def estimate(self, video: VideoMeta, ctx: PoseRunContext) -> RawPose:
        return RawPose("stub-registry", "0", CANONICAL_JOINTS, (), video.n_frames, "score")


@register_estimator("stub-registry")
def _stub_factory(options: Mapping[str, Any]) -> StubEstimator:
    return StubEstimator(options)


def test_stub_selected_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTCOACH_POSE_ESTIMATOR", "stub-registry")
    monkeypatch.setenv("TTCOACH_ESTIMATOR_OPTIONS", '{"stub-registry": {"alpha": 1}}')
    s = Settings()
    est = get_estimator(s.pose_estimator, s.options_for(s.pose_estimator))
    assert isinstance(est, StubEstimator)
    assert est.options == {"alpha": 1}


def test_builtin_and_unknown() -> None:
    import ttcoach.pose  # noqa: F401  (registers built-ins)

    assert {"sports2d", "stub-registry"} <= set(available_estimators())
    with pytest.raises(UnknownEstimator):
        get_estimator("nope", {})
