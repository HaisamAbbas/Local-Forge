import { useCallback, useEffect, useMemo, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import {
  compareRuns,
  createExperimentGroup,
  createReport,
  fetchWorkspaceSettings,
  getExperimentGroup,
  getExperimentSummary,
  getRun,
  importBenchmarkPlan,
  launchNextExperimentTrial,
  listBenchmarkPlans,
  listEngineProfiles,
  listExperimentGroups,
  listRuns,
  startExperimentGroup,
} from "../lib/api";
import { fixtureFilter } from "../lib/test-fixtures";
import { ExperimentVariantCharts, RunComparisonCharts, RunParetoChart } from "./ComparisonCharts";
import type {
  BenchmarkPlan,
  BenchmarkRun,
  EngineProfile,
  ExperimentCreateInput,
  ExperimentGroup,
  ExperimentSummary,
  ReportMetadata,
  RunComparison,
  WorkspaceSettingsResponse,
} from "../lib/api";

const sweepVariables = [
  ["concurrency", "Concurrency"],
  ["max_output_tokens", "Maximum output tokens"],
  ["arrival_rate_per_second", "Target arrival rate"],
  ["sampling_config.temperature", "Temperature"],
  ["sampling_config.top_p", "Top-p"],
  ["sampling_config.top_k", "Top-k"],
  ["sampling_config.seed", "Sampling seed"],
] as const;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "LOCALFORGE could not complete this action.";
}

function pretty(value: unknown): string {
  if (value === null || value === undefined) return "Not recorded";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function shortRun(id: string): string {
  return id.slice(0, 8);
}

type MetricIdentity = Pick<
  RunComparison["shared_metrics"][number],
  | "metric_name"
  | "unit"
  | "source"
  | "aggregation"
  | "definition"
  | "formula"
  | "method_version"
  | "metric_kind"
  | "labels"
>;

function metricIdentity(metric: MetricIdentity): string {
  return JSON.stringify([
    metric.metric_name,
    metric.unit,
    metric.source,
    metric.aggregation,
    metric.definition,
    metric.formula,
    metric.method_version,
    metric.metric_kind,
    metric.labels,
  ]);
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "Not estimated";
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  const minutes = Math.round(seconds / 60);
  return `${minutes} min`;
}

function isTerminal(run: BenchmarkRun): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(run.status);
}

/** Everything the screen lists. Engines and Settings only filter fixtures, so they may fail. */
async function loadScreenData() {
  const [runs, plans, groups, engines, settings] = await Promise.all([
    listRuns(100),
    listBenchmarkPlans(),
    listExperimentGroups(),
    listEngineProfiles().catch((): EngineProfile[] => []),
    fetchWorkspaceSettings().catch(() => null),
  ]);
  return { runs, plans, groups, engines, settings };
}

