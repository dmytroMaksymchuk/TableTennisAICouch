import type {
  ApiErrorBody,
  Candidates,
  Handedness,
  Job,
  JobList,
  ResultArtifact,
  ResultMap,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let code = "http_error";
    let message = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as ApiErrorBody;
      if (body?.error) {
        code = body.error.code;
        message = body.error.message;
      }
    } catch {
      // non-JSON error body: keep the generic message
    }
    throw new ApiError(res.status, code, message);
  }
  return (await res.json()) as T;
}

export interface CreateJobInput {
  file: File;
  handedness: Handedness;
  trimStart?: number | null;
  trimEnd?: number | null;
}

export const api = {
  createJob(input: CreateJobInput): Promise<Job> {
    const fd = new FormData();
    fd.append("file", input.file);
    fd.append("handedness", input.handedness);
    if (input.trimStart != null) fd.append("trim_start_s", String(input.trimStart));
    if (input.trimEnd != null) fd.append("trim_end_s", String(input.trimEnd));
    return request<Job>("/api/jobs", { method: "POST", body: fd });
  },
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  listJobs: () => request<JobList>("/api/jobs"),
  getCandidates: (id: string) => request<Candidates>(`/api/jobs/${id}/candidates`),
  selectPersons: (id: string, personIds: string[]) =>
    request<Job>(`/api/jobs/${id}/selection`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_ids: personIds }),
    }),
  reanalyze: (id: string) => request<Job>(`/api/jobs/${id}/reanalyze`, { method: "POST" }),
  retry: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: "POST" }),
  getResult: <A extends ResultArtifact>(id: string, artifact: A, rev: number) =>
    request<ResultMap[A]>(`/api/jobs/${id}/results/${artifact}?rev=${rev}`),
  frameUrl: (id: string, n: number) => `/api/jobs/${id}/frames/${n}.jpg`,
  videoUrl: (id: string) => `/api/jobs/${id}/video`,
};
