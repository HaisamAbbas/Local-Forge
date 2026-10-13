import { useMemo, useState } from "react";

import type { MetricSample, RunEventRecord } from "../lib/api";
import {
  STAT_COLUMNS,
  describeFlag,
  engineSummary,
  engineValueLabel,
  filterRecords,
  findRunMetric,
  formatMeasurement,
  loadLevels,
  outcomeRecords,
  sourceCounts,
  telemetryLabel,
  telemetrySummaries,
  throughputRecords,
  timingMatrix,
  unavailableReason,
  type RecordFilter,
} from "../lib/run-measurements";
import { RunTelemetryCharts } from "./RunCharts";

const SOURCE_LABELS: Record<RecordFilter, string> = {
  all: "All sources",
  client_measured: "Client measured",
  derived: "Derived",
  engine_native: "Engine native",
  system_observed: "System observed",
  user_supplied: "User supplied",
};

export type KeyResultCounts = {
  attempted: number;
  succeeded: number;
  failed: number;
  timedOut: number;
  cancelled: number;
  incomplete: number;
  warmup: number;
};

type LiveValue = { value: number | null; sampleCount: number };

/** Headline figures for a run: the handful of numbers a reader looks for first. */
export function RunKeyResults({
  metrics,
  counts,
  elapsed,
  liveLatency,
  liveThroughput,
}: {
  metrics: MetricSample[];
  counts: KeyResultCounts;
  elapsed: string;
  liveLatency: LiveValue;
  liveThroughput: LiveValue;
}) {
  const ttftP50 = findRunMetric(metrics, "ttft_ms", "p50");
  const ttftP95 = findRunMetric(metrics, "ttft_ms", "p95");
  const latencyP50 = findRunMetric(metrics, "request_latency_ms", "p50");
  const latencyP95 = findRunMetric(metrics, "request_latency_ms", "p95");
  const generation = findRunMetric(metrics, "generation_throughput_tokens_per_second", "run_rate");
  const output = findRunMetric(metrics, "output_throughput_tokens_per_second", "run_rate");
  const requestRate = findRunMetric(metrics, "request_throughput_requests_per_second", "run_rate");

  return (
    <div className="key-results" aria-label="Key results">
      <KeyResult
        label="Time to first token"
        qualifier="p50"
        value={metricText(ttftP50, "ms")}
        detail={ttftP50 ? `p95 ${metricText(ttftP95, "ms")}` : "Written when the run finalizes"}
      />
      <KeyResult
        label="Request latency"
        qualifier={latencyP50 ? "p50" : "live mean"}
        value={
          latencyP50 ? metricText(latencyP50, "ms") : formatMeasurement(liveLatency.value, "ms")
        }
        detail={
          latencyP50
            ? `p95 ${metricText(latencyP95, "ms")}`
            : `${liveLatency.sampleCount} requests observed so far`
        }
      />
      <KeyResult
        label="Generation speed"
        qualifier="tokens/s"
        value={metricText(generation)}
        detail={output ? `Output ${metricText(output, "tokens/s")} incl. first token wait` : "—"}
      />
      <KeyResult
        label="Request rate"
        qualifier="requests/s"
        value={requestRate ? metricText(requestRate) : formatMeasurement(liveThroughput.value)}
        detail={
          requestRate ? `Over ${requestRate.sample_count} measured requests` : "Live estimate"
        }
      />
      <KeyResult
        label="Succeeded"
        qualifier="measured"
        value={`${counts.succeeded.toLocaleString()} / ${counts.attempted.toLocaleString()}`}
        detail={`${counts.failed} failed · ${counts.timedOut} timed out · ${counts.cancelled} cancelled`}
        tone={counts.failed + counts.timedOut > 0 ? "warning" : undefined}
      />
      <KeyResult
        label="Elapsed"
        value={elapsed}
        detail={`${counts.warmup} warmup excluded${counts.incomplete ? ` · ${counts.incomplete} in progress` : ""}`}
      />
    </div>
  );
}

function KeyResult({
  label,
  qualifier,
  value,
  detail,
  tone,
}: {
  label: string;
  qualifier?: string;
  value: string;
  detail: string;
  tone?: "warning";
}) {
  return (
    <div className={`key-result${tone ? ` key-result--${tone}` : ""}`}>
      <span className="key-result__label">
        {label}
        {qualifier && <span className="key-result__qualifier"> · {qualifier}</span>}
      </span>
      <strong className="key-result__value">{value}</strong>
      <small className="key-result__detail">{detail}</small>
    </div>
  );
}

