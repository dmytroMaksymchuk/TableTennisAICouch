import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useJob } from "../api/hooks";
import { RulesBadge } from "../components/RulesBadge";
import { jobFx } from "../test/fixtures";
import { jsonResponse, renderWithProviders, stubFetch } from "../test/utils";

const id = jobFx.id;

function Harness() {
  const { data } = useJob(id);
  return (
    <>
      <span data-testid="status">{data?.status}</span>
      <RulesBadge jobId={id} rulesetId="default" rulesetVersion="2026.10.0" />
    </>
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("RulesBadge", () => {
  it("POSTs /reanalyze and then polls the job again", async () => {
    let reanalyzed = false;
    const fetchMock = stubFetch(
      (url, init) => {
        if (url === `/api/jobs/${id}/reanalyze` && init?.method === "POST") {
          reanalyzed = true;
          return jsonResponse({ ...jobFx, status: "queued" }, 202);
        }
      },
      (url) => {
        if (url === `/api/jobs/${id}`) {
          return jsonResponse({ ...jobFx, status: reanalyzed ? "running" : "done" });
        }
      },
    );

    renderWithProviders(<Harness />);
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("done"));
    expect(screen.getByText(/rules: default v2026\.10\.0/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /re-analyze/i }));

    const calls = () => fetchMock.mock.calls.map(([u, i]) => `${i?.method ?? "GET"} ${String(u)}`);
    const post = `POST /api/jobs/${id}/reanalyze`;
    await waitFor(() => expect(calls()).toContain(post));
    const postIdx = calls().indexOf(post);
    await waitFor(() => expect(calls().slice(postIdx + 1)).toContain(`GET /api/jobs/${id}`));
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("running"));
  });
});
