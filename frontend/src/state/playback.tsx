import {
  createContext,
  useCallback,
  useRef,
  useContext,
  useMemo,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { frameToSeekTime, timeToFrame } from "../utils/time";

export type ChartRange = [number, number];

export interface PlaybackValue {
  fps: number;
  nFrames: number;
  /**
   * Playback time lives outside React state so per-frame updates do not re-render
   * consumers: read it with getTime() and listen with subscribeTime().
   */
  getTime: () => number;
  subscribeTime: (cb: (t: number) => void) => () => void;
  selectedStrokeId: string | null;
  /** x-axis range requested for the charts (seconds), null = autorange */
  chartRange: ChartRange | null;
  /** Seek to the frame containing time t (DESIGN §0). */
  seekToTime: (t: number) => void;
  seekToFrame: (f: number) => void;
  /** Called by the video player with the presented frame's media time; does not seek. */
  reportTime: (t: number) => void;
  selectStroke: (id: string | null) => void;
  setChartRange: (r: ChartRange | null) => void;
}

export const PlaybackContext = createContext<PlaybackValue | null>(null);

export function usePlayback(): PlaybackValue {
  const v = useContext(PlaybackContext);
  if (!v) throw new Error("usePlayback must be used inside a PlaybackProvider");
  return v;
}

interface ProviderProps {
  fps: number;
  nFrames: number;
  videoRef: RefObject<{ currentTime: number } | null>;
  children: ReactNode;
}

export function PlaybackProvider({ fps, nFrames, videoRef, children }: ProviderProps) {
  const timeRef = useRef(0);
  const listeners = useRef(new Set<(t: number) => void>());
  const setCurrentTime = useCallback((t: number) => {
    timeRef.current = t;
    listeners.current.forEach((cb) => cb(t));
  }, []);
  const getTime = useCallback(() => timeRef.current, []);
  const subscribeTime = useCallback((cb: (t: number) => void) => {
    listeners.current.add(cb);
    return () => {
      listeners.current.delete(cb);
    };
  }, []);
  const [selectedStrokeId, selectStroke] = useState<string | null>(null);
  const [chartRange, setChartRange] = useState<ChartRange | null>(null);

  const seekToFrame = useCallback(
    (f: number) => {
      const frame = Math.min(Math.max(Math.round(f), 0), Math.max(nFrames - 1, 0));
      const t = frameToSeekTime(frame, fps);
      if (videoRef.current) videoRef.current.currentTime = t;
      setCurrentTime(t);
    },
    [fps, nFrames, videoRef, setCurrentTime],
  );

  const seekToTime = useCallback(
    (t: number) => seekToFrame(timeToFrame(t, fps, nFrames)),
    [fps, nFrames, seekToFrame],
  );

  const value = useMemo<PlaybackValue>(
    () => ({
      fps,
      nFrames,
      getTime,
      subscribeTime,
      selectedStrokeId,
      chartRange,
      seekToTime,
      seekToFrame,
      reportTime: setCurrentTime,
      selectStroke,
      setChartRange,
    }),
    [fps, nFrames, getTime, subscribeTime, selectedStrokeId, chartRange, seekToTime, seekToFrame, setCurrentTime],
  );

  return <PlaybackContext.Provider value={value}>{children}</PlaybackContext.Provider>;
}
