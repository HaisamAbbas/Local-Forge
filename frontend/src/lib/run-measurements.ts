import type { MetricSample } from "./api";
import { displayUnit, quantileR7 } from "./chart-data";

/**
 * Pure helpers that condense a run's persisted metric records into the summaries shown in
 * the run detail "Measurements" section. Nothing here invents values: every number comes from
 * a persisted record or is a plain statistic over persisted per-request records.
 */

export const STAT_COLUMNS = ["mean", "p50", "p90", "p95", "p99"] as const;
export type StatColumn = (typeof STAT_COLUMNS)[number];

export const TIMING_ROWS: ReadonlyArray<{ name: string; label: string }> = [
  { name: "ttft_ms", label: "Time to first token" },
  { name: "request_latency_ms", label: "Request latency" },
  { name: "tpot_ms_per_token", label: "Time per output token" },
  { name: "inter_token_latency_ms", label: "Inter-token latency" },
  { name: "inter_event_latency_ms", label: "Gap between output events" },
  { name: "queue_time_ms", label: "Queue time" },
];

export const THROUGHPUT_ROWS: ReadonlyArray<{ name: string; label: string }> = [
  { name: "generation_throughput_tokens_per_second", label: "Generation" },
  { name: "ollama.eval_rate_tokens_per_second", label: "Generation · engine-timed" },
  { name: "output_throughput_tokens_per_second", label: "Output" },
  { name: "input_throughput_tokens_per_second", label: "Input" },
  { name: "prompt_processing_throughput_tokens_per_second", label: "Prompt processing" },
  { name: "ollama.prompt_eval_rate_tokens_per_second", label: "Prompt processing · engine-timed" },
  { name: "request_throughput_requests_per_second", label: "Requests" },
  { name: "error_rate_percent", label: "Error rate" },
];

export const OUTCOME_ROWS: ReadonlyArray<{ name: string; label: string }> = [
  { name: "requests_attempted", label: "Attempted" },
  { name: "requests_succeeded", label: "Succeeded" },
  { name: "requests_failed", label: "Failed" },
  { name: "requests_timed_out", label: "Timed out" },
  { name: "requests_cancelled", label: "Cancelled" },
  { name: "requests_disconnected", label: "Disconnected" },
  { name: "requests_interrupted", label: "Interrupted" },
];

const FLAG_TEXT: Record<string, string> = {
  insufficient_samples_for_stable_tail: "Too few samples for this percentile",
  unavailable_token_timestamps_not_provided: "The engine does not report per-token timing",
  unavailable_no_samples: "No samples were recorded",
  unavailable_prefill_interval: "The engine does not report a prefill interval",
  missing_native_metric: "The engine did not report this value",
  missing_before_snapshot: "The engine has no server-wide counters to snapshot",
  missing_after_snapshot: "The engine has no server-wide counters to snapshot",
  missing_both_snapshot: "The engine has no server-wide counters to snapshot",
};

const ENGINE_VALUE_LABELS: Record<string, string> = {
  "ollama.load_duration": "Model load",
  "ollama.prompt_eval_duration": "Prompt evaluation",
  "ollama.eval_duration": "Generation (eval)",
  "ollama.total_duration": "Total server time",
  "ollama.prompt_eval_count": "Prompt tokens",
  "ollama.eval_count": "Generated tokens",
};

/** Readable label for an engine-native metric; unknown names are shown as reported. */
export function engineValueLabel(name: string): string {
  return ENGINE_VALUE_LABELS[name] ?? name;
}

/** Plain-language reason for a quality flag; unknown flags fall back to their words. */
export function describeFlag(flag: string): string {
  return FLAG_TEXT[flag] ?? flag.replace(/^unavailable_/, "").replaceAll("_", " ");
}

/** Reason text for an unavailable record, or null when it has a value. */
export function unavailableReason(metric: MetricSample): string | null {
  if (typeof metric.value === "number" && Number.isFinite(metric.value)) return null;
  return metric.quality_flags.length
    ? metric.quality_flags.map(describeFlag).join("; ")
    : "Not reported";
}

