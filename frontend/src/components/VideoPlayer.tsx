import { useCallback, useEffect, useRef, type RefObject } from "react";
import { api } from "../api/client";
import type { Keypoints } from "../api/types";
import { usePlayback } from "../state/playback";
import { timeToFrame } from "../utils/time";
import { computeContentBox, drawSkeleton } from "./SkeletonOverlay";

interface Props {
  jobId: string;
  keypoints: Keypoints;
  videoRef: RefObject<HTMLVideoElement>;
}

type VideoWithRvfc = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

export function VideoPlayer({ jobId, keypoints, videoRef }: Props) {
  const { fps, nFrames, reportTime } = usePlayback();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const lastTime = useRef(0);

  const draw = useCallback(
    (mediaTime: number) => {
      lastTime.current = mediaTime;
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas) return;
      const w = video.clientWidth;
      const h = video.clientHeight;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const box = computeContentBox(w, h, keypoints.width, keypoints.height);
      const f = timeToFrame(mediaTime, fps, nFrames);
      drawSkeleton(ctx, keypoints, f, box);
    },
    [fps, nFrames, keypoints, videoRef],
  );

  useEffect(() => {
    const video = videoRef.current as VideoWithRvfc | null;
    if (!video) return;
    let handle = 0;
    let raf = 0;
    let stopped = false;

    const onFrame = (_now: number, meta: { mediaTime: number }) => {
      if (stopped) return;
      reportTime(meta.mediaTime);
      draw(meta.mediaTime);
      handle = video.requestVideoFrameCallback!(onFrame);
    };
    const tick = () => {
      if (stopped) return;
      reportTime(video.currentTime);
      draw(video.currentTime);
      raf = requestAnimationFrame(tick);
    };
    if (video.requestVideoFrameCallback) handle = video.requestVideoFrameCallback(onFrame);
    else raf = requestAnimationFrame(tick);

    const onSeeked = () => {
      reportTime(video.currentTime);
      draw(video.currentTime);
    };
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("loadeddata", onSeeked);

    const ro = new ResizeObserver(() => draw(lastTime.current));
    ro.observe(video);

    return () => {
      stopped = true;
      if (handle && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(handle);
      if (raf) cancelAnimationFrame(raf);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("loadeddata", onSeeked);
      ro.disconnect();
    };
  }, [draw, reportTime, videoRef]);

  return (
    <div className="video-wrap">
      <video ref={videoRef} src={api.videoUrl(jobId)} controls preload="auto" playsInline />
      <canvas ref={canvasRef} className="video-overlay" />
    </div>
  );
}