function metricText(metric: MetricSample | null, unit?: string): string {
  if (!metric) return "—";
  if (unavailableReason(metric) !== null) return "Unavailable";
  return formatMeasurement(metric.value, unit ?? metric.unit);
}

/** The run's measurements, condensed into readable summaries with full records on demand. */
export function RunMeasurements({
  metrics,
  runStartedAt,
  catchingUp,
  telemetryEvent,
}: {
  metrics: MetricSample[];
  runStartedAt: string;
  catchingUp: boolean;
  telemetryEvent: RunEventRecord | undefined;
}) {
  const levels = useMemo(() => loadLevels(metrics), [metrics]);
  const [chosenLevel, setChosenLevel] = useState<string | null>(null);
  const level =
    chosenLevel !== null && levels.includes(chosenLevel) ? chosenLevel : (levels[0] ?? "");

  if (metrics.length === 0)
    return (
      <p className="empty-inline">
        {catchingUp
          ? "Restoring persisted metric history."
          : "No measurements yet. Summaries are written when the run finalizes; the request charts above update live."}
      </p>
    );

  return (
    <div className="measurements">
      {levels.length > 1 && (
        <div className="measure-levels" role="group" aria-label="Load level">
          <span>Concurrency level</span>
          {levels.map((item) => (
            <button
              aria-pressed={item === level}
              className={`measure-level${item === level ? " measure-level--active" : ""}`}
              key={item}
              onClick={() => setChosenLevel(item)}
              type="button"
            >
              {item || "All"}
            </button>
          ))}
        </div>
      )}
      <TimingTable metrics={metrics} level={level} />
      <div className="measure-pair">
        <RecordList
          title="Throughput"
          records={throughputRecords(metrics, level)}
          emptyText="No throughput summaries were recorded."
        />
        <RecordList
          title="Request outcomes"
          records={outcomeRecords(metrics, level)}
          emptyText="No outcome totals were recorded."
          integers
        />
      </div>
      <EngineTimings metrics={metrics} />
      <ResourceUsage
        metrics={metrics}
        runStartedAt={runStartedAt}
        catchingUp={catchingUp}
        telemetryEvent={telemetryEvent}
      />
      <MetricRecordExplorer metrics={metrics} />
    </div>
  );
}

