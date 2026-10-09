import { Link, useParams } from "react-router-dom";
import { useJob } from "../api/hooks";
import { JobStatusPanel } from "../components/JobStatusPanel";
import { PersonSelect } from "../components/PersonSelect";
import { ResultsView } from "../components/ResultsView";

export function JobPage() {
  const { jobId = "" } = useParams();
  const { data: job, error } = useJob(jobId);

  if (error && !job) return <p className="error">{error.message}</p>;
  if (!job) return <p className="muted">Loading...</p>;

  return (
    <div className="stack">
      <p>
        <Link to="/">&larr; All jobs</Link> - {job.original_filename}
      </p>
      {job.status === "awaiting_selection" && <PersonSelect job={job} />}
      {job.status === "done" && job.analysis_rev > 0 && <ResultsView job={job} />}
      {job.status !== "awaiting_selection" && !(job.status === "done" && job.analysis_rev > 0) && (
        <JobStatusPanel job={job} />
      )}
    </div>
  );
}
