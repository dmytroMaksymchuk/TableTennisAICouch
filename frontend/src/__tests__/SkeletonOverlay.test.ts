import { describe, expect, it, vi } from "vitest";
import { computeContentBox, drawSkeleton, type Ctx2D } from "../components/SkeletonOverlay";
import { keypointsFx } from "../test/fixtures";
import type { Keypoints } from "../api/types";

function mockCtx() {
  return {
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 1,
  } satisfies Ctx2D;
}

describe("drawSkeleton", () => {
  it("passes scaled + offset coordinates to moveTo/lineTo", () => {
    const ctx = mockCtx();
    drawSkeleton(ctx, keypointsFx, 3, { scale: 0.5, offsetX: 10, offsetY: 4 });
    // head (j0): x = 100+3, y = 200 ; neck (j1): x = 150+3, y = 210
    expect(ctx.moveTo).toHaveBeenNthCalledWith(1, 103 * 0.5 + 10, 200 * 0.5 + 4);
    expect(ctx.lineTo).toHaveBeenNthCalledWith(1, 153 * 0.5 + 10, 210 * 0.5 + 4);
    expect(ctx.moveTo).toHaveBeenCalledTimes(4);
  });

  it("skips edges whose joint is null at that frame", () => {
    const x = keypointsFx.x.map((row) => [...row]) as (number | null)[][];
    const wrist = keypointsFx.joints.indexOf("r_wrist");
    x[wrist][7] = null;
    const kp: Keypoints = { ...keypointsFx, x, x_smooth: x };

    const ctx = mockCtx();
    drawSkeleton(ctx, kp, 7, { scale: 1, offsetX: 0, offsetY: 0 });
    expect(ctx.moveTo).toHaveBeenCalledTimes(3); // r_elbow-r_wrist edge dropped
    expect(ctx.arc).toHaveBeenCalledTimes(4); // wrist marker dropped too
  });
});

describe("computeContentBox", () => {
  it("letterboxes a 16:9 video in a taller element", () => {
    const b = computeContentBox(800, 600, 1280, 720);
    expect(b.scale).toBeCloseTo(800 / 1280);
    expect(b.offsetX).toBe(0);
    expect(b.offsetY).toBeCloseTo((600 - 450) / 2);
  });
});
