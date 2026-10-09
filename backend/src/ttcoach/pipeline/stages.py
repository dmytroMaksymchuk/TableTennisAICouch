"""Pipeline stages and completion markers (DESIGN §2.1, §2.2, §2.4, §5)."""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ttcoach.coaching import analyze, load_ruleset
from ttcoach.coaching.models import AnglesDoc, EstimatorInfo, ReportDoc, RulesetInfo, StrokesDoc
from ttcoach.coaching.models import VideoInfo as ReportVideoInfo
from ttcoach.config import REPO_ROOT, Settings
from ttcoach.errors import ChildCancelled, PipelineError, UnknownEstimator
from ttcoach.jobs.models import Job, Stage
from ttcoach.jobs.repository import FileJobRepository, retry_sharing
from ttcoach.media import ffmpeg
from ttcoach.pose.align import align_pose
from ttcoach.pose.base import PoseRunContext, VideoMeta
from ttcoach.pose.persons import candidates, merge_tracks
from ttcoach.pose.registry import get_estimator
from ttcoach.pose.schema import (
    CANONICAL_JOINTS,
    SKELETON_EDGES,
    PersonTrack,
    complete_derived,
    person_to_columns,
    pose_from_json,
    pose_to_json,
)
from ttcoach.procs import RunChild

logger = logging.getLogger(__name__)

ARTIFACTS = ("keypoints", "angles", "strokes", "report")
KEEP_REVISIONS = 2
_REV_DIR = re.compile(r"^rev-(\d{4,})$")


class KeypointsDoc(BaseModel):
    """`results/keypoints` (DESIGN §4.4)."""

    schema_version: int = 1
    analysis_rev: int
    fps: float
    n_frames: int
    width: int
    height: int
    person_ids: list[str]
    joints: list[str]
    edges: list[tuple[str, str]]
    x: list[list[float | None]]
    y: list[list[float | None]]
    conf: list[list[float | None]]
    x_smooth: list[list[float | None]]
    y_smooth: list[list[float | None]]


RESULT_MODELS: dict[str, type[BaseModel]] = {
    "keypoints": KeypointsDoc,
    "angles": AnglesDoc,
    "strokes": StrokesDoc,
    "report": ReportDoc,
}


# --------------------------------------------------------------------------- context
@dataclass
class StageContext:
    repo: FileJobRepository
    settings: Settings
    job_id: str
    run_child: RunChild

    def path(self, rel: str) -> Path:
        return self.repo.path(self.job_id, rel)

    def log(self, msg: str) -> None:
        p = self.path("logs/pipeline.log")
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")

    def progress_reporter(self) -> Callable[[float | None], None]:
        """Throttled job.json progress updates (at most every 0.5 s, plus None and completion)."""
        last = [0.0]

        def report(value: float | None) -> None:
            now = time.monotonic()
            if value is not None and value < 1.0 and now - last[0] < 0.5:
                return
            last[0] = now
            self.repo.update(self.job_id, progress=None if value is None else round(min(1.0, value), 3))

        return report


# --------------------------------------------------------------------------- completion markers
def _parses(path: Path) -> bool:
    try:
        json.loads(retry_sharing(path.read_bytes))
        return True
    except (OSError, ValueError):
        return False


def normalize_complete(repo: FileJobRepository, job_id: str) -> bool:
    return repo.path(job_id, "video/normalized.mp4").is_file() and _parses(repo.path(job_id, "video/probe.json"))


def pose_complete(repo: FileJobRepository, job_id: str) -> bool:
    return _parses(repo.path(job_id, "pose/persons.json")) and _parses(repo.path(job_id, "pose/candidates.json"))


def rev_dir(repo: FileJobRepository, job_id: str, rev: int) -> Path:
    return repo.path(job_id, f"analysis/rev-{rev:04d}")


def analysis_complete(repo: FileJobRepository, job: Job) -> bool:
    return job.analysis_rev > 0 and _parses(rev_dir(repo, job.id, job.analysis_rev) / "report.json")


def first_incomplete_stage(repo: FileJobRepository, job: Job) -> Stage:
    if not normalize_complete(repo, job.id):
        return "normalizing"
    if not pose_complete(repo, job.id):
        return "estimating_pose"
    return "analyzing"


