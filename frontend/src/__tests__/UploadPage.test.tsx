import { Route, Routes } from "react-router-dom";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UploadPage } from "../pages/UploadPage";
import { jobFx } from "../test/fixtures";
import { jsonResponse, renderWithProviders, stubFetch } from "../test/utils";

afterEach(() => vi.unstubAllGlobals());

const page = (
  <Routes>
    <Route path="/" element={<UploadPage />} />
    <Route path="/jobs/:jobId" element={<div>job page</div>} />
  </Routes>
);

const listHandler = (jobs: unknown[] = []) => (url: string, init?: RequestInit) =>
  url === "/api/jobs" && (init?.method ?? "GET") === "GET" ? jsonResponse({ jobs }) : undefined;

describe("UploadPage", () => {
  it("posts multipart fields and navigates to the job", async () => {
    const fetchMock = stubFetch((url, init) =>
      url === "/api/jobs" && init?.method === "POST"
        ? jsonResponse({ ...jobFx, status: "queued" }, 201)
        : undefined,
      listHandler(),
    );
    renderWithProviders(page);

    const file = new File(["x"], "clip.mp4", { type: "video/mp4" });
    await userEvent.upload(screen.getByLabelText(/video file/i), file);
    expect(screen.getByLabelText("right")).toBeChecked();
    await userEvent.click(screen.getByLabelText("left"));
    await userEvent.type(screen.getByLabelText(/trim start/i), "30");
    await userEvent.type(screen.getByLabelText(/trim end/i), "40");
    await userEvent.click(screen.getByRole("button", { name: /upload/i }));

    await screen.findByText("job page");
    const post = fetchMock.mock.calls.find(([, i]) => i?.method === "POST")!;
    const fd = post[1]!.body as FormData;
    expect((fd.get("file") as File).name).toBe("clip.mp4");
    expect(fd.get("handedness")).toBe("left");
    expect(fd.get("trim_start_s")).toBe("30");
    expect(fd.get("trim_end_s")).toBe("40");
  });

  it("omits trim fields when empty and defaults handedness to right", async () => {
    const fetchMock = stubFetch((url, init) =>
      url === "/api/jobs" && init?.method === "POST"
        ? jsonResponse({ ...jobFx, status: "queued" }, 201)
        : undefined,
      listHandler(),
    );
    renderWithProviders(page);
    await userEvent.upload(
      screen.getByLabelText(/video file/i),
      new File(["x"], "a.MOV", { type: "video/quicktime" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /upload/i }));
    await screen.findByText("job page");
    const fd = fetchMock.mock.calls.find(([, i]) => i?.method === "POST")![1]!.body as FormData;
    expect(fd.get("handedness")).toBe("right");
    expect(fd.has("trim_start_s")).toBe(false);
    expect(fd.has("trim_end_s")).toBe(false);
  });

  it("rejects trim end <= start without calling the server", async () => {
    const fetchMock = stubFetch(listHandler());
    renderWithProviders(page);
    await userEvent.upload(
      screen.getByLabelText(/video file/i),
      new File(["x"], "clip.mp4", { type: "video/mp4" }),
    );
    await userEvent.type(screen.getByLabelText(/trim start/i), "10");
    await userEvent.type(screen.getByLabelText(/trim end/i), "5");
    await userEvent.click(screen.getByRole("button", { name: /upload/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/greater than trim start/i);
    expect(fetchMock.mock.calls.some(([, i]) => i?.method === "POST")).toBe(false);
  });

  it("shows the server's video_too_long message", async () => {
    stubFetch((url, init) =>
      url === "/api/jobs" && init?.method === "POST"
        ? jsonResponse(
            { error: { code: "video_too_long", message: "Video is 400 s; the limit is 300 s." } },
            422,
          )
        : undefined,
      listHandler(),
    );
    renderWithProviders(page);
    await userEvent.upload(
      screen.getByLabelText(/video file/i),
      new File(["x"], "clip.mkv", { type: "video/x-matroska" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /upload/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Video is 400 s; the limit is 300 s.");
  });

  it("lists recent jobs linking to /jobs/:id", async () => {
    stubFetch(listHandler([jobFx]));
    renderWithProviders(page);
    const link = await screen.findByRole("link", { name: "clip.mkv" });
    expect(link).toHaveAttribute("href", `/jobs/${jobFx.id}`);
    await waitFor(() => expect(screen.getByText("done")).toBeInTheDocument());
  });
});
