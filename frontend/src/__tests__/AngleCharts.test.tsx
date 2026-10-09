import { useState } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AngleCharts } from "../components/AngleCharts";
import { PlaybackContext, PlaybackProvider, type PlaybackValue } from "../state/playback";
import { anglesFx, strokesFx } from "../test/fixtures";
import { frameToSeekTime, timeToFrame } from "../utils/time";

const mocks = vi.hoisted(() => ({
  relayout: vi.fn(),
  plotProps: { current: null as null | Record<string, unknown> },
  div: null as null | HTMLElement,
  renders: { n: 0 },
}));

vi.mock("plotly.js-dist-min", () => ({ default: { relayout: mocks.relayout } }));
vi.mock("react-plotly.js/factory", async () => {
  const React = await import("react");
  return {
    default: () =>
      function FakePlot(props: Record<string, unknown>) {
        mocks.plotProps.current = props;
        mocks.renders.n++;
        React.useEffect(() => {
          const div = document.createElement("div");
          mocks.div = div;
          (props.onInitialized as (f: unknown, d: HTMLElement) => void)({}, div);
        }, []); // eslint-disable-line react-hooks/exhaustive-deps
        return <div data-testid="plot" />;
      },
  };
});

const listeners = new Set<(t: number) => void>();
let now = 0;
const emit = (t: number) => {
  now = t;
  listeners.forEach((cb) => cb(t));
};
const frame = () => new Promise((r) => requestAnimationFrame(() => r(null)));
const isCursor = (c: unknown[]) => "shapes[0].x0" in (c[1] as object);

const base: PlaybackValue = {
  fps: anglesFx.fps,
  nFrames: anglesFx.n_frames,
  getTime: () => now,
  subscribeTime: (cb) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  },
  selectedStrokeId: null,
  chartRange: null,
  seekToTime: vi.fn(),
  seekToFrame: vi.fn(),
  reportTime: vi.fn(),
  selectStroke: vi.fn(),
  setChartRange: vi.fn(),
};

const ui = (v: PlaybackValue) => (
  <PlaybackContext.Provider value={v}>
    <AngleCharts angles={anglesFx} strokes={strokesFx.strokes} />
  </PlaybackContext.Provider>
);

beforeEach(() => {
  mocks.relayout.mockClear();
  mocks.renders.n = 0;
  listeners.clear();
  now = 0;
  vi.mocked(base.seekToFrame).mockClear();
});

