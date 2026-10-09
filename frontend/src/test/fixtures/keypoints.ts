import type { Keypoints } from "../../api/types";

const N = 100;
const joints = ["head", "neck", "r_shoulder", "r_elbow", "r_wrist"];

// joint j at frame f sits at (100 + 50*j + f, 200 + 10*j)
const x = joints.map((_, j) => Array.from({ length: N }, (_, f) => 100 + 50 * j + f));
const y = joints.map((_, j) => Array.from({ length: N }, () => 200 + 10 * j));

export const keypointsFx = {
  schema_version: 1,
  analysis_rev: 1,
  fps: 50,
  n_frames: N,
  width: 1280,
  height: 720,
  person_ids: ["p00"],
  joints,
  edges: [
    ["head", "neck"],
    ["neck", "r_shoulder"],
    ["r_shoulder", "r_elbow"],
    ["r_elbow", "r_wrist"],
  ],
  x,
  y,
  conf: joints.map(() => Array.from({ length: N }, () => 1)),
  x_smooth: x,
  y_smooth: y,
} satisfies Keypoints;