function isRunLevel(metric: MetricSample): boolean {
  return metric.labels.request_id === undefined && metric.labels.snapshot === undefined;
}

/** Load level a run-level record belongs to; sweeps record one set of summaries per level. */
export function loadLevelKey(metric: MetricSample): string {
  return metric.labels.concurrency_level ?? "";
}

/** Distinct load levels among run-level summary records, in numeric order. */
export function loadLevels(metrics: MetricSample[]): string[] {
  const levels = new Set(
    metrics
      .filter((metric) => metric.source !== "system_observed" && isRunLevel(metric))
      .filter((metric) => metric.source !== "engine_native")
      .map(loadLevelKey),
  );
  return [...levels].sort((left, right) => Number(left) - Number(right));
}

function runLevelIndex(metrics: MetricSample[], level: string): Map<string, MetricSample> {
  const index = new Map<string, MetricSample>();
  for (const metric of metrics) {
    if (metric.source === "system_observed" || metric.source === "engine_native") continue;
    if (!isRunLevel(metric)) continue;
    const metricLevel = loadLevelKey(metric);
    if (metricLevel !== level && metricLevel !== "") continue;
    const key = `${metric.metric_name}|${metric.aggregation}`;
    // A level-specific record wins over an unlabelled one.
    if (!index.has(key) || metricLevel === level) index.set(key, metric);
  }
  return index;
}

export type TimingRow = {
  name: string;
  label: string;
  unit: string;
  sampleCount: number;
  definition: string;
  source: MetricSample["source"];
  cells: Record<StatColumn, MetricSample | null>;
};

/** Latency/timing records as one row per metric with mean and percentile columns. */
export function timingMatrix(metrics: MetricSample[], level = ""): TimingRow[] {
  const index = runLevelIndex(metrics, level);
  return TIMING_ROWS.flatMap(({ name, label }) => {
    const cells = Object.fromEntries(
      STAT_COLUMNS.map((column) => [column, index.get(`${name}|${column}`) ?? null]),
    ) as Record<StatColumn, MetricSample | null>;
    const present = STAT_COLUMNS.map((column) => cells[column]).filter(
      (cell): cell is MetricSample => cell !== null,
    );
    const first = present[0];
    if (!first) return [];
    return [
      {
        name,
        label,
        unit: first.unit,
        sampleCount: Math.max(...present.map((cell) => cell.sample_count)),
        definition: first.definition,
        source: first.source,
        cells,
      },
    ];
  });
}

export type NamedRecord = { name: string; label: string; metric: MetricSample };

function namedRecords(
  metrics: MetricSample[],
  rows: ReadonlyArray<{ name: string; label: string }>,
  aggregation: string,
  level: string,
): NamedRecord[] {
  const index = runLevelIndex(metrics, level);
  return rows.flatMap(({ name, label }) => {
    const metric = index.get(`${name}|${aggregation}`);
    return metric ? [{ name, label, metric }] : [];
  });
}

export function throughputRecords(metrics: MetricSample[], level = ""): NamedRecord[] {
  return namedRecords(metrics, THROUGHPUT_ROWS, "run_rate", level);
}

export function outcomeRecords(metrics: MetricSample[], level = ""): NamedRecord[] {
  return namedRecords(metrics, OUTCOME_ROWS, "run_total", level);
}

/**
 * A single run-level record by name and aggregation, for headline figures. Without a level,
 * the lowest recorded load level is used.
 */
export function findRunMetric(
  metrics: MetricSample[],
  name: string,
  aggregation: string,
  level?: string,
): MetricSample | null {
  const resolved = level ?? loadLevels(metrics)[0] ?? "";
  return runLevelIndex(metrics, resolved).get(`${name}|${aggregation}`) ?? null;
}

export type DistributionSummary = {
  count: number;
  mean: number;
  p50: number;
  min: number;
  max: number;
};

export function summarize(values: number[]): DistributionSummary | null {
  if (values.length === 0) return null;
  const total = values.reduce((sum, value) => sum + value, 0);
  return {
    count: values.length,
    mean: total / values.length,
    p50: quantileR7(values, 0.5) as number,
    min: Math.min(...values),
    max: Math.max(...values),
  };
}

