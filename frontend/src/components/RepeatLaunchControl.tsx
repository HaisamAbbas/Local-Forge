import { useState } from "react";

import {
  type BenchmarkRun,
  type ExperimentSummary,
  type RepeatSpreadMetric,
  getExperimentGroup,
  getExperimentSummary,
  getRun,
  launchNextExperimentTrial,
  repeatBenchmarkPlan,
  startBenchmarkRun,
  startExperimentGroup,
} from "../lib/api";
import { formatNumber, keySpreadMetrics } from "../lib/run-conditions";
import { isTerminalRunStatus } from "../lib/run-state";
import { SpreadBadge } from "./RunConditions";

const POLL_MILLISECONDS = 1200;

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "The repeated runs could not finish.";
}

type Props = {
  planId: string;
  disabled?: boolean;
  /** Called with each run as it starts, so the caller can link to it. */
  onRunStarted?: (run: BenchmarkRun) => void;
};

/** Launch one saved plan N times in a row and show the run-to-run spread. */
export function RepeatLaunchControl({ planId, disabled = false, onRunStarted }: Props) {
  const [count, setCount] = useState(3);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);

  async function launch() {
    setBusy(true);
    setError(null);
    setSummary(null);
    try {
      if (count === 1) {
        const result = await startBenchmarkRun(planId, false);
        onRunStarted?.(result.run);
        setProgress("Started one run.");
        return;
      }
      const created = await repeatBenchmarkPlan(planId, count);
      const estimate = created.estimated_duration_seconds;
      const needsConfirmation = estimate === null || estimate > 300;
      if (
        needsConfirmation &&
        !window.confirm(
          `Run this plan ${count} times in a row? Estimated total: ${
            estimate === null ? "unknown" : `${Math.round(estimate / 60)} min`
          }.`,
        )
      ) {
        setProgress("Saved as a draft repeat in Experiments; start it there when ready.");
        return;
      }
      const groupId = created.group.id;
      await startExperimentGroup(groupId, needsConfirmation);
      let finished = 0;
      for (;;) {
        const next = await launchNextExperimentTrial(groupId);
        if (!next.run) break;
        onRunStarted?.(next.run);
        setProgress(`Repeat ${finished + 1} of ${count} running…`);
        let run = next.run;
        while (!isTerminalRunStatus(run.status)) {
          await wait(POLL_MILLISECONDS);
          run = await getRun(run.id);
        }
        finished += 1;
        const group = await getExperimentGroup(groupId);
        if (group.status !== "running") break;
      }
      setSummary(await getExperimentSummary(groupId));
      setProgress(`Finished ${count} repeats. Compare them in Experiments for every metric.`);
    } catch (launchError) {
      setError(message(launchError));
    } finally {
      setBusy(false);
    }
  }

  const baseline = summary?.variants[0];
  const rows = baseline ? keySpreadMetrics(baseline.metrics as RepeatSpreadMetric[]) : [];

  return (
    <div className="repeat-launch">
      <label className="repeat-launch-count">
        <span>Repeat</span>
        <input
          aria-label="Repeat count"
          type="number"
          min={1}
          max={10}
          value={count}
          disabled={busy}
          onChange={(event) =>
            setCount(Math.min(10, Math.max(1, Math.round(Number(event.target.value) || 1))))
          }
        />
        <span>times</span>
      </label>
      <button
        className="button button--secondary"
        type="button"
        disabled={disabled || busy}
        onClick={() => void launch()}
      >
        {busy ? "Repeating…" : count === 1 ? "Run once" : `Run ${count} times`}
      </button>
      <span className="workload-action-hint">
        Repeats show how much results vary run to run; 3 or more is recommended before comparing.
      </span>
      {progress && <p className="repeat-launch-progress">{progress}</p>}
      {error && (
        <p className="notice notice--warning" role="alert">
          {error}
        </p>
      )}
      {rows.length > 0 && (
        <table className="repeat-spread-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Median</th>
              <th>Min–max</th>
              <th>Spread</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ label, metric }) => (
              <tr key={label}>
                <th scope="row">
                  {label}
                  {metric && <small>{metric.unit}</small>}
                </th>
                <td>{formatNumber(metric?.median)}</td>
                <td>
                  {metric?.spread_min === null || metric?.spread_min === undefined
                    ? "Unavailable"
                    : `${formatNumber(metric.spread_min)}–${formatNumber(metric.spread_max)}`}
                </td>
                <td>
                  <SpreadBadge metric={metric} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