# --------------------------------------------------------------------------- normalizing
def run_normalize(ctx: StageContext) -> Job:
    s = ctx.settings
    if not s.ffmpeg_path or not s.ffprobe_path:
        raise PipelineError("ffmpeg_failed", "ffmpeg/ffprobe not found (set TTCOACH_FFMPEG_PATH)", "normalizing")
    job = ctx.repo.get(ctx.job_id)
    src = ctx.repo.input_path(ctx.job_id)
    probe_path = ctx.path("input/probe.json")
    if _parses(probe_path):
        source_probe = json.loads(probe_path.read_text(encoding="utf-8"))
    else:
        try:
            source_probe = ffmpeg.probe(s.ffprobe_path, src, run=ctx.run_child)
        except ffmpeg.ProbeError as e:
            raise PipelineError("ffmpeg_failed", f"input unreadable: {e}", "normalizing") from None
    video_dir = ctx.path("video")
    (video_dir / "probe.json").unlink(missing_ok=True)
    t0 = time.monotonic()
    info = ffmpeg.normalize(
        s.ffmpeg_path, s.ffprobe_path, src, video_dir,
        source_probe=source_probe, max_fps=s.max_fps, max_height=s.max_video_height,
        trim_start_s=job.settings.trim_start_s, trim_end_s=job.settings.trim_end_s,
        progress=ctx.progress_reporter(), run=ctx.run_child,
    )  # fmt: skip
    ctx.log(f"normalizing done in {time.monotonic() - t0:.1f}s: {info}")
    return ctx.repo.update(ctx.job_id, video=info)


# --------------------------------------------------------------------------- estimating_pose
def clear_pose_dir(ctx: StageContext, estimator: str) -> None:
    pose_dir = ctx.path("pose")
    work = pose_dir / estimator
    try:
        if work.exists():
            shutil.rmtree(work)
        for p in pose_dir.glob("*.json*"):
            p.unlink()
    except OSError as e:
        raise PipelineError(
            "pose_failed",
            f"cannot clear pose work dir {work}: {e}; close programs using it and POST /retry",
            "estimating_pose",
        ) from None
    work.mkdir(parents=True, exist_ok=True)


def ensure_video(ctx: StageContext, job: Job) -> Job:
    """job.video is written after the normalize markers; rebuild it from video/probe.json if a crash
    happened in between."""
    if job.video is not None:
        return job
    if not normalize_complete(ctx.repo, job.id):
        raise PipelineError("ffmpeg_failed", "normalized video missing", "normalizing")
    info = ffmpeg.normalized_video_info(ctx.repo.read_json(job.id, "video/probe.json"))
    ctx.log(f"rebuilt job.video from video/probe.json: {info}")
    return ctx.repo.update(job.id, video=info)


def video_meta(ctx: StageContext, job: Job) -> VideoMeta:
    job = ensure_video(ctx, job)
    assert job.video is not None
    v = job.video
    return VideoMeta(ctx.path("video/normalized.mp4"), v.fps, v.n_frames, v.width, v.height, v.duration_s)


def run_pose(ctx: StageContext) -> dict[str, Any]:
    """Returns the candidates document (written last, as the completion marker)."""
    job = ctx.repo.get(ctx.job_id)
    name = job.settings.estimator
    clear_pose_dir(ctx, name)
    try:
        estimator = get_estimator(name, ctx.settings.options_for(name))
    except UnknownEstimator:
        raise PipelineError("pose_failed", f"unknown pose estimator {name!r}", "estimating_pose") from None
    meta = video_meta(ctx, job)
    pctx = PoseRunContext(
        work_dir=ctx.path(f"pose/{name}"), log=ctx.log, progress=ctx.progress_reporter(), run_child=ctx.run_child
    )
    t0 = time.monotonic()
    try:
        raw = estimator.estimate(meta, pctx)
    except (PipelineError, ChildCancelled):
        raise
    except Exception as e:  # adapter bugs, parse errors, ...
        logger.exception("estimator %s failed", name)
        raise PipelineError("pose_failed", f"{name}: {type(e).__name__}: {e}", "estimating_pose") from None
    seq = align_pose(raw, meta, ctx.log)
    ctx.log(f"estimating_pose done in {time.monotonic() - t0:.1f}s: {len(seq.persons)} persons, drift {seq.frame_drift}")
    ctx.repo.write_json(ctx.job_id, "pose/persons.json", pose_to_json(seq))
    doc = candidates(seq)
    ctx.repo.write_json(ctx.job_id, "pose/candidates.json", doc)
    return doc


# --------------------------------------------------------------------------- analyzing
def _write_artifact(dir_: Path, name: str, model: BaseModel) -> None:
    data = json.dumps(model.model_dump(mode="json", by_alias=True), ensure_ascii=False, separators=(",", ":"))
    raw = data.encode("utf-8")
    (dir_ / f"{name}.json").write_bytes(raw)
    (dir_ / f"{name}.json.gz").write_bytes(gzip.compress(raw, compresslevel=6))


def existing_revs(analysis_dir: Path) -> list[int]:
    if not analysis_dir.is_dir():
        return []
    revs = []
    for d in analysis_dir.iterdir():
        m = _REV_DIR.match(d.name)
        if m and d.is_dir():
            revs.append(int(m.group(1)))
    return sorted(revs)


