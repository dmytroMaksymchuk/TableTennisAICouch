"""Single in-process job worker (plan Decision B1; DESIGN §2, §2.2, §2.3)."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from ttcoach.config import Settings
from ttcoach.errors import ChildCancelled, PipelineError
from ttcoach.jobs.models import Job, JobError, Stage
from ttcoach.jobs.repository import FileJobRepository
from ttcoach.pipeline import stages
from ttcoach.procs import ChildRunner

logger = logging.getLogger(__name__)
MAX_RECOVERY_ATTEMPTS = 2


class JobWorker:
    def __init__(self, repo: FileJobRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings
        self.runner = ChildRunner()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="ttcoach-worker", daemon=True)
        self.current_job: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.recover()
        self._thread.start()

    def stop(self, timeout_s: float = 10.0) -> None:
        self._stop.set()
        self.runner.stop()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout_s)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def wait_idle(self, timeout_s: float | None = None) -> bool:
        """For tests and tools: block until the queue is drained."""
        done = threading.Event()

        def waiter() -> None:
            self._queue.join()
            done.set()

        threading.Thread(target=waiter, daemon=True).start()
        return done.wait(timeout_s)

    def recover(self) -> None:
        """DESIGN §2.2 — run before accepting new work."""
        to_enqueue: list[Job] = []
        for job in self.repo.list():
            if job.status == "queued":
                to_enqueue.append(job)
            elif job.status == "running":
                attempts = job.recovery_attempts + 1
                if attempts <= MAX_RECOVERY_ATTEMPTS:
                    resume = stages.first_incomplete_stage(self.repo, job)
                    to_enqueue.append(
                        self.repo.update(
                            job.id, status="queued", resume_from=resume, recovery_attempts=attempts, progress=None
                        )
                    )
                    logger.info("recovering job %s from %s (attempt %d)", job.id, resume, attempts)
                    stages.StageContext(self.repo, self.settings, job.id, self.runner).log(
                        f"recovered after restart: was running/{job.stage}, resuming at {resume} (attempt {attempts})"
                    )
                else:
                    self.repo.update(
                        job.id,
                        status="failed",
                        recovery_attempts=attempts,
                        progress=None,
                        error=JobError(
                            code="interrupted",
                            stage=job.stage,
                            message=f"job interrupted {attempts} times during {job.stage}; POST /retry to try again",
                        ).model_dump(),
                    )
        for job in sorted(to_enqueue, key=lambda j: (j.created_at, j.id)):
            self.enqueue(job.id)

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                self.current_job = job_id
                self.process(job_id)
            except Exception:  # the worker must never die
                logger.exception("unexpected error while processing %s", job_id)
            finally:
                self.current_job = None
                self._queue.task_done()

    def _fail(self, job_id: str, code: str, stage: Stage | None, message: str) -> None:
        self.repo.update(
            job_id,
            status="failed",
            progress=None,
            error=JobError(code=code, stage=stage, message=message).model_dump(),  # type: ignore[arg-type]
        )

    def _set_stage(self, job_id: str, stage: Stage) -> None:
        self.repo.update(job_id, status="running", stage=stage, resume_from=stage, progress=None, error=None)

    def process(self, job_id: str) -> None:
        job = self.repo.get(job_id)
        if job.status != "queued":
            return
        ctx = stages.StageContext(self.repo, self.settings, job_id, self.runner)
        stage: Stage = job.resume_from
        try:
            if stage == "normalizing":
                self._set_stage(job_id, stage)
                stages.run_normalize(ctx)
                stage = "estimating_pose"
            if stage == "estimating_pose":
                self._set_stage(job_id, stage)
                stages.run_pose(ctx)
                stage = "analyzing"
            if stage == "analyzing":
                if not self._ensure_selection(job_id):
                    return
                self._set_stage(job_id, stage)
                stages.run_analysis(ctx)
                self.repo.update(job_id, status="done", progress=None, recovery_attempts=0)
        except ChildCancelled:
            # Shutdown: the job stays `running` on disk and is recovered at the next start (DESIGN §2.3).
            ctx.log(f"interrupted by shutdown during {stage}")
        except PipelineError as e:
            ctx.log(f"FAILED {e.code} in {e.stage or stage}: {e.message}")
            self._fail(job_id, e.code, e.stage or stage, e.message)  # type: ignore[arg-type]
        except Exception as e:
            logger.exception("job %s failed in %s", job_id, stage)
            ctx.log(f"FAILED internal in {stage}: {type(e).__name__}: {e}")
            code = "analysis_failed" if stage == "analyzing" else "internal"
            self._fail(job_id, code, stage, f"{type(e).__name__}: {e}")

    def _ensure_selection(self, job_id: str) -> bool:
        """Auto-select a single candidate; with >= 2 and no selection, wait for the user."""
        if self.repo.path(job_id, "pose/selection.json").is_file():
            return True
        doc: dict[str, Any] = self.repo.read_json(job_id, "pose/candidates.json")
        cands = doc["candidates"]
        if len(cands) == 1:
            self.repo.write_json(
                job_id, "pose/selection.json", {"person_ids": [cands[0]["person_id"]], "selected_by": "auto"}
            )
            return True
        self.repo.update(job_id, status="awaiting_selection", resume_from="analyzing", progress=None)
        return False
