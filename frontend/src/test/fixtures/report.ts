import type { Report } from "../../api/types";

export const reportFx = {
  schema_version: 1,
  analysis_rev: 1,
  job_id: "20261004T221500-3f9a1c",
  generated_at: "2026-10-04T22:17:01Z",
  handedness: "right",
  estimator: { name: "sports2d", version: "0.0.0", confidence_kind: "presence" },
  ruleset: { id: "default", version: "2026.10.0", path: "backend/rules/default.yaml" },
  video: { fps: 50, n_frames: 100, width: 1280, height: 720, duration_s: 2 },
  person_ids: ["p00"],
  view: {
    class: "oblique",
    side_on: false,
    shoulder_width_ratio: 0.48,
    notice:
      "Camera is not side-on: joint angles measured in 2D may be distorted. Treat angle feedback as approximate.",
  },
  quality: { valid_frame_ratio: 0.94, torso_len_px: 182, frame_drift: 0 },
  summary: { n_strokes: 2, n_fh: 1, n_bh: 1, feedback_counts: { info: 0, warning: 1, critical: 0 } },
  skipped_rules: [],
} satisfies Report;
