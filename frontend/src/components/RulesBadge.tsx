import { useReanalyze } from "../api/hooks";

interface Props {
  jobId: string;
  rulesetId: string;
  rulesetVersion: string;
}

export function RulesBadge({ jobId, rulesetId, rulesetVersion }: Props) {
  const reanalyze = useReanalyze(jobId);
  return (
    <div className="rules-badge">
      <span className="badge">
        rules: {rulesetId} v{rulesetVersion}
      </span>
      <button type="button" disabled={reanalyze.isPending} onClick={() => reanalyze.mutate()}>
        Re-analyze with current rules
      </button>
      {reanalyze.error && (
        <p role="alert" className="error">
          {reanalyze.error.message}
        </p>
      )}
    </div>
  );
}
