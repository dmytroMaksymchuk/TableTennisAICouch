"""ffprobe / ffmpeg wrappers (DESIGN §3.1, §3.5, §5.1)."""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

from ttcoach.errors import PipelineError
from ttcoach.procs import RunChild, run_child

PROBE_TIMEOUT_S = 120.0
NORMALIZE_TIMEOUT_S = 3600.0
FRAME_TIMEOUT_S = 120.0


class ProbeError(Exception):
    pass


def _run_collect(run: RunChild, cmd: list[str], timeout_s: float) -> tuple[int | None, list[str], bool]:
    lines: list[str] = []
    res = run(cmd, timeout_s, lines.append)
    return res.returncode, lines, res.timed_out


def probe(ffprobe: str, path: Path, *, count_frames: bool = False, run: RunChild = run_child) -> dict[str, Any]:
    """ffprobe JSON (format + streams). Raises ProbeError if unreadable or without a video stream."""
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    if count_frames:
        cmd += ["-count_frames", "-select_streams", "v:0"]
    cmd.append(str(path))
    rc, lines, timed_out = _run_collect(run, cmd, PROBE_TIMEOUT_S)
    if timed_out or rc != 0:
        raise ProbeError(f"ffprobe failed (exit {rc}): {' | '.join(lines[-5:])}")
    try:
        doc = json.loads("\n".join(lines))
    except json.JSONDecodeError as e:
        raise ProbeError(f"ffprobe returned invalid JSON: {e}") from None
    if video_stream(doc) is None:
        raise ProbeError("no video stream")
    return doc


def video_stream(doc: dict[str, Any]) -> dict[str, Any] | None:
    for s in doc.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def _rate(value: str | None) -> float:
    try:
        r = Fraction(value or "0/1")
    except (ValueError, ZeroDivisionError):
        return 0.0
    return float(r)


def source_fps(doc: dict[str, Any]) -> float:
    s = video_stream(doc) or {}
    return _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate"))


def duration_s(doc: dict[str, Any]) -> float:
    """Container duration, else video-stream duration, else frame count / fps; 0.0 if unknown."""
    stream = video_stream(doc) or {}
    for value in ((doc.get("format") or {}).get("duration"), stream.get("duration")):
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except ValueError:
            continue
    fps = source_fps(doc)
    for key in ("nb_frames", "nb_read_frames"):
        try:
            frames = int(stream.get(key) or 0)
        except ValueError:
            continue
        if frames > 0 and fps > 0:
            return frames / fps
    return 0.0


def target_fps(doc: dict[str, Any], max_fps: int) -> int:
    fps = source_fps(doc)
    return max(1, min(max_fps, round(fps))) if fps > 0 else min(30, max_fps)


