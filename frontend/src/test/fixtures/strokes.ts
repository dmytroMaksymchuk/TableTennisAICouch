import type { Strokes } from "../../api/types";

export const strokesFx: Strokes = {
  schema_version: 1,
  analysis_rev: 1,
  ruleset_id: "default",
  ruleset_version: "2026.10.0",
  handedness: "right",
  strokes: [
    {
      stroke_id: "s001",
      type: "FH",
      type_confidence: 0.72,
      view_class: "oblique",
      start_frame: 10,
      contact_frame: 30,
      end_frame: 45,
      start_s: 0.2,
      contact_s: 0.6,
      end_s: 0.9,
      phases: [
        { name: "backswing", start_frame: 10, end_frame: 27 },
        { name: "contact", start_frame: 28, end_frame: 32 },
        { name: "follow_through", start_frame: 33, end_frame: 45 },
      ],
      metrics: {
        elbow_angle_at_contact: { value: 168, unit: "deg" },
        backswing_duration_s: { value: 0.34, unit: "s" },
      },
      feedback: [
        {
          rule_id: "fh.contact.elbow_extension",
          metric: "elbow_angle_at_contact",
          value: 168,
          threshold: { op: "between", min: 90, max: 150 },
          severity: "warning",
          message: "Elbow angle at contact is 168 deg; aim for 90-150 deg.",
          phase: "contact",
        },
      ],
    },
    {
      stroke_id: "s002",
      type: "BH",
      type_confidence: 0.6,
      view_class: "side",
      start_frame: 60,
      contact_frame: 75,
      end_frame: 90,
      start_s: 1.2,
      contact_s: 1.5,
      end_s: 1.8,
      phases: [
        { name: "backswing", start_frame: 60, end_frame: 72 },
        { name: "contact", start_frame: 73, end_frame: 77 },
        { name: "follow_through", start_frame: 78, end_frame: 90 },
      ],
      metrics: { elbow_angle_at_contact: { value: 120, unit: "deg" } },
      feedback: [],
    },
  ],
};