describe("AngleCharts", () => {
  it("builds the cursor as shapes[0] and stroke windows after it", () => {
    render(ui(base));
    const layout = mocks.plotProps.current!.layout as { shapes: { type: string; x0: number; x1: number }[] };
    expect(layout.shapes[0].type).toBe("line");
    expect(layout.shapes).toHaveLength(1 + strokesFx.strokes.length);
    expect(layout.shapes[1]).toMatchObject({ type: "rect", x0: 0.2, x1: 0.9 });
  });

  it("relayouts shapes[0] when playback time changes", async () => {
    render(ui(base));
    act(() => emit(1.234));
    await waitFor(() =>
      expect(mocks.relayout).toHaveBeenCalledWith(
        mocks.div,
        expect.objectContaining({ "shapes[0].x0": 1.234, "shapes[0].x1": 1.234 }),
      ),
    );
  });

  it("per-frame time updates cause no chart re-render and at most one relayout per animation frame", async () => {
    render(ui(base));
    await frame();
    mocks.relayout.mockClear();
    const rendersBefore = mocks.renders.n;
    act(() => {
      for (let i = 1; i <= 30; i++) emit(i / 50);
    });
    await frame();
    await frame();
    expect(mocks.renders.n).toBe(rendersBefore); // Plot (react-plotly) props untouched
    const cursorCalls = mocks.relayout.mock.calls.filter(isCursor);
    expect(cursorCalls).toHaveLength(1);
    expect(cursorCalls[0][1]).toMatchObject({ "shapes[0].x0": 30 / 50 });
  });

  it("does not re-apply xaxis.range on updates; user zoom clears chartRange without autorange", () => {
    const setChartRange = vi.fn();
    const v = { ...base, chartRange: [0.3, 1.4] as [number, number], setChartRange };
    render(ui(v));
    const rangeCalls = () => mocks.relayout.mock.calls.filter((c) => "xaxis.range" in (c[1] as object));
    expect(rangeCalls()).toHaveLength(1);
    // Plotly fires onUpdate after re-plots; the range must not snap back
    act(() => {
      (mocks.plotProps.current!.onUpdate as (f: unknown, d: HTMLElement) => void)({}, mocks.div!);
      (mocks.plotProps.current!.onUpdate as (f: unknown, d: HTMLElement) => void)({}, mocks.div!);
    });
    expect(rangeCalls()).toHaveLength(1);
    // programmatic relayout echo does not clear it
    act(() => (mocks.plotProps.current!.onRelayout as (e: unknown) => void)({ "xaxis.range": [0.3, 1.4] }));
    expect(setChartRange).not.toHaveBeenCalled();
    // user drag-zoom clears it
    act(() =>
      (mocks.plotProps.current!.onRelayout as (e: unknown) => void)({
        "xaxis.range[0]": 0.5,
        "xaxis.range[1]": 0.9,
      }),
    );
    expect(setChartRange).toHaveBeenCalledWith(null);
  });

  it("user zoom clearing the range does not trigger an autorange relayout", () => {
    const Wrapper = () => {
      const [r, setR] = useState<[number, number] | null>([0.3, 1.4]);
      return ui({ ...base, chartRange: r, setChartRange: setR });
    };
    render(<Wrapper />);
    mocks.relayout.mockClear();
    act(() =>
      (mocks.plotProps.current!.onRelayout as (e: unknown) => void)({ "xaxis.range[0]": 0.5 }),
    );
    expect(mocks.relayout).not.toHaveBeenCalledWith(expect.anything(), { "xaxis.autorange": true });
  });

  it("plotly_click seeks to the frame containing x", () => {
    render(ui(base));
    const x = 0.6;
    act(() => {
      (mocks.plotProps.current!.onClick as (e: unknown) => void)({ points: [{ x }] });
    });
    expect(base.seekToFrame).toHaveBeenCalledWith(timeToFrame(x, base.fps, base.nFrames));
  });

  it("click moves the video to frameToSeekTime(timeToFrame(x)) via the real provider", () => {
    const video = { currentTime: 0 };
    render(
      <PlaybackProvider fps={anglesFx.fps} nFrames={anglesFx.n_frames} videoRef={{ current: video }}>
        <AngleCharts angles={anglesFx} strokes={strokesFx.strokes} />
      </PlaybackProvider>,
    );
    const x = 0.6;
    act(() => {
      (mocks.plotProps.current!.onClick as (e: unknown) => void)({ points: [{ x }] });
    });
    expect(video.currentTime).toBeCloseTo(frameToSeekTime(timeToFrame(x, 50, 100), 50), 12);
  });

  it("applies a requested xaxis.range and resets to autorange", () => {
    const { rerender } = render(ui(base));
    rerender(ui({ ...base, chartRange: [0.3, 1.4] }));
    expect(mocks.relayout).toHaveBeenCalledWith(mocks.div, { "xaxis.range": [0.3, 1.4] });
    rerender(ui({ ...base, chartRange: null }));
    expect(mocks.relayout).toHaveBeenCalledWith(mocks.div, { "xaxis.autorange": true });
  });

  it("Reset zoom button clears the chart range", () => {
    render(ui({ ...base, chartRange: [0.3, 1.4] }));
    screen.getByRole("button", { name: /reset zoom/i }).click();
    expect(base.setChartRange).toHaveBeenCalledWith(null);
  });
});

describe("AngleCharts relayout/onUpdate feedback", () => {
  it("does not loop when Plotly fires onUpdate after each relayout (real react-plotly behaviour)", async () => {
    let calls = 0;
    mocks.relayout.mockImplementation((div: HTMLElement & { layout?: { shapes: Record<string, unknown>[] } }, update: Record<string, unknown>) => {
      calls++;
      if (calls > 50) throw new Error("relayout feedback loop");
      div.layout ??= { shapes: [{}] };
      if ("shapes[0].x0" in update) {
        div.layout.shapes[0].x0 = update["shapes[0].x0"];
        div.layout.shapes[0].x1 = update["shapes[0].x1"];
      }
      // react-plotly invokes onUpdate on plotly_relayout, synchronously from Plotly's emit
      (mocks.plotProps.current!.onUpdate as (f: unknown, d: HTMLElement) => void)({}, div);
    });
    render(ui(base));
    await waitFor(() => expect(mocks.div).not.toBeNull());
    act(() => emit(1.5));
    await act(frame);
    expect(calls).toBeLessThanOrEqual(3);
    const last = mocks.relayout.mock.calls.filter(isCursor).at(-1)!;
    expect(last[1]).toEqual({ "shapes[0].x0": 1.5, "shapes[0].x1": 1.5 });
    mocks.relayout.mockReset();
  });
});
