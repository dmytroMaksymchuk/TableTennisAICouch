import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useJobs } from "../api/hooks";
import type { Handedness } from "../api/types";

const EXTENSIONS = [".mp4", ".mkv", ".mov"];

function optNumber(s: string): number | null {
  return s.trim() === "" ? null : Number(s);
}

export function UploadPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const jobs = useJobs();
  const [file, setFile] = useState<File | null>(null);
  const [handedness, setHandedness] = useState<Handedness>("right");
  const [trimStart, setTrimStart] = useState("");
  const [trimEnd, setTrimEnd] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: api.createJob,
    onSuccess: (job) => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      navigate(`/jobs/${job.id}`);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    if (!file) return setFormError("Choose a video file.");
    if (!EXTENSIONS.some((x) => file.name.toLowerCase().endsWith(x))) {
      return setFormError(`Unsupported file type; use ${EXTENSIONS.join(", ")}.`);
    }
    const start = optNumber(trimStart);
    const end = optNumber(trimEnd);
    if ((start !== null && !(start >= 0)) || (end !== null && Number.isNaN(end))) {
      return setFormError("Trim values must be numbers >= 0.");
    }
    if (end !== null && end <= (start ?? 0)) {
      return setFormError("Trim end must be greater than trim start.");
    }
    create.mutate({ file, handedness, trimStart: start, trimEnd: end });
  };

  return (
    <div className="stack">
      <section className="panel">
        <h2>Analyze a clip</h2>
        <form onSubmit={submit} className="stack">
          <label>
            Video file
            <input
              type="file"
              accept={EXTENSIONS.join(",")}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <fieldset>
            <legend>Handedness</legend>
            {(["right", "left"] as const).map((h) => (
              <label key={h}>
                <input
                  type="radio"
                  name="handedness"
                  value={h}
                  checked={handedness === h}
                  onChange={() => setHandedness(h)}
                />{" "}
                {h}
              </label>
            ))}
          </fieldset>
          <div className="row">
            <label>
              Trim start (s)
              <input
                type="number"
                min={0}
                step="any"
                value={trimStart}
                onChange={(e) => setTrimStart(e.target.value)}
              />
            </label>
            <label>
              Trim end (s)
              <input
                type="number"
                min={0}
                step="any"
                value={trimEnd}
                onChange={(e) => setTrimEnd(e.target.value)}
              />
            </label>
          </div>
          <button type="submit" disabled={create.isPending}>
            {create.isPending ? "Uploading..." : "Upload and analyze"}
          </button>
          {(formError || create.error) && (
            <p role="alert" className="error">
              {formError ?? create.error?.message}
            </p>
          )}
        </form>
      </section>

      <section className="panel">
        <h2>Recent jobs</h2>
        {jobs.data?.jobs.length ? (
          <ul>
            {jobs.data.jobs.map((j) => (
              <li key={j.id}>
                <Link to={`/jobs/${j.id}`}>{j.original_filename}</Link>{" "}
                <span className="badge">{j.status}</span>{" "}
                <span className="muted">{j.created_at}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No jobs yet.</p>
        )}
      </section>
    </div>
  );
}
