import { useRef } from "react";
import { useResults } from "../api/hooks";
import type { Job } from "../api/types";
import { PlaybackProvider } from "../state/playback";
import { AngleCharts } from "./AngleCharts";
import { RulesBadge } from "./RulesBadge";
import { StrokeList } from "./StrokeList";
import { VideoPlayer } from "./VideoPlayer";
import { ViewNotice } from "./ViewNotice";

export function ResultsView({ job }: { job: Job }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { results, error, isLoading } = useResults(job.id, job.analysis_rev);

  if (error) return <p className="error">Failed to load results: {error.message}</p>;
  if (isLoading || !results) return <p className="muted">Loading results...</p>;

  const { keypoints, angles, strokes, report } = results;
  const { summary } = report;

  return (
    <PlaybackProvider fps={report.video.fps} nFrames={report.video.n_frames} videoRef={videoRef}>
      <div className="results">
        <div className="results-main">
          <VideoPlayer jobId={job.id} keypoints={keypoints} videoRef={videoRef} />
          <AngleCharts angles={angles} strokes={strokes.strokes} />
        </div>
        <aside className="results-side">
          <section className="panel">
            <ViewNotice view={report.view} />
            <p>
              {summary.n_strokes} strokes ({summary.n_fh} FH, {summary.n_bh} BH) - feedback:{" "}
              {summary.feedback_counts.critical} critical, {summary.feedback_counts.warning}{" "}
              warnings, {summary.feedback_counts.info} info
            </p>
            <RulesBadge
              jobId={job.id}
              rulesetId={report.ruleset.id}
              rulesetVersion={report.ruleset.version}
            />
          </section>
          <StrokeList strokes={strokes.strokes} />
        </aside>
      </div>
    </PlaybackProvider>
  );
}
