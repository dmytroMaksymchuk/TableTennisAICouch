import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AngleCharts } from "../components/AngleCharts";
import { StrokeList } from "../components/StrokeList";
import { PlaybackProvider } from "../state/playback";
import { anglesFx, strokesFx } from "../test/fixtures";
import { frameToSeekTime } from "../utils/time";

const mocks = vi.hoisted(() => ({ relayout: vi.fn() }));

vi.mock("plotly.js-dist-min", () => ({ default: { relayout: mocks.relayout } }));
vi.mock("react-plotly.js/factory", async () => {
  const React = await import("react");
  return {
    default: () =>
      function FakePlot(props: Record<string, unknown>) {
        React.useEffect(() => {
          (props.onInitialized as (f: unknown, d: HTMLElement) => void)(
            {},
            document.createElement("div"),
          );
        }, []); // eslint-disable-line react-hooks/exhaustive-deps
        return <div data-testid="plot" />;
      },
  };
});

beforeEach(() => mocks.relayout.mockClear());

const [s1, s2] = strokesFx.strokes;

describe("StrokeList", () => {
  it("renders type, phases with times, metrics with units, feedback with severity, view class", () => {
    const video = { currentTime: 0 };
    render(
      <PlaybackProvider fps={50} nFrames={100} videoRef={{ current: video }}>
        <StrokeList strokes={strokesFx.strokes} />
      </PlaybackProvider>,
    );
    const card = within(screen.getByTestId("stroke-s001"));
    expect(card.getByText("FH")).toBeInTheDocument();
    expect(card.getByText("oblique")).toBeInTheDocument();
    expect(card.getByText(/backswing: 0\.20s.0\.54s/)).toBeInTheDocument();
    expect(card.getByText(/contact: 0\.56s.0\.64s/)).toBeInTheDocument();
    expect(card.getByText(/follow through: 0\.66s.0\.90s/)).toBeInTheDocument();
    expect(card.getByText("168 deg")).toBeInTheDocument();
    expect(card.getByText("0.34 s")).toBeInTheDocument();
    expect(card.getByText("warning")).toBeInTheDocument();
    expect(card.getByText(/aim for 90-150 deg/)).toBeInTheDocument();
    expect(within(screen.getByTestId("stroke-s002")).getByText("side")).toBeInTheDocument();
  });

  it("click seeks the video to the stroke start and zooms the charts to start_s-0.5..end_s+0.5", async () => {
    const video = { currentTime: 0 };
    render(
      <PlaybackProvider fps={50} nFrames={100} videoRef={{ current: video }}>
        <AngleCharts angles={anglesFx} strokes={strokesFx.strokes} />
        <StrokeList strokes={strokesFx.strokes} />
      </PlaybackProvider>,
    );
    mocks.relayout.mockClear();

    await userEvent.click(screen.getByRole("button", { name: /s002/ }));

    expect(video.currentTime).toBeCloseTo(frameToSeekTime(s2.start_frame, 50), 12);
    expect(mocks.relayout).toHaveBeenCalledWith(expect.anything(), {
      "xaxis.range": [s2.start_s - 0.5, s2.end_s + 0.5],
    });

    await userEvent.click(screen.getByTestId("stroke-s001"));
    expect(video.currentTime).toBeCloseTo(frameToSeekTime(s1.start_frame, 50), 12);
    expect(mocks.relayout).toHaveBeenLastCalledWith(expect.anything(), {
      "xaxis.range": [s1.start_s - 0.5, s1.end_s + 0.5],
    });
  });

  it("shows an empty state", () => {
    render(
      <PlaybackProvider fps={50} nFrames={100} videoRef={{ current: null }}>
        <StrokeList strokes={[]} />
      </PlaybackProvider>,
    );
    expect(screen.getByText(/no strokes/i)).toBeInTheDocument();
  });
});
