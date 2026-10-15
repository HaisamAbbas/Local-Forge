import { useEffect, useId, useState } from "react";

import {
  cancelTune,
  getTune,
  listTunes,
  startTune,
  updateEngineProfile,
  type EngineProfile,
  type TuneJob,
  type TuneObjective,
} from "../lib/api";
import {
  OBJECTIVE_LABEL,
  changedFromProfile,
  jobIsActive,
  scoreText,
  settingsText,
} from "../lib/tune";

const POLL_MS = 2000;

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/** Tune CPU threads, GPU layers and batch size of one managed llama.cpp engine. */
export function EngineTuner({
  profile,
  onApplied,
}: {
  profile: EngineProfile;
  onApplied: () => void;
}) {
  const [job, setJob] = useState<TuneJob | null>(null);
  const [objective, setObjective] = useState<TuneObjective>("throughput");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "done"; text: string } | null>(null);
  const objectiveId = useId();
  const running = job !== null && jobIsActive(job);

  // Pick up a tune that was started before this page was opened.
  useEffect(() => {
    let active = true;
    listTunes().then(
      (jobs) => {
        const latest = jobs.find((item) => item.engine_profile_id === profile.id);
        if (active && latest) setJob(latest);
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, [profile.id]);

  const jobId = job?.id;
  useEffect(() => {
    if (!jobId || !running) return;
    let active = true;
    const timer = window.setInterval(() => {
      getTune(jobId).then(
        (next) => {
          if (active) setJob(next);
        },
        (error: unknown) => {
          if (active) setMessage({ kind: "error", text: errorText(error, "Lost the tune.") });
        },
      );
    }, POLL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [jobId, running]);

  async function start() {
    setBusy(true);
    setMessage(null);
    try {
      setJob(await startTune(profile.id, objective));
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error, "Could not start the tune.") });
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    if (!job) return;
    try {
      setJob(await cancelTune(job.id));
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error, "Could not stop the tune.") });
    }
  }

  async function apply() {
    if (!job?.recommended_launch_settings) return;
    setBusy(true);
    setMessage(null);
    try {
      await updateEngineProfile(profile.id, {
        engine_extensions: { llama_cpp: job.recommended_launch_settings },
      });
      setMessage({
        kind: "done",
        text: "Saved. These settings are used the next time the server starts.",
      });
      onApplied();
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error, "Could not save the settings.") });
    } finally {
      setBusy(false);
    }
  }

  const recommended = job?.recommended_launch_settings ?? null;
  const differs =
    recommended !== null &&
    changedFromProfile(recommended, profile.engine_extensions?.llama_cpp).length > 0;
  const hasWinner = job !== null && Object.keys(job.best_settings).length > 0;

  return (
    <section className="engine-tuner" aria-label={`Tune ${profile.label}`}>
      <h4>Tune launch settings</h4>
      <p className="helper-copy">
        Restarts a private copy of this server with different CPU threads, GPU layers and batch
        sizes, benchmarks each, and keeps a change only if it clearly helps. It takes several
        minutes and uses the same CPU and memory as the server, so close other programs
        {profile.managed_process_running ? " and stop this server first" : ""}. Your saved runs are
        not touched.
      </p>

      {running && job ? (
        <div className="engine-tuner__progress" role="status">
          <p>
            <strong>
              Trial {Math.min(job.trials.length + 1, job.total_trials)} of {job.total_trials}
            </strong>
            {job.stopping
              ? " · stopping after this trial"
              : ` · goal: ${job.objective_description}`}
          </p>
          <progress max={job.total_trials} value={job.trials.length} />
          <button
            className="button button--quiet"
            disabled={job.stopping}
            onClick={() => void stop()}
            type="button"
          >
            {job.stopping ? "Stopping…" : "Stop tuning"}
          </button>
        </div>
      ) : (
        <div className="engine-tuner__start">
          <label htmlFor={objectiveId}>
            <span className="fact-label">Optimise for</span>
            <select
              id={objectiveId}
              value={objective}
              onChange={(event) => setObjective(event.target.value as TuneObjective)}
            >
              {(Object.keys(OBJECTIVE_LABEL) as TuneObjective[]).map((key) => (
                <option key={key} value={key}>
                  {OBJECTIVE_LABEL[key]}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button button--primary"
            disabled={busy}
            onClick={() => void start()}
            type="button"
          >
            {busy ? "Starting…" : job ? "Tune again" : "Start tuning"}
          </button>
        </div>
      )}

      {message ? (
        <p
          className={message.kind === "error" ? "inline-warning" : "helper-copy"}
          role={message.kind === "error" ? "alert" : "status"}
        >
          {message.text}
        </p>
      ) : null}

      {job && job.trials.length > 0 ? (
        <div className="table-scroll">
          <table>
            <caption className="visually-hidden">Tuning trials</caption>
            <thead>
              <tr>
                <th scope="col">Settings tried</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {job.trials.map((trial, index) => (
                <tr key={index}>
                  <th scope="row">{settingsText(trial.settings)}</th>
                  <td>{scoreText(job, trial)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {job && job.status === "completed" ? (
        <div className="engine-tuner__result" role="status">
          {hasWinner ? (
            <p>
              <strong>Best: {settingsText(job.best_settings)}</strong>
              {job.improvement_percent !== null
                ? ` (${job.improvement_percent >= 0 ? "+" : ""}${job.improvement_percent}% vs your current settings)`
                : ""}
              . Small gains can be noise; tune again on a quiet machine to confirm.
            </p>
          ) : (
            <p>
              <strong>Your current settings are already about as good.</strong> No change beat them
              by {job.min_gain_percent}%.
            </p>
          )}
          {hasWinner && differs ? (
            <button
              className="button button--primary"
              disabled={busy || profile.managed_process_running}
              title={
                profile.managed_process_running
                  ? "Stop the server first; settings apply at the next start."
                  : undefined
              }
              onClick={() => void apply()}
              type="button"
            >
              Apply these settings
            </button>
          ) : null}
        </div>
      ) : null}
      {job && job.status === "failed" ? (
        <p className="inline-warning" role="alert">
          The tune failed: {job.error ?? "no details"}
        </p>
      ) : null}
      {job && job.status === "cancelled" ? (
        <p className="helper-copy" role="status">
          Tuning stopped. The trials above are kept, but nothing was changed.
        </p>
      ) : null}
    </section>
  );
}
