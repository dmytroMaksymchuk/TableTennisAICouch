import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { Job, ResultMap } from "./types";

export function isActive(job: Job | undefined): boolean {
  return !job || job.status === "queued" || job.status === "running";
}

/** Polls every 1 s while queued|running. */
export function useJob(id: string) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: () => api.getJob(id),
    // a job that errors with nothing cached (e.g. 404) is not polled forever
    refetchInterval: (q) => (q.state.error && !q.state.data ? false : isActive(q.state.data) ? 1000 : false),
  });
}

export function useJobs() {
  return useQuery({ queryKey: ["jobs"], queryFn: () => api.listJobs() });
}

export function useCandidates(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["candidates", id],
    queryFn: () => api.getCandidates(id),
    enabled,
  });
}

export type Results = ResultMap;

/**
 * Fetches all four artifacts for `analysisRev` (`?rev=`). `results` is non-null
 * only when every artifact reports that same rev (DESIGN §7).
 */
export function useResults(jobId: string, analysisRev: number) {
  const enabled = analysisRev > 0;
  const q = useQueries({
    queries: [
      {
        queryKey: ["result", jobId, "keypoints", analysisRev],
        queryFn: () => api.getResult(jobId, "keypoints", analysisRev),
        enabled,
        staleTime: Infinity,
      },
      {
        queryKey: ["result", jobId, "angles", analysisRev],
        queryFn: () => api.getResult(jobId, "angles", analysisRev),
        enabled,
        staleTime: Infinity,
      },
      {
        queryKey: ["result", jobId, "strokes", analysisRev],
        queryFn: () => api.getResult(jobId, "strokes", analysisRev),
        enabled,
        staleTime: Infinity,
      },
      {
        queryKey: ["result", jobId, "report", analysisRev],
        queryFn: () => api.getResult(jobId, "report", analysisRev),
        enabled,
        staleTime: Infinity,
      },
    ],
  });
  const [keypoints, angles, strokes, report] = [q[0].data, q[1].data, q[2].data, q[3].data];
  let results: Results | null = null;
  if (
    keypoints && angles && strokes && report &&
    [keypoints, angles, strokes, report].every((d) => d.analysis_rev === analysisRev)
  ) {
    results = { keypoints, angles, strokes, report };
  }
  const error = q.find((x) => x.error)?.error ?? null;
  return { results, error, isLoading: enabled && !results && !error };
}

function useJobMutation<V>(jobId: string, fn: (v: V) => Promise<Job>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (job) => {
      qc.setQueryData(["job", jobId], job);
      void qc.invalidateQueries({ queryKey: ["job", jobId] });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export const useRetry = (id: string) => useJobMutation(id, () => api.retry(id));
export const useReanalyze = (id: string) => useJobMutation(id, () => api.reanalyze(id));
export const useSelectPersons = (id: string) =>
  useJobMutation(id, (ids: string[]) => api.selectPersons(id, ids));
