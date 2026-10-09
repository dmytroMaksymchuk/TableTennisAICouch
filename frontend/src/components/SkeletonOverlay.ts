import type { Keypoints } from "../api/types";

export interface ContentBox {
  /** rendered px per video px */
  scale: number;
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
}

/** Letterbox-aware placement of video content inside an `object-fit: contain` element. */
export function computeContentBox(
  elWidth: number,
  elHeight: number,
  videoWidth: number,
  videoHeight: number,
): ContentBox {
  if (!videoWidth || !videoHeight || !elWidth || !elHeight) {
    return { scale: 1, offsetX: 0, offsetY: 0, width: elWidth, height: elHeight };
  }
  const scale = Math.min(elWidth / videoWidth, elHeight / videoHeight);
  const width = videoWidth * scale;
  const height = videoHeight * scale;
  return { scale, offsetX: (elWidth - width) / 2, offsetY: (elHeight - height) / 2, width, height };
}

export interface Transform {
  scale: number;
  offsetX: number;
  offsetY: number;
}

export type Ctx2D = Pick<
  CanvasRenderingContext2D,
  "beginPath" | "moveTo" | "lineTo" | "stroke" | "arc" | "fill"
> & { strokeStyle: unknown; fillStyle: unknown; lineWidth: number };

function pick(
  smooth: (number | null)[] | undefined,
  raw: (number | null)[] | undefined,
  f: number,
): number | null {
  return smooth?.[f] ?? raw?.[f] ?? null;
}

/** Draws the skeleton of frame `f`. Edges with a missing joint are skipped. */
export function drawSkeleton(ctx: Ctx2D, kp: Keypoints, f: number, tr: Transform): void {
  const index = new Map(kp.joints.map((name, i) => [name, i]));
  const point = (name: string): [number, number] | null => {
    const j = index.get(name);
    if (j === undefined) return null;
    const x = pick(kp.x_smooth[j], kp.x[j], f);
    const y = pick(kp.y_smooth[j], kp.y[j], f);
    if (x === null || y === null) return null;
    return [x * tr.scale + tr.offsetX, y * tr.scale + tr.offsetY];
  };

  ctx.strokeStyle = "#22d3ee";
  ctx.lineWidth = 2;
  for (const [a, b] of kp.edges) {
    const pa = point(a);
    const pb = point(b);
    if (!pa || !pb) continue;
    ctx.beginPath();
    ctx.moveTo(pa[0], pa[1]);
    ctx.lineTo(pb[0], pb[1]);
    ctx.stroke();
  }

  ctx.fillStyle = "#facc15";
  for (const name of kp.joints) {
    const p = point(name);
    if (!p) continue;
    ctx.beginPath();
    ctx.arc(p[0], p[1], 3, 0, Math.PI * 2);
    ctx.fill();
  }
}
