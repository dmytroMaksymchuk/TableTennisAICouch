import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Angles, Stroke } from "../api/types";
import { usePlayback } from "../state/playback";
import { timeToFrame } from "../utils/time";

const Plot = createPlotlyComponent(Plotly);

const GROUPS: { id: string; label: string; names: string[]; defaultOn: boolean }[] = [
  { id: "elbows", label: "Elbows", names: ["l_elbow", "r_elbow"], defaultOn: true },
  { id: "shoulders", label: "Shoulders", names: ["l_shoulder", "r_shoulder"], defaultOn: false },
  { id: "hips", label: "Hips", names: ["l_hip", "r_hip"], defaultOn: false },
  { id: "knees", label: "Knees", names: ["l_knee", "r_knee"], defaultOn: false },
  {
    id: "trunk",
    label: "Trunk",
    names: ["trunk_lean", "shoulder_hip_separation"],
    defaultOn: true,
  },
];

const CURSOR_SHAPE = {
  type: "line",
  xref: "x",
  yref: "paper",
  x0: 0,
  x1: 0,
  y0: 0,
  y1: 1,
  line: { color: "#ef4444", width: 2 },
};

const PLOT_CONFIG = { responsive: true, displaylogo: false };
const PLOT_STYLE = { width: "100%" };

type Mode = "angle" | "velocity";
// Plotly's typings are strict about relayout payloads; the keys used here are plain strings.
type Relayout = (div: HTMLElement, update: Record<string, unknown>) => unknown;
const relayout = (div: HTMLElement, update: Record<string, unknown>) =>
  (Plotly.relayout as unknown as Relayout)(div, update);

interface Props {
  angles: Angles;
  strokes: Stroke[];
}

export function AngleCharts({ angles, strokes }: Props) {
  const {
    fps,
    nFrames,
    getTime,
    subscribeTime,
    seekToFrame,
    selectedStrokeId,
    chartRange,
    setChartRange,
  } = usePlayback();
  const [mode, setMode] = useState<Mode>("angle");
  const [on, setOn] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(GROUPS.map((g) => [g.id, g.defaultOn])),
  );

  const divRef = useRef<HTMLElement | null>(null);
  const timeRef = useRef(getTime());
  const rangeRef = useRef(chartRange);
  const rafRef = useRef(0);
  const prevRange = useRef(chartRange); // initial range is applied by onInitialized
  const userCleared = useRef(false);

  const data = useMemo(() => {
    const visible = new Set(GROUPS.filter((g) => on[g.id]).flatMap((g) => g.names));
    return angles.series
      .filter((s) => visible.has(s.name))
      .map((s) => ({
        type: "scatter",
        mode: "lines",
        name: s.label,
        x: angles.t,
        y: mode === "angle" ? s.values : s.velocity,
        connectgaps: false,
        hovertemplate: `${s.label}: %{y:.1f} ${mode === "angle" ? s.unit : s.velocity_unit}<extra></extra>`,
      }));
  }, [angles, on, mode]);

  const layout = useMemo(
    () => ({
      height: 340,
      margin: { l: 56, r: 16, t: 16, b: 44 },
      xaxis: { title: { text: "time (s)" }, zeroline: false },
      yaxis: { title: { text: mode === "angle" ? "angle (deg)" : "velocity (deg/s)" } },
      legend: { orientation: "h", y: -0.25 },
      hovermode: "x",
      uirevision: "keep",
      shapes: [
        { ...CURSOR_SHAPE },
        ...strokes.map((s) => ({
          type: "rect",
          xref: "x",
          yref: "paper",
          x0: s.start_s,
          x1: s.end_s,
          y0: 0,
          y1: 1,
          line: { width: 0 },
          layer: "below",
          fillcolor: s.type === "FH" ? "rgba(34,197,94,0.15)" : "rgba(59,130,246,0.15)",
          opacity: s.stroke_id === selectedStrokeId ? 1 : 0.55,
        })),
      ],
    }),
    [mode, strokes, selectedStrokeId],
  );

  // No-op when the cursor is already in place: react-plotly calls onUpdate after every
  // plotly_relayout, so an unconditional relayout here would loop forever and freeze the page.
  const applyCursor = useCallback(() => {
    const div = divRef.current as (HTMLElement & { layout?: { shapes?: { x0?: unknown; x1?: unknown }[] } }) | null;
    if (!div) return;
    const t = timeRef.current;
    const cursor = div.layout?.shapes?.[0];
    if (cursor && cursor.x0 === t && cursor.x1 === t) return;
    relayout(div, { "shapes[0].x0": t, "shapes[0].x1": t });
  }, []);

  // Cursor follows playback: at most one relayout per animation frame, no React re-render.
  useEffect(() => {
    timeRef.current = getTime();
    return subscribeTime((t) => {
      timeRef.current = t;
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        applyCursor();
      });
    });
  }, [getTime, subscribeTime, applyCursor]);

  useEffect(
    () => () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    },
    [],
  );

  // Stroke zoom / reset requests: applied only when chartRange itself changes.
  useEffect(() => {
    rangeRef.current = chartRange;
    const div = divRef.current;
    if (div && chartRange !== prevRange.current) {
      if (chartRange) relayout(div, { "xaxis.range": chartRange });
      else if (prevRange.current && !userCleared.current) relayout(div, { "xaxis.autorange": true });
    }
    userCleared.current = false;
    prevRange.current = chartRange;
  }, [chartRange]);

  return (
    <section className="panel charts">
      <div className="chart-controls">
        <strong>Joint angles</strong>
        {GROUPS.map((g) => (
          <label key={g.id}>
            <input
              type="checkbox"
              checked={on[g.id]}
              onChange={(e) => setOn((p) => ({ ...p, [g.id]: e.target.checked }))}
            />{" "}
            {g.label}
          </label>
        ))}
        <select
          aria-label="Chart quantity"
          value={mode}
          onChange={(e) => setMode(e.target.value as Mode)}
        >
          <option value="angle">Angle</option>
          <option value="velocity">Angular velocity</option>
        </select>
        <button type="button" onClick={() => setChartRange(null)}>
          Reset zoom
        </button>
      </div>
      <Plot
        data={data}
        layout={layout}
        config={PLOT_CONFIG}
        useResizeHandler
        style={PLOT_STYLE}
        onInitialized={(_fig, div) => {
          divRef.current = div;
          applyCursor();
          if (rangeRef.current) relayout(div, { "xaxis.range": rangeRef.current });
        }}
        onUpdate={(_fig, div) => {
          divRef.current = div;
          applyCursor();
        }}
        onRelayout={(ev: Record<string, unknown>) => {
          // user zoom/pan/double-click: drop the requested range so it is not re-applied
          if (("xaxis.range[0]" in ev || "xaxis.autorange" in ev) && rangeRef.current) {
            userCleared.current = true;
            setChartRange(null);
          }
        }}
        onClick={(ev: { points?: { x: number | string }[] }) => {
          const x = Number(ev.points?.[0]?.x);
          if (Number.isFinite(x)) seekToFrame(timeToFrame(x, fps, nFrames));
        }}
      />
    </section>
  );
}