def _ruleset_display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def prune_revisions(ctx: StageContext, keep: int = KEEP_REVISIONS) -> None:
    """Best-effort: errors (e.g. Windows sharing locks) are logged and retried on the next run."""
    analysis_dir = ctx.path("analysis")
    for rev in existing_revs(analysis_dir)[:-keep]:
        d = analysis_dir / f"rev-{rev:04d}"
        try:
            shutil.rmtree(d)
        except OSError as e:
            ctx.log(f"WARNING: could not prune {d}: {e}")
            logger.warning("could not prune %s: %s", d, e)


def run_analysis(ctx: StageContext) -> Job:
    job = ctx.repo.get(ctx.job_id)
    analysis_dir = ctx.path("analysis")
    analysis_dir.mkdir(parents=True, exist_ok=True)
    for stale in analysis_dir.glob("rev-*.tmp"):
        shutil.rmtree(stale, ignore_errors=True)
        if stale.exists():
            raise PipelineError("analysis_failed", f"cannot remove stale {stale}", "analyzing")
    revs = existing_revs(analysis_dir)
    rev = max([job.analysis_rev, *revs]) + 1

    try:
        ruleset = load_ruleset(ctx.settings.rules_path)
    except ValueError as e:
        raise PipelineError("analysis_failed", str(e), "analyzing") from None
    seq = pose_from_json(ctx.repo.read_json(ctx.job_id, "pose/persons.json"))
    selection = ctx.repo.read_json(ctx.job_id, "pose/selection.json")
    person_ids: list[str] = list(selection["person_ids"])
    try:
        track: PersonTrack = complete_derived(merge_tracks(seq, person_ids))
    except KeyError as e:
        raise PipelineError("analysis_failed", f"selected person {e} not in pose data", "analyzing") from None

    t0 = time.monotonic()
    handedness = job.settings.handedness
    try:
        res = analyze(
            track.xy, track.conf, seq.fps, handedness, ruleset,
            frame_drift=seq.frame_drift, cutoff_hz_max=ctx.settings.smoothing_cutoff_hz_max,
            video_path=ctx.path("video/normalized.mp4"),
        )  # fmt: skip
    except Exception as e:
        logger.exception("analysis failed")
        raise PipelineError("analysis_failed", f"{type(e).__name__}: {e}", "analyzing") from None

    smooth = PersonTrack(track.person_id, res.xy_smooth, track.conf)
    raw_cols, smooth_cols = person_to_columns(track), person_to_columns(smooth)
    job = ensure_video(ctx, job)
    assert job.video is not None
    docs: dict[str, BaseModel] = {
        "keypoints": KeypointsDoc(
            analysis_rev=rev, fps=seq.fps, n_frames=seq.n_frames, width=seq.width, height=seq.height,
            person_ids=person_ids, joints=list(CANONICAL_JOINTS), edges=list(SKELETON_EDGES),
            x=raw_cols["x"], y=raw_cols["y"], conf=raw_cols["conf"],
            x_smooth=smooth_cols["x"], y_smooth=smooth_cols["y"],
        ),
        "angles": res.angles.model_copy(update={"analysis_rev": rev}),
        "strokes": StrokesDoc(
            analysis_rev=rev, ruleset_id=ruleset.ruleset_id, ruleset_version=ruleset.ruleset_version,
            handedness=handedness, strokes=res.strokes,
        ),
        "report": ReportDoc(
            analysis_rev=rev, job_id=job.id,
            generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            handedness=handedness,
            estimator=EstimatorInfo(
                name=seq.estimator, version=seq.estimator_version, confidence_kind=seq.confidence_kind
            ),
            ruleset=RulesetInfo(
                id=ruleset.ruleset_id, version=ruleset.ruleset_version,
                path=_ruleset_display_path(ctx.settings.rules_path),
            ),
            video=ReportVideoInfo(**job.video.model_dump()),
            person_ids=person_ids, view=res.view, quality=res.quality, summary=res.summary,
            skipped_rules=res.skipped_rules,
        ),
    }  # fmt: skip

    tmp = analysis_dir / f"rev-{rev:04d}.tmp"
    tmp.mkdir()
    for name, model in docs.items():
        _write_artifact(tmp, name, model)
    os.replace(tmp, rev_dir(ctx.repo, ctx.job_id, rev))
    job = ctx.repo.update(
        ctx.job_id, analysis_rev=rev, person_ids=person_ids, ruleset_version=ruleset.ruleset_version
    )
    ctx.log(
        f"analyzing done in {time.monotonic() - t0:.1f}s: rev {rev}, {res.summary.n_strokes} strokes, "
        f"ruleset {ruleset.ruleset_version}"
    )
    prune_revisions(ctx)
    return job