def normalize_cmd(
    ffmpeg: str, src: Path, dst: Path, *, fps: int, max_height: int,
    trim_start_s: float | None, trim_end_s: float | None,
) -> list[str]:  # fmt: skip
    cmd = [ffmpeg, "-y", "-hide_banner", "-progress", "pipe:1", "-nostats"]
    if trim_start_s:
        cmd += ["-ss", f"{trim_start_s:.6f}"]
    if trim_end_s is not None:
        cmd += ["-to", f"{trim_end_s:.6f}"]
    cmd += [
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:'min({max_height},ih)'",
        "-r", str(fps), "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    return cmd


_OUT_TIME = re.compile(r"^out_time_us=(\d+)")


def normalize(
    ffmpeg: str, ffprobe: str, src: Path, video_dir: Path, *,
    source_probe: dict[str, Any], max_fps: int, max_height: int,
    trim_start_s: float | None, trim_end_s: float | None,
    progress: Callable[[float | None], None], run: RunChild = run_child,
) -> dict[str, Any]:  # fmt: skip
    """Write `video/normalized.mp4` then `video/probe.json` (completion marker). Returns VideoInfo dict."""
    video_dir.mkdir(parents=True, exist_ok=True)
    dst = video_dir / "normalized.mp4"
    fps = target_fps(source_probe, max_fps)
    total = duration_s(source_probe)
    end = total if trim_end_s is None else (min(trim_end_s, total) if total else trim_end_s)
    expected = max(1e-6, end - (trim_start_s or 0.0))

    def on_line(line: str) -> None:
        m = _OUT_TIME.match(line)
        if m:
            progress(min(1.0, int(m.group(1)) / 1e6 / expected))

    cmd = normalize_cmd(
        ffmpeg, src, dst, fps=fps, max_height=max_height, trim_start_s=trim_start_s, trim_end_s=trim_end_s
    )
    res = run(cmd, NORMALIZE_TIMEOUT_S, on_line)
    if res.timed_out:
        raise PipelineError("ffmpeg_failed", "ffmpeg timed out", "normalizing")
    if res.returncode != 0:
        raise PipelineError("ffmpeg_failed", f"ffmpeg exit {res.returncode}: {res.tail_text()[-800:]}", "normalizing")
    try:
        doc = probe(ffprobe, dst, count_frames=True, run=run)
    except ProbeError as e:
        raise PipelineError("ffmpeg_failed", f"normalized video unreadable: {e}", "normalizing") from None
    info = check_normalized(doc, fps)
    (video_dir / "probe.json.tmp").write_text(json.dumps(doc), encoding="utf-8")
    (video_dir / "probe.json.tmp").replace(video_dir / "probe.json")
    return info


def check_normalized(doc: dict[str, Any], fps: int) -> dict[str, Any]:
    """Post-condition of DESIGN §5.1; returns the VideoInfo dict."""
    s = video_stream(doc) or {}
    start = float(s.get("start_time") or 0.0)
    if abs(start) > 1e-3:
        raise PipelineError("ffmpeg_failed", f"normalized start_time {start} != 0", "normalizing")
    avg = _rate(s.get("avg_frame_rate"))
    if abs(avg - fps) > 1e-6:
        raise PipelineError("ffmpeg_failed", f"normalized avg_frame_rate {avg} != {fps}", "normalizing")
    n = int(s.get("nb_read_frames") or 0)
    if n < 1:
        raise PipelineError("ffmpeg_failed", "normalized video has no frames", "normalizing")
    return normalized_video_info(doc)


def normalized_video_info(doc: dict[str, Any]) -> dict[str, Any]:
    """VideoInfo dict from `video/probe.json` (ffprobe -count_frames of the normalized video)."""
    s = video_stream(doc) or {}
    fps = round(_rate(s.get("avg_frame_rate")), 6)
    n = int(s.get("nb_read_frames") or 0)
    return {
        "fps": float(fps),
        "n_frames": n,
        "width": int(s["width"]),
        "height": int(s["height"]),
        "duration_s": n / fps,
    }


_frame_locks: dict[Path, threading.Lock] = {}
_frame_locks_guard = threading.Lock()


def _frame_lock(dst: Path) -> threading.Lock:
    with _frame_locks_guard:
        return _frame_locks.setdefault(dst.resolve(), threading.Lock())


def extract_frame(ffmpeg: str, video: Path, n: int, dst: Path, *, run: RunChild = run_child) -> Path:
    """JPEG of frame n (full resolution), cached at dst. Safe under concurrent requests for the same frame."""
    if dst.is_file():
        return dst
    with _frame_lock(dst):
        if dst.is_file():  # another request finished it while we waited
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.stem}.{uuid.uuid4().hex}.tmp.jpg")
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
            "-vf", f"select=eq(n\\,{n})", "-vframes", "1", "-q:v", "3", str(tmp),
        ]  # fmt: skip
        res = run(cmd, FRAME_TIMEOUT_S, None)
        if not res.ok or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            raise PipelineError("ffmpeg_failed", f"frame {n} extraction failed: {res.tail_text()[-400:]}")
        try:
            tmp.replace(dst)
        except PermissionError:
            # Windows: dst is open by a concurrent reader (e.g. another process serving it); it is complete.
            tmp.unlink(missing_ok=True)
            if not dst.is_file():
                raise
        return dst
