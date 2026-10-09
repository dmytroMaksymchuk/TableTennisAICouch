import type { ReportView } from "../api/types";

const FALLBACK =
  "Camera is not side-on: joint angles measured in 2D may be distorted. Treat angle feedback as approximate.";

/** Rendered only when the clip is known to be not side-on (AC12). */
export function ViewNotice({ view }: { view: ReportView }) {
  if (view.side_on !== false) return null;
  return (
    <div role="alert" className="notice">
      {view.notice ?? FALLBACK}
    </div>
  );
}
