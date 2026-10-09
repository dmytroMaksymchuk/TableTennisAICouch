import { describe, expect, it } from "vitest";
import { frameToSeekTime, frameToTime, timeToFrame } from "../utils/time";

const FPS = [25, 29.97, 30, 50, 60];
const N = 400;

describe("time utils", () => {
  it.each(FPS)("timeToFrame(f/fps) == f at %s fps (epsilon case)", (fps) => {
    for (let f = 0; f < N; f++) {
      expect(timeToFrame(f / fps, fps, N)).toBe(f);
    }
  });

  it.each(FPS)("timeToFrame(frameToSeekTime(f)) == f at %s fps", (fps) => {
    for (let f = 0; f < N; f++) {
      expect(timeToFrame(frameToSeekTime(f, fps), fps, N)).toBe(f);
    }
  });

  it("clamps at both ends", () => {
    expect(timeToFrame(-1, 30, 100)).toBe(0);
    expect(timeToFrame(1000, 30, 100)).toBe(99);
    expect(timeToFrame(99 / 30, 30, 100)).toBe(99);
  });

  it("seek time is the middle of the frame interval; reported time is f/fps", () => {
    expect(frameToSeekTime(10, 50)).toBeCloseTo(10.5 / 50, 12);
    expect(frameToTime(10, 50)).toBeCloseTo(0.2, 12);
  });
});
