import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Candidates } from "../api/types";
import { PersonSelect } from "../components/PersonSelect";
import { jobFx } from "../test/fixtures";
import { jsonResponse, renderWithProviders, stubFetch } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const candidates = {
  schema_version: 1,
  candidates: [
    { person_id: "p00", coverage: 0.9, mean_confidence: 0.8, preview_frame: 12, bbox_at_preview: [128, 72, 384, 360] },
    { person_id: "p03", coverage: 0.4, mean_confidence: 0.7, preview_frame: 40, bbox_at_preview: [640, 0, 1280, 720] },
  ],
  suggested_person_ids: ["p00"],
} satisfies Candidates;

describe("PersonSelect", () => {
  it("shows frame thumbnails with scaled bboxes, toggles, and posts person_ids", async () => {
    const id = jobFx.id;
    const fetchMock = stubFetch((url, init) => {
      if (url === `/api/jobs/${id}/candidates`) return jsonResponse(candidates);
      if (url === `/api/jobs/${id}/selection` && init?.method === "POST") {
        return jsonResponse({ ...jobFx, status: "queued" }, 202);
      }
    });
    renderWithProviders(<PersonSelect job={{ ...jobFx, status: "awaiting_selection" }} />);

    const img = await screen.findByAltText("preview of p00");
    expect(img).toHaveAttribute("src", `/api/jobs/${id}/frames/12.jpg`);
    expect(screen.getByAltText("preview of p03")).toHaveAttribute("src", `/api/jobs/${id}/frames/40.jpg`);

    // video is 1280x720, so bbox [128,72,384,360] -> 10% / 10% / 20% / 40%
    const box = screen.getByTestId("bbox-p00");
    expect(box.style.left).toBe("10%");
    expect(box.style.top).toBe("10%");
    expect(box.style.width).toBe("20%");
    expect(box.style.height).toBe("40%");

    const p00 = screen.getByRole("button", { name: "Person p00" });
    const p03 = screen.getByRole("button", { name: "Person p03" });
    expect(p00).toHaveAttribute("aria-pressed", "true"); // suggested
    expect(p03).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(p03);
    expect(p03).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(p00);
    expect(p00).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(p00);

    await userEvent.click(screen.getByRole("button", { name: /analyze selected/i }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, i]) => i?.method === "POST");
      expect(post).toBeTruthy();
      expect(JSON.parse(String(post![1]!.body))).toEqual({ person_ids: ["p03", "p00"] });
    });
  });
});