export type EngineTimingSummary = {
  name: string;
  unit: string;
  definition: string;
  stats: DistributionSummary;
};

export type EngineSummary = {
  /** Per-request engine-reported values, summarised over measurement requests. */
  perRequest: EngineTimingSummary[];
  /** Engine-native run-level records (aggregates, deltas) that carry a value. */
  runLevel: MetricSample[];
  /** Engine-native records with no value (for example server snapshots the engine lacks). */
  unavailableCount: number;
  unavailableReasons: string[];
};

const NANOSECONDS_PER_MILLISECOND = 1_000_000;

/** Condense engine-native records: per-request values become a distribution per metric. */
export function engineSummary(metrics: MetricSample[]): EngineSummary {
  const native = metrics.filter((metric) => metric.source === "engine_native");
  const groups = new Map<string, MetricSample[]>();
  const runLevel: MetricSample[] = [];
  const reasons = new Set<string>();
  let unavailableCount = 0;
  for (const metric of native) {
    if (unavailableReason(metric) !== null) {
      unavailableCount += 1;
      for (const flag of metric.quality_flags) reasons.add(describeFlag(flag));
      if (metric.quality_flags.length === 0) reasons.add("Not reported");
      continue;
    }
    if (metric.labels.request_id !== undefined) {
      if (metric.labels.phase === "warmup") continue;
      const group = groups.get(metric.metric_name) ?? [];
      group.push(metric);
      groups.set(metric.metric_name, group);
    } else {
      runLevel.push(metric);
    }
  }
  const perRequest = [...groups.entries()]
    .map(([name, group]) => {
      const first = group[0] as MetricSample;
      const toMilliseconds = first.unit === "ns";
      const values = group.map((metric) =>
        toMilliseconds
          ? (metric.value as number) / NANOSECONDS_PER_MILLISECOND
          : (metric.value as number),
      );
      return {
        name,
        unit: toMilliseconds ? "ms" : first.unit,
        definition: first.definition,
        stats: summarize(values) as DistributionSummary,
      };
    })
    .sort((left, right) => left.name.localeCompare(right.name));
  // Run-level mean/percentile summaries of values already tabulated per request repeat that table.
  const tabulated = new Set(perRequest.map((item) => item.name));
  const statAggregations = new Set<string>(STAT_COLUMNS);
  return {
    perRequest,
    runLevel: runLevel
      .filter(
        (metric) =>
          !(tabulated.has(metric.metric_name) && statAggregations.has(metric.aggregation)),
      )
      .sort((left, right) => left.metric_name.localeCompare(right.metric_name)),
    unavailableCount,
    unavailableReasons: [...reasons],
  };
}

export type TelemetrySummary = {
  id: string;
  name: string;
  scope: string | null;
  unit: string;
  definition: string;
  stats: DistributionSummary;
  last: number;
};

const TELEMETRY_LABELS: Record<string, string> = {
  host_cpu_percent: "Host CPU",
  host_memory_used_percent: "Host memory used",
  host_memory_available_bytes: "Host memory available",
  worker_process_cpu_percent: "Benchmark worker CPU",
  worker_process_rss_bytes: "Benchmark worker memory",
};

export function telemetryLabel(name: string): string {
  return TELEMETRY_LABELS[name] ?? name.replaceAll("_", " ");
}

/** Min/mean/max/last per sampled resource series instead of one row per sample. */
export function telemetrySummaries(metrics: MetricSample[]): TelemetrySummary[] {
  const groups = new Map<string, MetricSample[]>();
  for (const metric of metrics) {
    if (metric.source !== "system_observed") continue;
    if (typeof metric.value !== "number" || !Number.isFinite(metric.value)) continue;
    const id = `${metric.metric_name}|${metric.labels.scope ?? ""}|${metric.unit}`;
    const group = groups.get(id) ?? [];
    group.push(metric);
    groups.set(id, group);
  }
  const order = Object.keys(TELEMETRY_LABELS);
  return [...groups.entries()]
    .map(([id, group]) => {
      const ordered = [...group].sort((left, right) =>
        left.observed_at.localeCompare(right.observed_at),
      );
      const first = ordered[0] as MetricSample;
      const raw = ordered.map((metric) => metric.value as number);
      const scale = displayUnit(first.unit, Math.max(...raw.map(Math.abs)));
      const values = raw.map((value) => value / scale.divisor);
      return {
        id,
        name: first.metric_name,
        scope: first.labels.scope ?? null,
        unit: scale.unit,
        definition: first.definition,
        stats: summarize(values) as DistributionSummary,
        last: values[values.length - 1] as number,
      };
    })
    .sort((left, right) => {
      const leftIndex = order.indexOf(left.name);
      const rightIndex = order.indexOf(right.name);
      return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
    });
}

