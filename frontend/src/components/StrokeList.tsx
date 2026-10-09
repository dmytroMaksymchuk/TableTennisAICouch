import type { Stroke } from "../api/types";
import { usePlayback } from "../state/playback";
import { fmtMetric, fmtSeconds, labelOf } from "../utils/format";
import { frameToTime } from "../utils/time";

export function StrokeList({ strokes }: { strokes: Stroke[] }) {
  const { fps, seekToFrame, selectStroke, selectedStrokeId, setChartRange } = usePlayback();

  if (strokes.length === 0) {
    return <p className="muted">No strokes detected in this clip.</p>;
  }

  const pick = (s: Stroke) => {
    seekToFrame(s.start_frame);
    selectStroke(s.stroke_id);
    setChartRange([s.start_s - 0.5, s.end_s + 0.5]);
  };

  return (
    <div className="stroke-list">
      {strokes.map((s) => (
        <article
          key={s.stroke_id}
          data-testid={`stroke-${s.stroke_id}`}
          className={`stroke${s.stroke_id === selectedStrokeId ? " selected" : ""}`}
          onClick={() => pick(s)}
        >
          <button type="button" className="stroke-head">
            <span className="badge type">{s.type}</span>
            <span>
              {s.stroke_id} · {fmtSeconds(s.start_s)}–{fmtSeconds(s.end_s)}
            </span>
            <span className="badge view" title="camera view class of this stroke">
              {labelOf(s.view_class)}
            </span>
          </button>
          <ul className="phases">
            {s.phases.map((p) => (
              <li key={p.name}>
                {labelOf(p.name)}: {fmtSeconds(frameToTime(p.start_frame, fps))}–
                {fmtSeconds(frameToTime(p.end_frame, fps))}
              </li>
            ))}
          </ul>
          <ul className="metrics">
            {Object.entries(s.metrics).map(([name, m]) => (
              <li key={name}>
                {labelOf(name)}: <strong>{fmtMetric(m.value, m.unit)}</strong>
              </li>
            ))}
          </ul>
          <ul className="feedback">
            {s.feedback.map((fb) => (
              <li key={fb.rule_id} className={`sev-${fb.severity}`}>
                <span className="badge">{fb.severity}</span> {fb.message}
              </li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}