function TimingTable({ metrics, level }: { metrics: MetricSample[]; level: string }) {
  const rows = timingMatrix(metrics, level);
  if (rows.length === 0) return null;
  const reasons = new Set<string>();
  for (const row of rows)
    for (const column of STAT_COLUMNS) {
      const cell = row.cells[column];
      if (cell && unavailableReason(cell) !== null)
        for (const flag of cell.quality_flags) reasons.add(describeFlag(flag));
    }
  return (
    <section className="measure-block" aria-labelledby="measure-timing-title">
      <h4 id="measure-timing-title">Latency and timing</h4>
      <div className="table-scroll">
        <table className="measure-table">
          <caption className="visually-hidden">
            Latency and timing summaries: mean and percentiles per measurement
          </caption>
          <thead>
            <tr>
              <th scope="col">Measurement</th>
              {STAT_COLUMNS.map((column) => (
                <th className="measure-num" key={column} scope="col">
                  {column}
                </th>
              ))}
              <th className="measure-num" scope="col" title="Observations behind each value">
                Samples
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <th scope="row" title={row.definition}>
                  {row.label}
                  <small className="measure-unit">
                    {row.unit} · {row.source.replace("_", " ")}
                  </small>
                </th>
                {STAT_COLUMNS.map((column) => (
                  <td className="measure-num" key={column}>
                    <MeasureCell metric={row.cells[column]} />
                  </td>
                ))}
                <td className="measure-num measure-muted">{row.sampleCount.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {reasons.size > 0 && (
        <p className="measure-note">
          — means unavailable, never zero. Hover a dash for its reason: {[...reasons].join("; ")}.
        </p>
      )}
    </section>
  );
}

function MeasureCell({ metric }: { metric: MetricSample | null }) {
  if (!metric)
    return (
      <span className="measure-na" title="Not recorded for this run">
        —<span className="visually-hidden">Not recorded</span>
      </span>
    );
  const reason = unavailableReason(metric);
  if (reason)
    return (
      <span className="measure-na" title={reason}>
        —<span className="visually-hidden">Unavailable: {reason}</span>
      </span>
    );
  return <>{formatMeasurement(metric.value)}</>;
}

function RecordList({
  title,
  records,
  emptyText,
  integers = false,
}: {
  title: string;
  records: ReturnType<typeof throughputRecords>;
  emptyText: string;
  integers?: boolean;
}) {
  return (
    <section className="measure-block measure-card" aria-label={title}>
      <h4>{title}</h4>
      {records.length === 0 ? (
        <p className="empty-inline">{emptyText}</p>
      ) : (
        <dl className="measure-list">
          {records.map(({ name, label, metric }) => {
            const reason = unavailableReason(metric);
            return (
              <div key={name} title={metric.definition}>
                <dt>{label}</dt>
                <dd>
                  {reason ? (
                    <span className="measure-na" title={reason}>
                      Unavailable
                    </span>
                  ) : integers ? (
                    (metric.value as number).toLocaleString()
                  ) : (
                    formatMeasurement(metric.value, metric.unit)
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </section>
  );
}

function EngineTimings({ metrics }: { metrics: MetricSample[] }) {
  const summary = useMemo(() => engineSummary(metrics), [metrics]);
  const hasValues = summary.perRequest.length > 0 || summary.runLevel.length > 0;
  if (!hasValues && summary.unavailableCount === 0) return null;
  return (
    <section className="measure-block" aria-labelledby="measure-engine-title">
      <h4 id="measure-engine-title">Engine-reported values</h4>
      {summary.perRequest.length > 0 && (
        <div className="table-scroll">
          <table className="measure-table">
            <caption className="visually-hidden">
              Engine-reported values per measured request, summarised
            </caption>
            <thead>
              <tr>
                <th scope="col">Value (per request)</th>
                <th className="measure-num" scope="col">
                  Mean
                </th>
                <th className="measure-num" scope="col">
                  p50
                </th>
                <th className="measure-num" scope="col">
                  Min
                </th>
                <th className="measure-num" scope="col">
                  Max
                </th>
                <th className="measure-num" scope="col">
                  Requests
                </th>
              </tr>
            </thead>
            <tbody>
              {summary.perRequest.map((item) => (
                <tr key={item.name}>
                  <th scope="row" title={item.definition}>
                    {engineValueLabel(item.name)}
                    <small className="measure-unit">
                      {item.unit}
                      {engineValueLabel(item.name) !== item.name ? ` · ${item.name}` : ""}
                    </small>
                  </th>
                  <td className="measure-num">{formatMeasurement(item.stats.mean)}</td>
                  <td className="measure-num">{formatMeasurement(item.stats.p50)}</td>
                  <td className="measure-num">{formatMeasurement(item.stats.min)}</td>
                  <td className="measure-num">{formatMeasurement(item.stats.max)}</td>
                  <td className="measure-num measure-muted">{item.stats.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {summary.runLevel.length > 0 && (
        <dl className="measure-list measure-list--inline">
          {summary.runLevel.map((metric) => (
            <div key={metric.id} title={`${metric.definition} · ${metric.formula}`}>
              <dt>
                {metric.metric_name} <span className="measure-muted">{metric.aggregation}</span>
              </dt>
              <dd>{formatMeasurement(metric.value, metric.unit)}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="measure-note">
        {summary.perRequest.length > 0
          ? "Summarised from the engine's own per-request reports over measured requests (warmup excluded); ns values are shown in ms. "
          : ""}
        {summary.unavailableCount > 0 &&
          `${summary.unavailableCount} further engine record${summary.unavailableCount === 1 ? "" : "s"} had no value: ${summary.unavailableReasons.join("; ").toLowerCase()}.`}
      </p>
    </section>
  );
}

function ResourceUsage({
  metrics,
  runStartedAt,
  catchingUp,
  telemetryEvent,
}: {
  metrics: MetricSample[];
  runStartedAt: string;
  catchingUp: boolean;
  telemetryEvent: RunEventRecord | undefined;
}) {
  const summaries = useMemo(() => telemetrySummaries(metrics), [metrics]);
  return (
    <section className="measure-block" aria-labelledby="measure-resources-title">
      <h4 id="measure-resources-title">Resource usage during the run</h4>
      {summaries.length === 0 ? (
        <p className="empty-inline">
          {telemetryEvent && telemetryEvent.payload.available !== true
            ? `Resource telemetry unavailable: ${String(telemetryEvent.payload.reason ?? "the host sampler did not report readings")}. This is missing data, not zero usage.`
            : catchingUp
              ? "Restoring resource telemetry."
              : "No resource telemetry was recorded for this run."}
        </p>
      ) : (
        <>
          <div className="table-scroll">
            <table className="measure-table">
              <caption className="visually-hidden">
                Resource usage summary per sampled series
              </caption>
              <thead>
                <tr>
                  <th scope="col">Series</th>
                  <th className="measure-num" scope="col">
                    Mean
                  </th>
                  <th className="measure-num" scope="col">
                    Min
                  </th>
                  <th className="measure-num" scope="col">
                    Max
                  </th>
                  <th className="measure-num" scope="col">
                    Last
                  </th>
                  <th className="measure-num" scope="col">
                    Samples
                  </th>
                </tr>
              </thead>
              <tbody>
                {summaries.map((item) => (
                  <tr key={item.id}>
                    <th scope="row" title={item.definition}>
                      {telemetryLabel(item.name)}
                      <small className="measure-unit">{item.unit}</small>
                    </th>
                    <td className="measure-num">{formatMeasurement(item.stats.mean)}</td>
                    <td className="measure-num">{formatMeasurement(item.stats.min)}</td>
                    <td className="measure-num">{formatMeasurement(item.stats.max)}</td>
                    <td className="measure-num">{formatMeasurement(item.last)}</td>
                    <td className="measure-num measure-muted">{item.stats.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="measure-details">
            <summary>Resource usage over time ({summaries.length} charts)</summary>
            <RunTelemetryCharts metrics={metrics} runStartedAt={runStartedAt} />
          </details>
        </>
      )}
    </section>
  );
}

const EXPLORER_PAGE = 50;

/** Every persisted record with full provenance, filterable and paged. */
function MetricRecordExplorer({ metrics }: { metrics: MetricSample[] }) {
  const [source, setSource] = useState<RecordFilter>("all");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(EXPLORER_PAGE);
  const counts = useMemo(() => sourceCounts(metrics), [metrics]);
  const filtered = useMemo(() => filterRecords(metrics, source, query), [metrics, source, query]);
  const visible = filtered.slice(0, limit);

  return (
    <details className="measure-details metric-explorer">
      <summary>
        All metric records ({metrics.length.toLocaleString()}) · source, formula, sample count and
        quality flags
      </summary>
      <div className="metric-explorer__controls">
        <label>
          <span>Source</span>
          <select
            value={source}
            onChange={(event) => {
              setSource(event.target.value as RecordFilter);
              setLimit(EXPLORER_PAGE);
            }}
          >
            {counts.map((item) => (
              <option key={item.source} value={item.source}>
                {SOURCE_LABELS[item.source]} ({item.count.toLocaleString()})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Metric name</span>
          <input
            type="search"
            value={query}
            placeholder="Filter, e.g. ttft"
            onChange={(event) => {
              setQuery(event.target.value);
              setLimit(EXPLORER_PAGE);
            }}
          />
        </label>
        <span className="measure-muted" role="status">
          {filtered.length.toLocaleString()} matching
        </span>
      </div>
      {filtered.length === 0 ? (
        <p className="empty-inline">No records match this filter.</p>
      ) : (
        <div className="table-scroll">
          <table className="measure-table measure-table--records">
            <caption className="visually-hidden">Persisted metric records</caption>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                <th className="measure-num" scope="col">
                  Value
                </th>
                <th scope="col">Unit</th>
                <th scope="col">Source</th>
                <th className="measure-num" scope="col">
                  n
                </th>
                <th scope="col">Definition and quality</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((metric) => (
                <tr key={metric.id}>
                  <th scope="row">
                    {metric.metric_name}
                    <small className="measure-unit">
                      {[metric.aggregation, ...labelText(metric.labels)].join(" · ")}
                    </small>
                  </th>
                  <td className="measure-num">
                    {metric.value === null ? "Unavailable" : formatMeasurement(metric.value)}
                  </td>
                  <td>{metric.unit}</td>
                  <td>{metric.source}</td>
                  <td className="measure-num">{metric.sample_count.toLocaleString()}</td>
                  <td>
                    <span>{metric.definition}</span>
                    <small className="measure-unit">
                      {metric.formula} · {metric.method_version}
                      {metric.quality_flags.length
                        ? ` · ${metric.quality_flags.map(describeFlag).join("; ")}`
                        : ""}
                    </small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {filtered.length > visible.length && (
        <button
          className="button button--quiet"
          onClick={() => setLimit(limit + EXPLORER_PAGE * 4)}
          type="button"
        >
          Show more ({(filtered.length - visible.length).toLocaleString()} remaining)
        </button>
      )}
    </details>
  );
}

function labelText(labels: Record<string, string>): string[] {
  return Object.entries(labels)
    .filter(([key]) => key !== "concurrency_level" && key !== "effective_concurrency")
    .map(([key, value]) =>
      key === "request_id"
        ? `request ${value.slice(0, 8)}`
        : `${key.replaceAll("_", " ")} ${value}`,
    );
}