export type RecordFilter = MetricSample["source"] | "all";

/** Record counts per source, for the full-record explorer's filter. */
export function sourceCounts(
  metrics: MetricSample[],
): Array<{ source: RecordFilter; count: number }> {
  const counts = new Map<MetricSample["source"], number>();
  for (const metric of metrics) counts.set(metric.source, (counts.get(metric.source) ?? 0) + 1);
  return [
    { source: "all" as const, count: metrics.length },
    ...[...counts.entries()]
      .sort((left, right) => left[0].localeCompare(right[0]))
      .map(([source, count]) => ({ source, count })),
  ];
}

function recordRank(metric: MetricSample): number {
  if (metric.source === "system_observed") return 3;
  if (metric.labels.request_id !== undefined || metric.labels.snapshot !== undefined) return 2;
  return metric.source === "engine_native" ? 1 : 0;
}

/**
 * Records matching the filter, run-level summaries first, then per-request engine values,
 * then telemetry samples; names sort alphabetically within each group.
 */
export function filterRecords(
  metrics: MetricSample[],
  source: RecordFilter,
  query: string,
): MetricSample[] {
  const needle = query.trim().toLowerCase();
  return metrics
    .filter(
      (metric) =>
        (source === "all" || metric.source === source) &&
        (needle === "" ||
          metric.metric_name.toLowerCase().includes(needle) ||
          metric.aggregation.toLowerCase().includes(needle)),
    )
    .sort(
      (left, right) =>
        recordRank(left) - recordRank(right) ||
        left.metric_name.localeCompare(right.metric_name) ||
        left.observed_at.localeCompare(right.observed_at),
    );
}

const WARNING_EXPLANATIONS: Record<string, string> = {
  native_metrics_unavailable:
    "The engine exposes no server-wide counters to snapshot before and after the run. Per-request engine values and LocalForge's own measurements are unaffected.",
  native_metric_missing:
    "Expected server-wide counters were absent from a snapshot, so no before/after change could be computed for them. Per-request values are unaffected.",
};

export type WarningGroup = {
  code: string;
  count: number;
  explanation: string | null;
  messages: string[];
  firstSequence: number;
};

/** One entry per warning code, in order of first occurrence, with each distinct message kept. */
export function groupWarnings(
  events: Array<{ sequence_number: number; payload: Record<string, unknown> }>,
): WarningGroup[] {
  const groups = new Map<string, WarningGroup>();
  for (const event of events) {
    const code = String(event.payload.code ?? "warning");
    const message = String(event.payload.message ?? "Review this warning in the event timeline.");
    const group = groups.get(code) ?? {
      code,
      count: 0,
      explanation: WARNING_EXPLANATIONS[code] ?? null,
      messages: [],
      firstSequence: event.sequence_number,
    };
    group.count += 1;
    if (!group.messages.includes(message)) group.messages.push(message);
    groups.set(code, group);
  }
  return [...groups.values()].sort((left, right) => left.firstSequence - right.firstSequence);
}

/** Compact number formatting for summary tables: fewer decimals as magnitude grows. */
export function formatMeasurement(value: number | null | undefined, unit = ""): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  const digits = magnitude >= 1000 ? 0 : magnitude >= 100 ? 1 : magnitude >= 1 ? 2 : 3;
  const text = value.toLocaleString(undefined, { maximumFractionDigits: digits });
  return unit ? `${text} ${unit}` : text;
}
