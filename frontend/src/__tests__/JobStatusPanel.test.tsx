import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JobStatusPanel } from "../components/JobStatusPanel";
import { jobFx } from "../test/fixtures";
import { jsonResponse, renderWithProviders, stubFetch } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

describe("JobStatusPanel", () => {
  it("renders queued", () => {
    renderWithProviders(<JobStatusPanel job={{ ...jobFx, status: "queued" }} />);
    expect(screen.getByText(/queued/i)).toBeInTheDocument();
  });

  it("renders running with stage and progress", () => {
    renderWithProviders(
      <JobStatusPanel
        job={{ ...jobFx, status: "running", stage: "estimating_pose", progress: 0.42 }}
      />,
    );
    expect(screen.getByText(/estimating pose/i)).toBeInTheDocument();
    expect(screen.getByText(/42%/)).toBeInTheDocument();
  });

  it("renders failed with the error message and a Retry button that POSTs /retry", async () => {
    const fetchMock = stubFetch((url, init) =>
      url === `/api/jobs/${jobFx.id}/retry` && init?.method === "POST"
        ? jsonResponse({ ...jobFx, status: "queued" }, 202)
        : undefined,
    );
    renderWithProviders(
      <JobStatusPanel
        job={{
          ...jobFx,
          status: "failed",
          stage: "estimating_pose",
          error: { code: "pose_failed", stage: "estimating_pose", message: "sports2d exited 1" },
        }}
      />,
    );
    expect(screen.getByText("sports2d exited 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /re-analyze/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/jobs/${jobFx.id}/retry`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
