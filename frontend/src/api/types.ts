// Mirrors DESIGN.md §3 / §4. All JSON is snake_case.

export type JobStatus = "queued" | "running" | "awaiting_selection" | "done" | "failed";
export type Stage = "normalizing" | "estimating_pose" | "analyzing";
export type Handedness = "right" | "left";

export type ErrorCode =
  | "ffmpeg_failed"
  | "pose_failed"
  | "no_person_detected"
  | "analysis_failed"
  | "interrupted"
  | "internal";

export interface JobError {
  code: ErrorCode | string;
  stage: Stage | null;
  message: string;
}

export interface VideoInfo {
  fps: number;
  n_frames: number;
  width: number;
  height: number;
  duration_s: number;
}

export interface JobSettings {
  handedness: Handedness;
  trim_start_s: number | null;
  trim_end_s: number | null;
  estimator: string;
}

export interface Job {
  schema_version: number;
  id: string;
  status: JobStatus;
  stage: Stage;
  resume_from: Stage;
  progress: number | null;
  created_at: string;
  updated_at: string;
  settings: JobSettings;
  original_filename: string;
  video: VideoInfo | null;
  analysis_rev: number;
  person_ids: string[] | null;
  ruleset_version: string | null;
  recovery_attempts: number;
  error: JobError | null;
}

export interface JobList {
  jobs: Job[];
}

export interface Candidate {
  person_id: string;
  coverage: number;
  mean_confidence: number;
  preview_frame: number;
  /** [x_min, y_min, x_max, y_max] in normalized-video pixels */
  bbox_at_preview: [number, number, number, number];
}

export interface Candidates {
  schema_version: number;
  candidates: Candidate[];
  suggested_person_ids: string[];
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

// ---- results ----

export type ResultArtifact = "keypoints" | "angles" | "strokes" | "report";

export interface Keypoints {
  schema_version: number;
  analysis_rev: number;
  fps: number;
  n_frames: number;
  width: number;
  height: number;
  person_ids: string[];
  joints: string[];
  edges: [string, string][];
  /** [joint][frame], null = missing */
  x: (number | null)[][];
  y: (number | null)[][];
  conf: (number | null)[][];
  x_smooth: (number | null)[][];
  y_smooth: (number | null)[][];
}

export interface AngleSeries {
  name: string;
  label: string;
  unit: string;
  definition: string;
  values: (number | null)[];
  velocity: (number | null)[];
  velocity_unit: string;
}

export interface Angles {
  schema_version: number;
  analysis_rev: number;
  fps: number;
  n_frames: number;
  t: number[];
  series: AngleSeries[];
}

export type StrokeType = "FH" | "BH";
export type ViewClass = "side" | "oblique" | "front_or_back" | "unknown";
export type PhaseName = "backswing" | "contact" | "follow_through";
export type Severity = "info" | "warning" | "critical";
export type ThresholdOp = "lt" | "lte" | "gt" | "gte" | "between" | "outside";

export interface Phase {
  name: PhaseName;
  start_frame: number;
  end_frame: number;
}

export interface MetricValue {
  value: number | null;
  unit: string;
}

export interface Threshold {
  op: ThresholdOp;
  min?: number;
  max?: number;
}

export interface Feedback {
  rule_id: string;
  metric: string;
  value: number;
  threshold: Threshold;
  severity: Severity;
  message: string;
  phase?: PhaseName;
}

export interface Stroke {
  stroke_id: string;
  type: StrokeType;
  type_confidence: number;
  view_class: ViewClass;
  start_frame: number;
  contact_frame: number;
  end_frame: number;
  start_s: number;
  contact_s: number;
  end_s: number;
  phases: Phase[];
  /** unavailable metrics are omitted (not null); skipped rules appear in report.skipped_rules */
  metrics: Record<string, MetricValue>;
  feedback: Feedback[];
}

export interface Strokes {
  schema_version: number;
  analysis_rev: number;
  ruleset_id: string;
  ruleset_version: string;
  handedness: Handedness;
  strokes: Stroke[];
}

export interface ReportView {
  class: ViewClass;
  side_on: boolean;
  shoulder_width_ratio: number;
  /** set whenever side_on is false, including class "unknown" */
  notice: string | null;
}

export interface Report {
  schema_version: number;
  analysis_rev: number;
  job_id: string;
  generated_at: string;
  handedness: Handedness;
  estimator: { name: string; version: string; confidence_kind: "score" | "presence" };
  ruleset: { id: string; version: string; path: string };
  video: VideoInfo;
  person_ids: string[];
  view: ReportView;
  quality: { valid_frame_ratio: number; torso_len_px: number; frame_drift: number };
  summary: {
    n_strokes: number;
    n_fh: number;
    n_bh: number;
    feedback_counts: Record<Severity, number>;
  };
  skipped_rules: { stroke_id: string; rule_id: string; reason: string }[];
}

export interface ResultMap {
  keypoints: Keypoints;
  angles: Angles;
  strokes: Strokes;
  report: Report;
}