/** First ready plan that is not a hidden test fixture. */
function defaultBaselinePlan(data: Awaited<ReturnType<typeof loadScreenData>>): string {
  const { isFixturePlan } = fixtureFilter(data.engines, data.plans, data.settings);
  return data.plans.find((plan) => plan.status === "ready" && !isFixturePlan(plan))?.id ?? "";
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export function ExperimentsScreen() {
  const [runs, setRuns] = useState<BenchmarkRun[]>([]);
  const [plans, setPlans] = useState<BenchmarkPlan[]>([]);
  const [engines, setEngines] = useState<EngineProfile[]>([]);
  const [settings, setSettings] = useState<WorkspaceSettingsResponse | null>(null);
  const planNames = useMemo(() => new Map(plans.map((plan) => [plan.id, plan.name])), [plans]);
  const runLabel = useCallback(
    (runId: string) => {
      const run = runs.find((item) => item.id === runId);
      return (run && planNames.get(run.plan_id)) ?? `Run ${shortRun(runId)}`;
    },
    [runs, planNames],
  );
  const [groups, setGroups] = useState<ExperimentGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [experimentSummary, setExperimentSummary] = useState<ExperimentSummary | null>(null);
  const [activeComparisonGroupId, setActiveComparisonGroupId] = useState<string | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [activeGroupId, setActiveGroupId] = useState<string | null>(null);
  const [activeTrialRun, setActiveTrialRun] = useState<BenchmarkRun | null>(null);
  const [latestReport, setLatestReport] = useState<ReportMetadata | null>(null);
  const [reportFormat, setReportFormat] = useState<"json" | "csv" | "markdown">("markdown");
  const [reportName, setReportName] = useState("LOCALFORGE comparison report");
  const [includeRetainedContent, setIncludeRetainedContent] = useState(false);
  const [planImportBusy, setPlanImportBusy] = useState(false);
  const [draft, setDraft] = useState({
    name: "Controlled workload sweep",
    baselinePlanId: "",
    independentVariable: "sampling_config.temperature",
    variantValues: "0.5\n0.9",
    repetitions: "1",
    randomizeOrder: true,
    orderSeed: "42",
    hypothesis: "",
  });

  const applyScreenData = useCallback((data: Awaited<ReturnType<typeof loadScreenData>>) => {
    setRuns(data.runs);
    setPlans(data.plans);
    setGroups(data.groups);
    setEngines(data.engines);
    setSettings(data.settings);
    setDraft((current) => ({
      ...current,
      baselinePlanId: current.baselinePlanId || defaultBaselinePlan(data),
    }));
  }, []);

  const refresh = useCallback(async () => {
    applyScreenData(await loadScreenData());
  }, [applyScreenData]);

  useEffect(() => {
    let active = true;
    void loadScreenData()
      .then((data) => {
        if (active) applyScreenData(data);
      })
      .catch((loadError: unknown) => {
        if (active) setError(errorMessage(loadError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [applyScreenData]);

  const fixtures = useMemo(
    () => fixtureFilter(engines, plans, settings),
    [engines, plans, settings],
  );
  const visiblePlans = useMemo(
    () => plans.filter((plan) => !fixtures.isFixturePlan(plan)),
    [plans, fixtures],
  );
  const readyPlans = visiblePlans.filter((plan) => plan.status === "ready");
  const hiddenPlanCount = plans.length - visiblePlans.length;
  const selectableRuns = useMemo(
    () =>
      runs.filter(
        (run) =>
          (run.status === "completed" || run.status === "failed") && !fixtures.isFixtureRun(run),
      ),
    [runs, fixtures],
  );
  const selectedPlans = useMemo(() => {
    const planById = new Map(plans.map((plan) => [plan.id, plan]));
    return selectedRuns.map((id) => {
      const run = runs.find((item) => item.id === id);
      return run ? (planById.get(run.plan_id) ?? null) : null;
    });
  }, [plans, runs, selectedRuns]);
  const canIncludeContent =
    selectedPlans.length > 0 && selectedPlans.every((plan) => plan?.retain_prompt_output === true);

  const toggleRun = (runId: string) => {
    setComparison(null);
    setExperimentSummary(null);
    setActiveComparisonGroupId(null);
    setLatestReport(null);
    setSelectedRuns((current) =>
      current.includes(runId)
        ? current.filter((id) => id !== runId)
        : current.length < 20
          ? [...current, runId]
          : current,
    );
  };

  const compareSelected = async () => {
    setBusy(true);
    setError(null);
    setLatestReport(null);
    setComparisonLoading(true);
    setActiveComparisonGroupId(null);
    setExperimentSummary(null);
    try {
      const result = await compareRuns({ run_ids: selectedRuns });
      setComparison(result);
    } catch (compareError) {
      setError(errorMessage(compareError));
    } finally {
      setBusy(false);
      setComparisonLoading(false);
    }
  };

  const compareGroup = async (group: ExperimentGroup) => {
    const runIds = group.trials.flatMap((trial) => (trial.run_id ? [trial.run_id] : []));
    if (runIds.length < 2) return;
    setBusy(true);
    setComparison(null);
    setLatestReport(null);
    setComparisonLoading(true);
    setError(null);
    setActiveComparisonGroupId(group.id);
    setSelectedRuns(runIds);
    setReportName(`${group.name} results`);
    try {
      const [result, summary] = await Promise.all([
        compareRuns({
          run_ids: runIds,
          declared_variable: group.independent_variable,
          baseline_run_id:
            group.trials.find((trial) => trial.variant_name === "Baseline")?.run_id ?? runIds[0],
        }),
        getExperimentSummary(group.id),
      ]);
      setComparison(result);
      setExperimentSummary(summary);
    } catch (compareError) {
      setError(errorMessage(compareError));
    } finally {
      setComparisonLoading(false);
      setBusy(false);
    }
  };

  const parseVariants = (): ExperimentCreateInput["variants"] => {
    const isInteger = [
      "concurrency",
      "max_output_tokens",
      "sampling_config.top_k",
      "sampling_config.seed",
    ].includes(draft.independentVariable);
    const values = draft.variantValues
      .split(/\r?\n|,/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (!values.length) throw new Error("Enter at least one variant value.");
    return values.map((raw, index) => {
      const value = Number(raw);
      if (!Number.isFinite(value) || (isInteger && !Number.isInteger(value))) {
        throw new Error(
          isInteger ? "Use whole numbers for this variable." : "Use numeric variant values.",
        );
      }
      return { name: `Variant ${index + 1}`, value };
    });
  };

  const runGroupToCompletion = async (groupId: string) => {
    setActiveGroupId(groupId);
    setBusy(true);
    setError(null);
    setNotice(
      "The sweep runs one trial at a time. You can return later and continue if the browser closes.",
    );
    try {
      for (;;) {
        let currentGroup = await getExperimentGroup(groupId);
        const activeTrial = currentGroup.trials.find(
          (trial) => trial.status === "running" && trial.run_id,
        );
        if (activeTrial?.run_id) {
          let run = await getRun(activeTrial.run_id);
          setActiveTrialRun(run);
          while (!isTerminal(run)) {
            await sleep(1200);
            run = await getRun(run.id);
            setActiveTrialRun(run);
          }
          await refresh();
          currentGroup = await getExperimentGroup(groupId);
        }
        if (currentGroup.status !== "running") break;
        const next = await launchNextExperimentTrial(groupId);
        currentGroup = next.group;
        setGroups((existing) =>
          existing.map((item) => (item.id === groupId ? currentGroup : item)),
        );
        if (!next.run) break;
        setActiveTrialRun(next.run);
      }
      await refresh();
      setNotice("The experiment sweep has finished. Failed trials remain in its history.");
    } catch (runError) {
      setError(errorMessage(runError));
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
      setActiveGroupId(null);
      setActiveTrialRun(null);
    }
  };

  const submitSweep = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const input: ExperimentCreateInput = {
        name: draft.name.trim(),
        baseline_plan_id: draft.baselinePlanId,
        independent_variable: draft.independentVariable,
        variants: parseVariants(),
        repetitions: Number(draft.repetitions),
        randomize_order: draft.randomizeOrder,
        order_seed: Number(draft.orderSeed),
        hypothesis: draft.hypothesis.trim() || null,
      };
      const created = await createExperimentGroup(input);
      await refresh();
      setGroups((existing) => [
        created.group,
        ...existing.filter((item) => item.id !== created.group.id),
      ]);
      setActiveGroupId(created.group.id);
      const needsConfirmation =
        created.estimated_duration_seconds === null || created.estimated_duration_seconds > 300;
      if (
        needsConfirmation &&
        !window.confirm(
          `This sweep contains ${created.estimated_run_count} trials and its estimated runtime is ${formatDuration(created.estimated_duration_seconds)}. Start it?`,
        )
      ) {
        setNotice("Sweep saved as a draft. Start it when you are ready.");
        return;
      }
      await startExperimentGroup(created.group.id, needsConfirmation);
      await runGroupToCompletion(created.group.id);
    } catch (createError) {
      setError(errorMessage(createError));
    } finally {
      setBusy(false);
    }
  };

  const resumeSweep = async (group: ExperimentGroup) => {
    if (group.status === "draft") {
      const needsConfirmation =
        group.estimated_duration_seconds === null || group.estimated_duration_seconds > 300;
      if (
        needsConfirmation &&
        !window.confirm(
          `This sweep contains ${group.trials.length} trials and its estimated runtime is ${formatDuration(group.estimated_duration_seconds)}. Start it?`,
        )
      ) {
        return;
      }
      try {
        await startExperimentGroup(group.id, needsConfirmation);
      } catch (startError) {
        setError(errorMessage(startError));
        return;
      }
    }
    await runGroupToCompletion(group.id);
  };

  const createComparisonReport = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await createReport({
        name: reportName.trim() || "LOCALFORGE report",
        filename: null,
        format: reportFormat,
        run_ids: selectedRuns,
        baseline_run_id: comparison?.baseline_run_id ?? selectedRuns[0] ?? null,
        experiment_group_id: activeComparisonGroupId,
        include_retained_content: includeRetainedContent && canIncludeContent,
      });
      setLatestReport(result.report);
      await refresh();
    } catch (exportError) {
      setError(errorMessage(exportError));
    } finally {
      setBusy(false);
    }
  };

  const importPlan = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setPlanImportBusy(true);
    setError(null);
    setNotice(null);
    try {
      const bundle = JSON.parse(await file.text());
      const imported = await importBenchmarkPlan(bundle);
      setNotice(
        imported.plan.status === "ready"
          ? `Imported plan “${imported.plan.name}” with the saved workload configuration.`
          : `Imported the workload, but its plan needs attention: ${imported.preflight.blockers.map((item) => item.message).join(" ")}`,
      );
      await refresh();
    } catch (importError) {
      setError(errorMessage(importError));
    } finally {
      setPlanImportBusy(false);
      event.target.value = "";
    }
  };

  if (loading) {
    return (
      <section className="experiments-screen" aria-live="polite">
        <p className="screen-loading">Loading runs and experiment plans…</p>
      </section>
    );
  }

  return (
    <div className="experiments-screen">
      <section className="experiments-intro panel">
        <div>
          <p className="eyebrow">MEASURE ONE CHANGE AT A TIME</p>
          <h2>Make comparisons you can explain.</h2>
          <p>
            LOCALFORGE checks the captured machine, model, runtime, workload, traffic pattern,
            measurement definitions, and contention before it assigns a grade. Missing measurements
            stay unavailable; they are never treated as zero.
          </p>
        </div>
        <div className="experiments-intro-stat">
          <strong>{selectableRuns.length}</strong>
          <span>runs ready to compare</span>
        </div>
      </section>

      {error && (
        <div className="experiments-alert" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="experiments-notice" role="status">
          {notice}
        </div>
      )}

      <section className="experiments-panel panel" aria-labelledby="compare-runs-title">
        <div className="experiments-panel-heading">
          <div>
            <p className="eyebrow">RUN ANALYSIS</p>
            <h2 id="compare-runs-title">Compare completed runs</h2>
          </div>
          <button
            className="button button--primary"
            disabled={selectedRuns.length < 2 || busy}
            onClick={() => void compareSelected()}
            type="button"
          >
            {comparisonLoading ? "Comparing…" : `Compare ${selectedRuns.length || "runs"}`}
          </button>
        </div>
        {selectableRuns.length === 0 ? (
          <p className="experiments-empty">
            Complete a benchmark run before building a comparison.
          </p>
        ) : (
          <div className="comparison-run-grid">
            {selectableRuns.map((run) => (
              <label
                className={`comparison-run-card ${selectedRuns.includes(run.id) ? "is-selected" : ""}`}
                key={run.id}
              >
                <input
                  checked={selectedRuns.includes(run.id)}
                  disabled={busy || (!selectedRuns.includes(run.id) && selectedRuns.length >= 20)}
                  onChange={() => toggleRun(run.id)}
                  type="checkbox"
                />
                <span>
                  <strong>{planNames.get(run.plan_id) ?? `Run ${shortRun(run.id)}`}</strong>
                  <small>
                    {[
                      run.profile_snapshot?.engine?.engine_display_name ??
                        run.profile_snapshot?.engine?.label,
                      `Run ${shortRun(run.id)}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                  <small>
                    {run.status} ·{" "}
                    {new Date(
                      run.completed_at ?? run.started_at ?? run.created_at,
                    ).toLocaleString()}
                  </small>
                </span>
              </label>
            ))}
          </div>
        )}

        {comparison && (
          <div className="comparison-result">
            <div className={`comparability-banner comparability-banner--${comparison.grade}`}>
              <div>
                <p className="eyebrow">
                  COMPARABILITY / {comparison.grade === "not_comparable" ? "N/A" : comparison.grade}
                </p>
                <h3>{comparison.label}</h3>
                <p>{comparison.rationale}</p>
              </div>
              <span
                className={
                  comparison.controlled_comparison_badge
                    ? "comparison-badge comparison-badge--controlled"
                    : "comparison-badge"
                }
              >
                {comparison.controlled_comparison_badge ? "Controlled" : "No causal claim"}
              </span>
            </div>
            <p className="comparison-caveat">
              Values show observed differences for these selected runs. LOCALFORGE does not claim a
              cause when the captured setup changed in more than one way.
            </p>
            <div className="comparison-dimensions">
              {comparison.dimensions.map((dimension) => (
                <details
                  className={`comparison-dimension comparison-dimension--${dimension.state}`}
                  key={dimension.key}
                >
                  <summary>
                    <span>{dimension.label}</span>
                    <strong>{dimension.state.replaceAll("_", " ")}</strong>
                  </summary>
                  <pre>{pretty(dimension.changes ?? dimension.values)}</pre>
                </details>
              ))}
            </div>
            <h3>Shared metrics</h3>
            {comparison.shared_metrics.length > 0 && (
              <>
                <RunComparisonCharts comparison={comparison} runLabel={runLabel} />
                <RunParetoChart comparison={comparison} runLabel={runLabel} />
              </>
            )}
            {comparison.shared_metrics.length === 0 ? (
              <p className="experiments-empty">
                No metrics have matching units, sources, formulas, and aggregation across every
                selected run.
              </p>
            ) : (
              <div className="comparison-table-wrap">
                <table className="comparison-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Definition and source</th>
                      {comparison.run_ids.map((id) => (
                        <th key={id}>
                          {runLabel(id)}
                          {runLabel(id) !== `Run ${shortRun(id)}` && (
                            <small className="table-subtext">Run {shortRun(id)}</small>
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {comparison.shared_metrics.map((metric) => (
                      <tr key={metricIdentity(metric)}>
                        <th scope="row">
                          {metric.metric_name}
                          <small>{metric.unit}</small>
                        </th>
                        <td>
                          {metric.definition}
                          <small>
                            {metric.source} · {metric.formula} · {pretty(metric.labels)}
                          </small>
                        </td>
                        {comparison.run_ids.map((id) => {
                          const result = metric.runs[id];
                          const delta = metric.observed_deltas[id];
                          return (
                            <td key={id}>
                              <strong>
                                {result?.value === null || result?.value === undefined
                                  ? "Unavailable"
                                  : `${result.value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${metric.unit}`}
                              </strong>
                              {result && (
                                <small>
                                  n={result.sample_count} · {result.record_count} records
                                </small>
                              )}
                              {id !== comparison.baseline_run_id && (
                                <small>
                                  Observed delta:{" "}
                                  {delta === null || delta === undefined
                                    ? "Unavailable"
                                    : `${delta.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${metric.unit}`}
                                </small>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {experimentSummary && (
              <section className="experiment-repeat-summary" aria-labelledby="repeat-summary-title">
                <div>
                  <p className="eyebrow">REPEATED TRIALS</p>
                  <h3 id="repeat-summary-title">Median and spread by variant</h3>
                  <p>
                    Only successful, non-missing trial values contribute. Failed repetitions remain
                    listed below.
                  </p>
                </div>
                <ExperimentVariantCharts summary={experimentSummary} />
                <div className="comparison-table-wrap">
                  <table className="comparison-table">
                    <thead>
                      <tr>
                        <th>Variant</th>
                        <th>Trials</th>
                        {comparison.shared_metrics.map((metric) => (
                          <th key={metricIdentity(metric)}>
                            {metric.metric_name}
                            <small>{metric.unit} · median [min–max]</small>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {experimentSummary.variants.map((variant) => (
                        <tr key={variant.name}>
                          <th scope="row">
                            {variant.name}
                            <small>{pretty(variant.value)}</small>
                          </th>
                          <td>
                            {variant.succeeded_count} passed · {variant.failed_count} failed
                          </td>
                          {comparison.shared_metrics.map((metric) => {
                            const summary = variant.metrics.find(
                              (item) => metricIdentity(item) === metricIdentity(metric),
                            );
                            return (
                              <td key={metricIdentity(metric)}>
                                <strong>
                                  {summary?.median === null || summary?.median === undefined
                                    ? "Unavailable"
                                    : summary.median.toLocaleString(undefined, {
                                        maximumFractionDigits: 2,
                                      })}
                                </strong>
                                <small>
                                  {summary?.spread_min === null || summary?.spread_min === undefined
                                    ? "No spread"
                                    : `${summary.spread_min.toLocaleString(undefined, { maximumFractionDigits: 2 })}–${summary.spread_max?.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${metric.unit}`}
                                </small>
                                <small>
                                  n={summary?.repeat_count ?? 0} repeats ·{" "}
                                  {summary?.sample_count ?? 0} samples
                                </small>
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {experimentSummary.failed_trials.length > 0 && (
                  <p className="experiments-empty">
                    {experimentSummary.failed_trials.length} failed trial(s) are preserved in the
                    experiment history and exported with the report.
                  </p>
                )}
              </section>
            )}
            {comparison.excluded_metrics.length > 0 && (
              <details className="excluded-metrics">
                <summary>{comparison.excluded_metrics.length} metric definitions excluded</summary>
                <ul>
                  {comparison.excluded_metrics.map((item, index) => (
                    <li key={`${item.metric_name}:${item.reason}:${index}`}>
                      <strong>{item.metric_name}</strong> — {item.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <div className="report-export-tools">
              <div>
                <p className="eyebrow">REPRODUCIBLE REPORT</p>
                <h3>Save this comparison locally</h3>
              </div>
              <label>
                Report name
                <input
                  maxLength={160}
                  onChange={(event) => setReportName(event.target.value)}
                  value={reportName}
                />
              </label>
              <label>
                Format
                <select
                  onChange={(event) => setReportFormat(event.target.value as typeof reportFormat)}
                  value={reportFormat}
                >
                  <option value="markdown">Markdown</option>
                  <option value="json">JSON</option>
                  <option value="csv">CSV</option>
                </select>
              </label>
              <label className="report-content-choice">
                <input
                  checked={includeRetainedContent && canIncludeContent}
                  disabled={!canIncludeContent || busy}
                  onChange={(event) => setIncludeRetainedContent(event.target.checked)}
                  type="checkbox"
                />
                Include prompt/output text retained by every selected plan
              </label>
              <div className="report-export-actions">
                <button
                  className="button button--primary"
                  disabled={busy}
                  onClick={() => void createComparisonReport()}
                  type="button"
                >
                  {busy ? "Saving…" : "Create report"}
                </button>
                {latestReport && (
                  <a
                    className="button button--secondary"
                    href={`/api/v1/reports/${latestReport.id}/download`}
                  >
                    Download {latestReport.format.toUpperCase()}
                  </a>
                )}
              </div>
              <small>
                Credentials are always omitted. Export files stay in the local reports folder; no
                upload occurs.
              </small>
            </div>
          </div>
        )}
      </section>

      <section className="experiments-panel panel" aria-labelledby="sweep-title">
        <div className="experiments-panel-heading">
          <div>
            <p className="eyebrow">CONTROLLED SWEEP</p>
            <h2 id="sweep-title">Change one workload setting</h2>
          </div>
          <span className="experiment-limit">Maximum {20} trials · sequential execution</span>
        </div>
        {readyPlans.length === 0 ? (
          <p className="experiments-empty">
            Create and preflight a benchmark plan in Workloads before setting up a sweep.
          </p>
        ) : (
          <form className="experiment-builder" onSubmit={(event) => void submitSweep(event)}>
            <label>
              Experiment name
              <input
                maxLength={160}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                required
                value={draft.name}
              />
            </label>
            <label>
              Baseline plan
              <select
                onChange={(event) => setDraft({ ...draft, baselinePlanId: event.target.value })}
                required
                value={draft.baselinePlanId}
              >
                <option value="">Choose a ready plan</option>
                {readyPlans.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Independent variable
              <select
                onChange={(event) =>
                  setDraft({ ...draft, independentVariable: event.target.value })
                }
                value={draft.independentVariable}
              >
                {sweepVariables.map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Variant values
              <textarea
                aria-describedby="variant-values-help"
                onChange={(event) => setDraft({ ...draft, variantValues: event.target.value })}
                required
                rows={3}
                value={draft.variantValues}
              />
              <small id="variant-values-help">
                Enter one numeric value per line. The saved plan is the baseline.
              </small>
            </label>
            <label>
              Repetitions per setting
              <input
                max={10}
                min={1}
                onChange={(event) => setDraft({ ...draft, repetitions: event.target.value })}
                type="number"
                value={draft.repetitions}
              />
            </label>
            <label>
              Order seed
              <input
                max={Number.MAX_SAFE_INTEGER}
                min={0}
                onChange={(event) => setDraft({ ...draft, orderSeed: event.target.value })}
                type="number"
                value={draft.orderSeed}
              />
            </label>
            <label className="experiment-check-option">
              <input
                checked={draft.randomizeOrder}
                onChange={(event) => setDraft({ ...draft, randomizeOrder: event.target.checked })}
                type="checkbox"
              />
              Randomize trial order using this seed
            </label>
            <label className="hypothesis-field">
              Hypothesis or notes
              <textarea
                maxLength={2000}
                onChange={(event) => setDraft({ ...draft, hypothesis: event.target.value })}
                rows={2}
                value={draft.hypothesis}
              />
            </label>
            <div className="experiment-submit-row">
              <button
                className="button button--primary"
                disabled={busy || planImportBusy}
                type="submit"
              >
                {busy ? "Preparing sweep…" : "Create and run sweep"}
              </button>
              <p>
                Each variant is preflighted first. Runs are queued one at a time; failed trials
                remain recorded.
              </p>
            </div>
          </form>
        )}
        {activeTrialRun && (
          <div className="experiment-progress" role="status">
            <strong>
              Run {shortRun(activeTrialRun.id)} · {activeTrialRun.status}
            </strong>
            <span>The next variant starts after this run reaches a terminal state.</span>
          </div>
        )}
      </section>

      <section className="experiments-panel panel" aria-labelledby="history-title">
        <div className="experiments-panel-heading">
          <div>
            <p className="eyebrow">LOCAL HISTORY</p>
            <h2 id="history-title">Experiment groups</h2>
          </div>
          <button
            className="button button--secondary"
            disabled={planImportBusy}
            onClick={() => document.getElementById("plan-import-file")?.click()}
            type="button"
          >
            {planImportBusy ? "Importing…" : "Import a plan"}
          </button>
          <input
            accept="application/json,.json"
            className="visually-hidden"
            id="plan-import-file"
            onChange={(event) => void importPlan(event)}
            type="file"
          />
        </div>
        {groups.length === 0 ? (
          <p className="experiments-empty">
            Your experiment groups will be stored here with their randomized order, seed, plans,
            runs, and failed trials.
          </p>
        ) : (
          <div className="experiment-group-list">
            {groups.map((group) => (
              <article className="experiment-group-card" key={group.id}>
                <div className="experiment-group-card-heading">
                  <div>
                    <h3>{group.name}</h3>
                    <p>
                      {group.independent_variable} · {group.repetitions} repetition(s) ·{" "}
                      {group.trials.length} trial(s) ·{" "}
                      {formatDuration(group.estimated_duration_seconds)}
                    </p>
                  </div>
                  <span className={`status-pill status-pill--${group.status}`}>{group.status}</span>
                </div>
                {group.description && <p className="experiment-hypothesis">{group.description}</p>}
                <div className="experiment-trial-list">
                  {group.trials.map((trial) => (
                    <div className="experiment-trial-row" key={trial.order}>
                      <span className="experiment-trial-order">
                        {String(trial.order + 1).padStart(2, "0")}
                      </span>
                      <strong>{trial.variant_name}</strong>
                      <span>
                        {pretty(trial.variant_value)} · repeat {trial.repetition}
                      </span>
                      <span className={`status-pill status-pill--${trial.status}`}>
                        {trial.status}
                      </span>
                      {trial.run_id && (
                        <a href={`#runs?run=${encodeURIComponent(trial.run_id)}`}>
                          Run {shortRun(trial.run_id)}
                        </a>
                      )}
                      {trial.error && <small role="status">{trial.error}</small>}
                    </div>
                  ))}
                </div>
                {group.run_ids.length >= 2 && (
                  <button
                    className="button button--secondary"
                    disabled={busy}
                    onClick={() => void compareGroup(group)}
                    type="button"
                  >
                    Compare group
                  </button>
                )}
                {(group.status === "draft" || group.status === "running") && (
                  <button
                    className="button button--secondary"
                    disabled={busy}
                    onClick={() => void resumeSweep(group)}
                    type="button"
                  >
                    {group.status === "draft"
                      ? "Start sweep"
                      : activeGroupId === group.id
                        ? "Sweep running…"
                        : "Continue sweep"}
                  </button>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="experiments-panel panel" aria-labelledby="plan-library-title">
        <div className="experiments-panel-heading">
          <div>
            <p className="eyebrow">REPEATABLE CONFIGURATION</p>
            <h2 id="plan-library-title">Benchmark plans</h2>
          </div>
          <label className="plan-import-label">
            Import JSON
            <input
              accept="application/json,.json"
              disabled={planImportBusy}
              onChange={(event) => void importPlan(event)}
              type="file"
            />
          </label>
        </div>
        <div className="plan-export-list">
          {visiblePlans.map((plan) => (
            <div className="plan-export-row" key={plan.id}>
              <div>
                <strong>{plan.name}</strong>
                <small>
                  {plan.status} · {plan.measured_requests ?? "duration-based"} measured requests ·{" "}
                  {plan.concurrency} concurrency
                </small>
              </div>
              <a className="text-link" href={`/api/v1/benchmark-plans/${plan.id}/export`}>
                Export plan JSON
              </a>
            </div>
          ))}
        </div>
        {hiddenPlanCount > 0 && (
          <p className="experiments-empty" role="note">
            {hiddenPlanCount} plan(s) and their runs use test-fixture engines and are hidden. Turn
            on test fixture engines in <a href="#settings">Settings</a> to show them.
          </p>
        )}
        <p className="privacy-footnote">
          Plan files contain workload settings and local record IDs, never endpoint credentials or
          dataset text. Dataset-based plans import only when the same dataset snapshot is present.
        </p>
      </section>
    </div>
  );
}
