import type { Angles } from "../../api/types";

const N = 100;
const FPS = 50;
const ramp = (a: number, b: number): (number | null)[] =>
  Array.from({ length: N }, (_, f) => (f === 5 ? null : a + ((b - a) * f) / (N - 1)));

const series = (name: string, label: string, a: number, b: number) => ({
  name,
  label,
  unit: "deg",
  definition: `fixture ${name}`,
  values: ramp(a, b),
  velocity: ramp(0, 10),
  velocity_unit: "deg/s",
});

export const anglesFx = {
  schema_version: 1,
  analysis_rev: 1,
  fps: FPS,
  n_frames: N,
  t: Array.from({ length: N }, (_, f) => f / FPS),
  series: [
    series("l_elbow", "Left elbow flexion", 100, 150),
    series("r_elbow", "Right elbow flexion", 90, 170),
    series("trunk_lean", "Trunk lean", 5, 20),
  ],
} satisfies Angles;
