// Frame/time rule: DESIGN.md §0.
export function timeToFrame(t: number, fps: number, nFrames: number): number {
  const f = Math.floor(t * fps + 1e-6);
  return Math.min(Math.max(f, 0), Math.max(nFrames - 1, 0));
}

export function frameToSeekTime(f: number, fps: number): number {
  return (f + 0.5) / fps;
}

export function frameToTime(f: number, fps: number): number {
  return f / fps;
}
