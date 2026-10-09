import type { Job } from "../../api/types";

export const jobFx = {
  schema_version: 1,
  id: "20261004T221500-3f9a1c",
  status: "done",
  stage: "analyzing",
  resume_from: "analyzing",
  progress: null,
  created_at: "2026-10-04T22:15:00Z",
  updated_at: "2026-10-04T22:17:01Z",
  settings: { handedness: "right", trim_start_s: 30, trim_end_s: 40, estimator: "sports2d" },
  original_filename: "clip.mkv",
  video: { fps: 50, n_frames: 100, width: 1280, height: 720, duration_s: 2 },
  analysis_rev: 1,
  person_ids: ["p00"],
  ruleset_version: "2026.10.0",
  recovery_attempts: 0,
  error: null,
} satisfies Job;
