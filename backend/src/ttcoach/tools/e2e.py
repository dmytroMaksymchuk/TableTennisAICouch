"""`ttcoach-e2e`: drive a running backend over HTTP and check the results (plan B5, Step I)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from ttcoach.config import REPO_ROOT, Settings
from ttcoach.pipeline.stages import ARTIFACTS, RESULT_MODELS

FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
)


class E2EError(Exception):
    pass


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ttcoach-e2e", description=__doc__)
    p.add_argument("--video", type=Path, help="video to upload")
    p.add_argument("--handedness", choices=["right", "left"], default="right")
    p.add_argument("--trim-start", type=float)
    p.add_argument("--trim-end", type=float)
    p.add_argument("--person", default="auto", help="auto | <id>[,<id>...] (used on awaiting_selection)")
    p.add_argument("--render-frames", help="first,mid,last | <n>,<n>,... — draw smoothed keypoints into PNGs")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "e2e")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--job", help="existing job id")
    p.add_argument("--check-only", action="store_true", help="with --job: only check job + results")
    p.add_argument("--timeout", type=float, default=3600.0, help="max seconds to wait for the job")
    p.add_argument("--poll-interval", type=float, default=1.0, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.check_only and not args.job:
        p.error("--check-only requires --job")
    if not args.job and not args.video:
        p.error("one of --video or --job is required")
    return args


def _json(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        try:
            err = r.json()["error"]
            raise E2EError(f"{r.request.method} {r.request.url.path} -> {r.status_code} {err['code']}: {err['message']}")
        except (ValueError, KeyError):
            raise E2EError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}") from None
    return r.json()


def upload(client: httpx.Client, args: argparse.Namespace) -> str:
    data = {"handedness": args.handedness}
    if args.trim_start is not None:
        data["trim_start_s"] = str(args.trim_start)
    if args.trim_end is not None:
        data["trim_end_s"] = str(args.trim_end)
    with open(args.video, "rb") as f:
        job = _json(client.post("/api/jobs", files={"file": (args.video.name, f)}, data=data))
    print(f"job {job['id']} created")
    return job["id"]


def wait(client: httpx.Client, job_id: str, args: argparse.Namespace) -> dict[str, Any]:
    deadline = time.monotonic() + args.timeout
    last = None
    while True:
        job = _json(client.get(f"/api/jobs/{job_id}"))
        state = (job["status"], job["stage"])
        if state != last:
            print(f"  {job['status']} / {job['stage']}")
            last = state
        if job["status"] == "awaiting_selection":
            if args.person == "auto":
                ids = _json(client.get(f"/api/jobs/{job_id}/candidates"))["suggested_person_ids"]
            else:
                ids = [p.strip() for p in args.person.split(",") if p.strip()]
            print(f"  selecting {ids}")
            _json(client.post(f"/api/jobs/{job_id}/selection", json={"person_ids": ids}))
        elif job["status"] == "failed":
            e = job["error"] or {}
            raise E2EError(f"job failed: {e.get('code')} in {e.get('stage')}: {e.get('message')}")
        elif job["status"] == "done":
            return job
        if time.monotonic() > deadline:
            raise E2EError(f"timed out after {args.timeout:g} s (status {job['status']})")
        time.sleep(args.poll_interval)


def check(client: httpx.Client, job_id: str) -> dict[str, Any]:
    """Job is done and all four artifacts return 200 and validate."""
    job = _json(client.get(f"/api/jobs/{job_id}"))
    if job["status"] != "done":
        raise E2EError(f"job {job_id} is {job['status']}, expected done")
    docs: dict[str, Any] = {}
    for name in ARTIFACTS:
        doc = _json(client.get(f"/api/jobs/{job_id}/results/{name}", params={"rev": job["analysis_rev"]}))
        RESULT_MODELS[name].model_validate(doc)
        if doc["analysis_rev"] != job["analysis_rev"]:
            raise E2EError(f"{name}: analysis_rev {doc['analysis_rev']} != job {job['analysis_rev']}")
        docs[name] = doc
    report = docs["report"]
    print(f"job {job_id}: done, analysis_rev {job['analysis_rev']}, persons {job['person_ids']}")
    print(f"  strokes: {report['summary']['n_strokes']} (FH {report['summary']['n_fh']}, BH {report['summary']['n_bh']})")
    print(f"  view: {report['view']['class']} (side_on={report['view']['side_on']})")
    print(f"  feedback: {report['summary']['feedback_counts']}")
    print(f"  frame_drift: {report['quality']['frame_drift']}")
    return docs


def frame_numbers(spec: str, n_frames: int) -> list[int]:
    named = {"first": 0, "mid": n_frames // 2, "last": n_frames - 1}
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        n = named[part] if part in named else int(part)
        if not 0 <= n < n_frames:
            raise E2EError(f"frame {n} outside 0..{n_frames - 1}")
        out.append(n)
    return out


def _initials(joint: str) -> str:
    return "".join(p[0] for p in joint.split("_"))


def render_frames(client: httpx.Client, job_id: str, keypoints: dict[str, Any], spec: str, out: Path) -> list[Path]:
    ffmpeg = Settings().ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise E2EError("ffmpeg not found for --render-frames")
    font = next((f for f in FONT_CANDIDATES if f.is_file()), None)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for n in frame_numbers(spec, keypoints["n_frames"]):
        r = client.get(f"/api/jobs/{job_id}/frames/{n}.jpg")
        if r.status_code != 200:
            raise E2EError(f"frame {n}: HTTP {r.status_code}")
        jpg = out / f"frame_{n}.jpg"
        jpg.write_bytes(r.content)
        filters = []
        for j, name in enumerate(keypoints["joints"]):
            x, y = keypoints["x_smooth"][j][n], keypoints["y_smooth"][j][n]
            if x is None or y is None:
                continue
            filters.append(f"drawbox=x={x - 4:.0f}:y={y - 4:.0f}:w=8:h=8:color=red@0.9:t=fill")
            if font is not None:
                fontfile = font.as_posix().replace(":", "\\:")
                filters.append(
                    f"drawtext=fontfile='{fontfile}':text='{_initials(name)}':x={x + 5:.0f}:y={y - 12:.0f}"
                    ":fontsize=12:fontcolor=yellow"
                )
        png = out / f"frame_{n}.png"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(jpg)]
        if filters:
            cmd += ["-vf", ",".join(filters)]
        cmd.append(str(png))
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
        if res.returncode != 0:
            raise E2EError(f"ffmpeg render of frame {n} failed: {res.stderr[-400:]}")
        jpg.unlink()
        written.append(png)
        print(f"  rendered {png}")
    return written


def run(args: argparse.Namespace, client: httpx.Client) -> int:
    try:
        job_id = args.job or upload(client, args)
        if not args.check_only:
            wait(client, job_id, args)
        docs = check(client, job_id)
        if args.render_frames:
            render_frames(client, job_id, docs["keypoints"], args.render_frames, args.out)
    except (E2EError, httpx.HTTPError, ValueError) as e:
        print(f"E2E FAILED: {e}", file=sys.stderr)
        return 1
    print("E2E OK")
    return 0


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    args = parse_args(argv)
    if client is not None:
        return run(args, client)
    with httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(600.0, connect=10.0)) as c:
        return run(args, c)


if __name__ == "__main__":
    sys.exit(main())
