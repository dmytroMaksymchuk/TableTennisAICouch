import { useReanalyze, useRetry } from "../api/hooks";
import type { Job } from "../api/types";
import { labelOf } from "../utils/format";

export function JobStatusPanel({ job }: { job: Job }) {
  const retry = useRetry(job.id);
  const reanalyze = useReanalyze(job.id);
  const pct = job.progress == null ? null : Math.round(job.progress * 100);

  if (job.status === "queued") {
    return <div className="panel status">Queued - waiting for the worker to pick this job up...</div>;
  }

  if (job.status === "running") {
    return (
      <div className="panel status">
        <p>
          Running: <strong>{labelOf(job.stage)}</strong>
          {pct !== null && ` (${pct}%)`}
        </p>
        <progress max={100} value={pct ?? undefined} aria-label="progress" />
      </div>
    );
  }

  if (job.status === "failed") {
    const err = job.error;
    return (
      <div className="panel status failed" role="alert">
        <p>
          <strong>Failed</strong>
          {err && ` (${err.code}${err.stage ? ` at ${labelOf(err.stage)}` : ""})`}
        </p>
        {err && <p>{err.message}</p>}
        <div className="row">
          <button type="button" disabled={retry.isPending} onClick={() => retry.mutate()}>
            Retry
          </button>
          {err?.stage === "analyzing" && (
            <button type="button" disabled={reanalyze.isPending} onClick={() => reanalyze.mutate()}>
              Re-analyze
            </button>
          )}
        </div>
        {(retry.error || reanalyze.error) && (
          <p className="error">{(retry.error ?? reanalyze.error)?.message}</p>
        )}
      </div>
    );
  }

  return <div className="panel status">Status: {labelOf(job.status)}</div>;
}
