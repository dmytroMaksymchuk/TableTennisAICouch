import { useState } from "react";
import { useCandidates, useSelectPersons } from "../api/hooks";
import { api } from "../api/client";
import type { Job } from "../api/types";

export function PersonSelect({ job }: { job: Job }) {
  const { data, isLoading, error } = useCandidates(job.id, true);
  const select = useSelectPersons(job.id);
  const [chosen, setChosen] = useState<Set<string> | null>(null);

  if (isLoading) return <p className="muted">Loading candidates...</p>;
  if (error || !data) return <p className="error">{error?.message ?? "No candidates"}</p>;

  const selected = chosen ?? new Set(data.suggested_person_ids);
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChosen(next);
  };
  const vw = job.video?.width ?? 1;
  const vh = job.video?.height ?? 1;

  return (
    <section className="panel">
      <h2>Who is the player?</h2>
      <p className="muted">
        Several people were detected. Click to select the player (select more than one if the same
        player was split across tracks), then analyze.
      </p>
      <div className="candidates">
        {data.candidates.map((c) => {
          const [x0, y0, x1, y1] = c.bbox_at_preview;
          const isOn = selected.has(c.person_id);
          return (
            <button
              type="button"
              key={c.person_id}
              className={`candidate${isOn ? " on" : ""}`}
              aria-pressed={isOn}
              aria-label={`Person ${c.person_id}`}
              onClick={() => toggle(c.person_id)}
            >
              <div className="candidate-frame">
                <img src={api.frameUrl(job.id, c.preview_frame)} alt={`preview of ${c.person_id}`} />
                <div
                  className="bbox"
                  data-testid={`bbox-${c.person_id}`}
                  style={{
                    left: `${(x0 / vw) * 100}%`,
                    top: `${(y0 / vh) * 100}%`,
                    width: `${((x1 - x0) / vw) * 100}%`,
                    height: `${((y1 - y0) / vh) * 100}%`,
                  }}
                />
              </div>
              <span>
                {c.person_id} - coverage {Math.round(c.coverage * 100)}%
                {data.suggested_person_ids.includes(c.person_id) && " (suggested)"}
              </span>
            </button>
          );
        })}
      </div>
      <button
        type="button"
        disabled={selected.size === 0 || select.isPending}
        onClick={() => select.mutate([...selected])}
      >
        Analyze selected
      </button>
      {select.error && <p className="error">{select.error.message}</p>}
    </section>
  );
}
