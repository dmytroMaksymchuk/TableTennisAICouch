import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isActive, useJob, useResults } from "../api/hooks";
import { anglesFx, jobFx, keypointsFx, reportFx, strokesFx } from "../test/fixtures";
import { jsonResponse, makeClient, renderWithProviders, stubFetch } from "../test/utils";

const id = jobFx.id;

function Harness() {
  const { data: job } = useJob(id);
  const { results } = useResults(id, job?.analysis_rev ?? 0);
  return (
    <div data-testid="view">{results ? `ready:${results.report.analysis_rev}` : "waiting"}</div>
  );
}

function Poll() {
  const { data } = useJob(id);
  return <span>{data?.status}</span>;
}

afterEach(() => vi.unstubAllGlobals());

describe("useResults rev-refetch", () => {
  it("refetches all four artifacts with ?rev=2 and renders only when all report rev 2", async () => {
    let rev = 1;
    let releaseStrokes: () => void = () => {};
    const strokesGate = new Promise<void>((r) => (releaseStrokes = r));
    const bodies = { keypoints: keypointsFx, angles: anglesFx, strokes: strokesFx, report: reportFx };

    const fetchMock = stubFetch((url) => {
      if (url === `/api/jobs/${id}`) return jsonResponse({ ...jobFx, analysis_rev: rev });
      const m = url.match(/\/results\/(\w+)\?rev=(\d+)$/);
      if (!m) return undefined;
      const [, artifact, r] = m;
      const body = { ...bodies[artifact as keyof typeof bodies], analysis_rev: Number(r) };
      if (artifact === "strokes" && r === "2") return strokesGate.then(() => jsonResponse(body));
      return jsonResponse(body);
    });

    const client = makeClient();
    renderWithProviders(<Harness />, { client });
    await waitFor(() => expect(screen.getByTestId("view")).toHaveTextContent("ready:1"));

    rev = 2;
    await act(() => client.invalidateQueries({ queryKey: ["job", id] }));

    const urls = () => fetchMock.mock.calls.map(([u]) => String(u));
    await waitFor(() => {
      for (const a of ["keypoints", "angles", "strokes", "report"]) {
        expect(urls()).toContain(`/api/jobs/${id}/results/${a}?rev=2`);
      }
    });
    // strokes@2 still pending: the view must not render (neither rev 1 nor rev 2)
    expect(screen.getByTestId("view")).toHaveTextContent("waiting");

    releaseStrokes();
    await waitFor(() => expect(screen.getByTestId("view")).toHaveTextContent("ready:2"));
  });
});

describe("polling", () => {
  it("isActive only for queued/running", () => {
    expect(isActive({ ...jobFx, status: "queued" })).toBe(true);
    expect(isActive({ ...jobFx, status: "running" })).toBe(true);
    for (const s of ["done", "failed", "awaiting_selection"] as const) {
      expect(isActive({ ...jobFx, status: s })).toBe(false);
    }
  });

  it("stops polling once the job is awaiting_selection", async () => {
    const fetchMock = stubFetch((url) =>
      url === `/api/jobs/${id}`
        ? jsonResponse({ ...jobFx, status: "awaiting_selection" })
        : undefined,
    );
    renderWithProviders(<Poll />);
    await screen.findByText("awaiting_selection");
    await new Promise((r) => setTimeout(r, 1300));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps polling every second while running", async () => {
    const fetchMock = stubFetch((url) =>
      url === `/api/jobs/${id}` ? jsonResponse({ ...jobFx, status: "running" }) : undefined,
    );
    renderWithProviders(<Poll />);
    await screen.findByText("running");
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2), {
      timeout: 2500,
    });
  });

  it("does not poll forever when the job 404s", async () => {
    const fetchMock = stubFetch((url) =>
      url === `/api/jobs/${id}`
        ? jsonResponse({ error: { code: "job_not_found", message: "nope" } }, 404)
        : undefined,
    );
    renderWithProviders(<Poll />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 1300));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
