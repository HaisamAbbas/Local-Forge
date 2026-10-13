/**
 * Pure derivations that turn persisted run records into chart-ready series.
 *
 * Rules every helper follows:
 * - Only values present in API records are used. Nothing is interpolated, smoothed or
 *   back-filled; a missing value is counted and reported, never replaced with zero.
 * - Every derived number keeps its formula next to it so the UI can caption it.
 */
import type { MetricSample, RequestSample, RunComparison, ExperimentSummary } from "./api";

/** The samples endpoint also returns per-request gap arrays that `RequestSample` omits. */
export type RequestSampleWithGaps = RequestSample & {
  inter_event_latency_ms?: number[] | null;
  inter_token_latency_ms?: number[] | null;
};

export type RequestPoint = {
  /** Stable key (sequence number). */
  key: string;
  /** 1-based request sequence position, the x position on per-request charts. */
  requestNumber: number;
  value: number;
  /** Extra readout for tooltips and tables. */
  detail?: string;
};

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function bySequence(left: RequestSample, right: RequestSample): number {
  return left.sequence_number - right.sequence_number;
}

export function measurementSamples<T extends RequestSample>(samples: T[]): T[] {
  return samples.filter((sample) => sample.phase === "measurement").sort(bySequence);
}

// ---------------------------------------------------------------------------
// Per-request series
// ---------------------------------------------------------------------------

export type TtftSeries = { points: RequestPoint[]; missing: number };

/** Client-measured TTFT (`ttft_ms`) for each measurement request that recorded one. */
export function ttftPerRequest(samples: RequestSample[]): TtftSeries {
  const points: RequestPoint[] = [];
  let missing = 0;
  for (const sample of measurementSamples(samples)) {
    if (sample.outcome === "in_progress" && !isFiniteNumber(sample.ttft_ms)) continue;
    if (!isFiniteNumber(sample.ttft_ms)) {
      missing += 1;
      continue;
    }
    points.push({
      key: String(sample.sequence_number),
      requestNumber: sample.sequence_number + 1,
      value: sample.ttft_ms,
      detail: sample.outcome,
    });
  }
  return { points, missing };
}

export const DECODE_THROUGHPUT_FORMULA =
  "completion_tokens ÷ ((latency_ms − ttft_ms) ÷ 1000), per succeeded measurement request";

export type DecodeThroughputSeries = {
  points: RequestPoint[];
  /** Succeeded requests whose engine reported no completion token count. */
  missingTokenCount: number;
  /** Succeeded requests without both latency and TTFT. */
  missingTiming: number;
  /** Requests whose decode window was zero or negative (e.g. a single output event). */
  emptyWindow: number;
  /** Distinct `completion_token_count_source` values that fed the points. */
  tokenSources: string[];
};

/**
 * Per-request decode throughput in output tokens per second.
 * Uses the same window as the backend's generation throughput: the time from first
 * output to completion (`latency_ms − ttft_ms`). Token counts come only from
 * `completion_tokens`; when the engine did not report them the request is excluded.
 */
export function decodeThroughputPerRequest(samples: RequestSample[]): DecodeThroughputSeries {
  const points: RequestPoint[] = [];
  const sources = new Set<string>();
  let missingTokenCount = 0;
  let missingTiming = 0;
  let emptyWindow = 0;
  for (const sample of measurementSamples(samples)) {
    if (sample.outcome !== "succeeded") continue;
    if (!isFiniteNumber(sample.completion_tokens)) {
      missingTokenCount += 1;
      continue;
    }
    if (!isFiniteNumber(sample.latency_ms) || !isFiniteNumber(sample.ttft_ms)) {
      missingTiming += 1;
      continue;
    }
    const windowMs = sample.latency_ms - sample.ttft_ms;
    if (windowMs <= 0) {
      emptyWindow += 1;
      continue;
    }
    sources.add(sample.completion_token_count_source);
    points.push({
      key: String(sample.sequence_number),
      requestNumber: sample.sequence_number + 1,
      value: sample.completion_tokens / (windowMs / 1000),
      detail: `${sample.completion_tokens} tokens over ${Math.round(windowMs).toLocaleString()} ms`,
    });
  }
  return {
    points,
    missingTokenCount,
    missingTiming,
    emptyWindow,
    tokenSources: [...sources].sort(),
  };
}

// ---------------------------------------------------------------------------
// Inter-token / inter-event gaps and histogram binning
// ---------------------------------------------------------------------------

export type GapKind = "token" | "event";
export type GapValues = { kind: GapKind | null; values: number[]; requestCount: number };

/**
 * Pools per-request gaps from measurement samples. Token timestamps are preferred; when
 * no measured request reports `inter_token_latency_ms`, output-event gaps are used and
 * labelled as such (an event can carry more than one token).
 */
export function pooledGaps(samples: RequestSampleWithGaps[]): GapValues {
  const measured = measurementSamples(samples);
  for (const kind of ["token", "event"] as const) {
    const field = kind === "token" ? "inter_token_latency_ms" : "inter_event_latency_ms";
    const values: number[] = [];
    let requestCount = 0;
    for (const sample of measured) {
      const gaps = sample[field];
      if (!Array.isArray(gaps)) continue;
      const finite = gaps.filter(isFiniteNumber);
      if (finite.length === 0) continue;
      requestCount += 1;
      values.push(...finite);
    }
    if (values.length > 0) return { kind, values, requestCount };
  }
  return { kind: null, values: [], requestCount: 0 };
}

export type HistogramBin = { start: number; end: number; count: number };

/** Square-root rule bin count (clamped 5–30) on a clean bin width. */
export function histogramBins(values: number[], maxBins = 30): HistogramBin[] {
  const finite = values.filter(isFiniteNumber);
  if (finite.length === 0) return [];
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  if (min === max) return [{ start: min, end: max, count: finite.length }];
  const target = Math.min(maxBins, Math.max(5, Math.ceil(Math.sqrt(finite.length))));
  const width = cleanWidth((max - min) / target);
  const start = Math.floor(min / width) * width;
  const binCount = Math.max(1, Math.ceil((max - start) / width + 1e-9));
  const bins: HistogramBin[] = Array.from({ length: binCount }, (_, index) => ({
    start: round(start + index * width),
    end: round(start + (index + 1) * width),
    count: 0,
  }));
  for (const value of finite) {
    const index = Math.min(binCount - 1, Math.floor((value - start) / width));
    const bin = bins[index];
    if (bin) bin.count += 1;
  }
  return bins;
}

function cleanWidth(raw: number): number {
  const exponent = Math.floor(Math.log10(raw));
  const magnitude = 10 ** exponent;
  const fraction = raw / magnitude;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * magnitude;
}

function round(value: number): number {
  return Number(value.toPrecision(12));
}

// ---------------------------------------------------------------------------
// Percentiles
// ---------------------------------------------------------------------------

export const PERCENTILES = ["p50", "p90", "p95", "p99"] as const;
export type Percentile = (typeof PERCENTILES)[number];

/** Same minimum-sample gates the backend applies before reporting a tail percentile. */
export const PERCENTILE_MIN_SAMPLES: Record<Percentile, number> = {
  p50: 1,
  p90: 10,
  p95: 20,
  p99: 100,
};

/** R-7 (linear interpolation) quantile, matching the backend method. */
export function quantileR7(values: number[], probability: number): number | null {
  const sorted = values.filter(isFiniteNumber).sort((left, right) => left - right);
  if (sorted.length === 0) return null;
  const position = (sorted.length - 1) * probability;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  const low = sorted[lower] ?? 0;
  const high = sorted[upper] ?? low;
  return low + (high - low) * (position - lower);
}

export type PercentileRow = {
  key: string;
  label: string;
  value: number | null;
  /** Why the value is unavailable, when it is. */
  reason: string | null;
  sampleCount: number;
};

export type PercentileSummary = {
  source: "api" | "derived" | "none";
  unit: string;
  rows: PercentileRow[];
  formula: string;
};

function humanizeFlag(flag: string): string {
  return flag.replaceAll("_", " ");
}

function labelsText(labels: Record<string, string>): string {
  return Object.entries(labels)
    .filter(([key]) => key !== "token_count_source")
    .map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`)
    .join(", ");
}

/**
 * Percentile rows for one metric. Persisted p50/p90/p95/p99 records from the metrics
 * endpoint win (including their explicit unavailability). Only when the run has no
 * persisted percentile record for that metric yet (e.g. while it is still running) are
 * percentiles derived in the browser from `fallbackValues`, gated by the same minimum
 * sample counts, and labelled as derived.
 */
export function percentileSummary(
  metrics: MetricSample[],
  metricName: string,
  fallbackValues: number[],
  fallbackUnit: string,
): PercentileSummary {
  const persisted = metrics.filter(
    (metric) =>
      metric.metric_name === metricName &&
      (PERCENTILES as readonly string[]).includes(metric.aggregation),
  );
  if (persisted.length > 0) {
    const groups = new Set(persisted.map((metric) => JSON.stringify(metric.labels)));
    const ordered = [...persisted].sort((left, right) => {
      const labelOrder = JSON.stringify(left.labels).localeCompare(JSON.stringify(right.labels));
      if (labelOrder !== 0) return labelOrder;
      return (
        PERCENTILES.indexOf(left.aggregation as Percentile) -
        PERCENTILES.indexOf(right.aggregation as Percentile)
      );
    });
    return {
      source: "api",
      unit: persisted.find((metric) => metric.unit !== "unknown")?.unit ?? fallbackUnit,
      formula: persisted[0]?.formula.replace(/ at \d+.*$/, "") ?? "",
      rows: ordered.map((metric) => {
        const suffix = groups.size > 1 ? ` · ${labelsText(metric.labels)}` : "";
        const available = isFiniteNumber(metric.value);
        const minimum = /minimum (\d+) samples/.exec(metric.formula)?.[1];
        return {
          key: `${metric.aggregation}:${JSON.stringify(metric.labels)}`,
          label: `${metric.aggregation}${suffix}`,
          value: available ? metric.value : null,
          reason: available
            ? null
            : [
                metric.quality_flags.length
                  ? metric.quality_flags.map(humanizeFlag).join("; ")
                  : "not reported",
                minimum ? `needs ≥${minimum} samples` : null,
              ]
                .filter(Boolean)
                .join(" · "),
          sampleCount: metric.sample_count,
        };
      }),
    };
  }
  const values = fallbackValues.filter(isFiniteNumber);
  if (values.length === 0) return { source: "none", unit: fallbackUnit, rows: [], formula: "" };
  return {
    source: "derived",
    unit: fallbackUnit,
    formula: "R-7 linear-interpolation percentile computed in the browser from request samples",
    rows: PERCENTILES.map((percentile) => {
      const minimum = PERCENTILE_MIN_SAMPLES[percentile];
      const enough = values.length >= minimum;
      return {
        key: percentile,
        label: percentile,
        value: enough ? quantileR7(values, Number(percentile.slice(1)) / 100) : null,
        reason: enough ? null : `insufficient samples · needs ≥${minimum}, have ${values.length}`,
        sampleCount: values.length,
      };
    }),
  };
}

// ---------------------------------------------------------------------------
// Host resource telemetry
// ---------------------------------------------------------------------------

export type TimePoint = { key: string; t: number; value: number };
export type TelemetrySeries = {
  id: string;
  metricName: string;
  scope: string | null;
  /** Unit after display scaling (e.g. bytes → GiB). */
  unit: string;
  sourceUnit: string;
  definition: string;
  points: TimePoint[];
};

const TELEMETRY_ORDER = [
  "host_cpu_percent",
  "host_memory_used_percent",
  "host_memory_available_bytes",
  "worker_process_cpu_percent",
  "worker_process_rss_bytes",
];

/** Scale byte series to MiB or GiB for readable axes; other units are left untouched. */
export function displayUnit(unit: string, maximum: number): { unit: string; divisor: number } {
  if (unit !== "bytes") return { unit, divisor: 1 };
  if (maximum >= 1024 ** 3) return { unit: "GiB", divisor: 1024 ** 3 };
  if (maximum >= 1024 ** 2) return { unit: "MiB", divisor: 1024 ** 2 };
  if (maximum >= 1024) return { unit: "KiB", divisor: 1024 };
  return { unit, divisor: 1 };
}

/**
 * Time series for every `system_observed` metric with a numeric value, one series per
 * metric name and scope. x is seconds since `runStartedAt` (from `observed_at`).
 */
export function telemetrySeries(
  metrics: MetricSample[],
  runStartedAt: string | null,
): TelemetrySeries[] {
  const observed = metrics.filter(
    (metric) =>
      metric.source === "system_observed" &&
      isFiniteNumber(metric.value) &&
      Number.isFinite(Date.parse(metric.observed_at)),
  );
  if (observed.length === 0) return [];
  const startMs =
    runStartedAt && Number.isFinite(Date.parse(runStartedAt))
      ? Date.parse(runStartedAt)
      : Math.min(...observed.map((metric) => Date.parse(metric.observed_at)));
  const groups = new Map<string, MetricSample[]>();
  for (const metric of observed) {
    const id = `${metric.metric_name}|${metric.labels.scope ?? ""}|${metric.unit}`;
    const group = groups.get(id) ?? [];
    group.push(metric);
    groups.set(id, group);
  }
  const series = [...groups.entries()].map(([id, group]) => {
    const first = group[0] as MetricSample;
    const maximum = Math.max(...group.map((metric) => Math.abs(metric.value as number)));
    const scale = displayUnit(first.unit, maximum);
    const points = group
      .map((metric) => ({
        key: metric.id,
        t: (Date.parse(metric.observed_at) - startMs) / 1000,
        value: (metric.value as number) / scale.divisor,
      }))
      .sort((left, right) => left.t - right.t);
    return {
      id,
      metricName: first.metric_name,
      scope: first.labels.scope ?? null,
      unit: scale.unit,
      sourceUnit: first.unit,
      definition: first.definition,
      points,
    };
  });
  return series.sort((left, right) => {
    const leftIndex = TELEMETRY_ORDER.indexOf(left.metricName);
    const rightIndex = TELEMETRY_ORDER.indexOf(right.metricName);
    if (leftIndex !== rightIndex)
      return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
    return left.id.localeCompare(right.id);
  });
}

// ---------------------------------------------------------------------------
// Engine-native per-request timings
// ---------------------------------------------------------------------------

export type EngineTimingSeries = {
  metricName: string;
  unit: string;
  sourceUnit: string;
  definition: string;
  points: RequestPoint[];
};

const ENGINE_TIMING_ORDER = ["load_duration", "prompt_eval_duration", "eval_duration"];

function engineTimingRank(name: string): number {
  const suffix = name.slice(name.indexOf(".") + 1);
  const index = ENGINE_TIMING_ORDER.indexOf(suffix);
  if (index !== -1) return index;
  return suffix === "total_duration" ? 98 : 50;
}

/**
 * Raw engine-native per-request records (they carry a `request_id` label) for measurement
 * requests, matched to request samples for their sequence position. Nanoseconds are shown
 * as milliseconds. Records without a value (missing native metric) are skipped and the
 * series is omitted entirely when the engine reported nothing usable.
 */
export function engineTimingsPerRequest(
  metrics: MetricSample[],
  samples: RequestSample[],
): EngineTimingSeries[] {
  const sequenceByRequest = new Map<string, number>();
  for (const sample of samples) {
    if (sample.request_id) sequenceByRequest.set(sample.request_id, sample.sequence_number);
  }
  const groups = new Map<string, MetricSample[]>();
  for (const metric of metrics) {
    if (metric.source !== "engine_native" || metric.aggregation !== "raw") continue;
    if (!isFiniteNumber(metric.value)) continue;
    const requestId = metric.labels.request_id;
    if (!requestId || metric.labels.phase !== "measurement") continue;
    if (!sequenceByRequest.has(requestId)) continue;
    const key = `${metric.metric_name}|${metric.unit}`;
    const group = groups.get(key) ?? [];
    group.push(metric);
    groups.set(key, group);
  }
  return [...groups.values()]
    .map((group) => {
      const first = group[0] as MetricSample;
      const divisor = first.unit === "ns" ? 1_000_000 : first.unit === "us" ? 1_000 : 1;
      const unit = divisor === 1 ? first.unit : "ms";
      const points = group
        .map((metric) => {
          const sequence = sequenceByRequest.get(metric.labels.request_id as string) as number;
          return {
            key: `${sequence}:${metric.id}`,
            requestNumber: sequence + 1,
            value: (metric.value as number) / divisor,
          };
        })
        .sort((left, right) => left.requestNumber - right.requestNumber);
      return {
        metricName: first.metric_name,
        unit,
        sourceUnit: first.unit,
        definition: first.definition,
        points,
      };
    })
    .sort(
      (left, right) =>
        engineTimingRank(left.metricName) - engineTimingRank(right.metricName) ||
        left.metricName.localeCompare(right.metricName),
    );
}

// ---------------------------------------------------------------------------
// Comparisons and experiment summaries
// ---------------------------------------------------------------------------

export type MetricDirection = "lower" | "higher" | null;

/** Headline metrics charted on the comparison screen, in display order. */
export const KEY_COMPARISON_METRICS: Array<{
  metricName: string;
  aggregations: string[];
  title: string;
  direction: MetricDirection;
}> = [
  {
    metricName: "ttft_ms",
    aggregations: ["p50", "p95"],
    title: "Time to first token",
    direction: "lower",
  },
  {
    metricName: "request_latency_ms",
    aggregations: ["p50", "p95"],
    title: "Request latency",
    direction: "lower",
  },
  {
    metricName: "tpot_ms_per_token",
    aggregations: ["p50", "p95"],
    title: "Time per output token",
    direction: "lower",
  },
  {
    metricName: "inter_token_latency_ms",
    aggregations: ["p50", "p95"],
    title: "Inter-token latency",
    direction: "lower",
  },
  {
    metricName: "inter_event_latency_ms",
    aggregations: ["p50", "p95"],
    title: "Inter-event latency",
    direction: "lower",
  },
  {
    metricName: "output_throughput_tokens_per_second",
    aggregations: ["run_rate"],
    title: "Output throughput",
    direction: "higher",
  },
  {
    metricName: "generation_throughput_tokens_per_second",
    aggregations: ["run_rate"],
    title: "Generation (decode) throughput",
    direction: "higher",
  },
  {
    metricName: "request_throughput_requests_per_second",
    aggregations: ["run_rate"],
    title: "Request throughput",
    direction: "higher",
  },
  {
    metricName: "error_rate_percent",
    aggregations: ["run_rate"],
    title: "Error rate",
    direction: "lower",
  },
];

export type ComparisonBarRow = {
  key: string;
  label: string;
  value: number | null;
  isBaseline: boolean;
  range: [number, number] | null;
  note: string;
};

export type ComparisonChartGroup = {
  id: string;
  title: string;
  metricName: string;
  aggregation: string;
  unit: string;
  direction: MetricDirection;
  labels: string;
  rows: ComparisonBarRow[];
};

function aggregationLabel(aggregation: string): string {
  return aggregation === "run_rate" ? "run rate" : aggregation;
}

/** Chart groups for shared comparison metrics that have at least one reported value. */
export function comparisonChartGroups(
  comparison: RunComparison,
  runLabel: (runId: string) => string = (runId) => `Run ${runId.slice(0, 8)}`,
): ComparisonChartGroup[] {
  const groups: ComparisonChartGroup[] = [];
  for (const key of KEY_COMPARISON_METRICS) {
    for (const aggregation of key.aggregations) {
      const shared = comparison.shared_metrics.filter(
        (metric) => metric.metric_name === key.metricName && metric.aggregation === aggregation,
      );
      for (const metric of shared) {
        const rows = comparison.run_ids.map((runId) => {
          const result = metric.runs[runId];
          const value = isFiniteNumber(result?.value) ? (result?.value as number) : null;
          return {
            key: runId,
            label: runLabel(runId),
            value,
            isBaseline: runId === comparison.baseline_run_id,
            range: null,
            note: result ? `n=${result.sample_count}` : "no record",
          };
        });
        if (rows.every((row) => row.value === null)) continue;
        groups.push({
          id: `${metric.metric_name}|${metric.aggregation}|${JSON.stringify(metric.labels)}`,
          title: `${key.title} · ${aggregationLabel(metric.aggregation)}`,
          metricName: metric.metric_name,
          aggregation: metric.aggregation,
          unit: metric.unit,
          direction: key.direction,
          labels: labelsText(metric.labels),
          rows,
        });
      }
    }
  }
  return groups;
}

/**
 * Variant medians with their observed min–max spread across repetitions, for the same
 * headline metrics. The "Baseline" variant (the backend's name for it) is highlighted.
 */
export function experimentChartGroups(summary: ExperimentSummary): ComparisonChartGroup[] {
  const groups: ComparisonChartGroup[] = [];
  for (const key of KEY_COMPARISON_METRICS) {
    for (const aggregation of key.aggregations) {
      const identities = new Map<string, { unit: string; labels: Record<string, string> }>();
      for (const variant of summary.variants) {
        for (const metric of variant.metrics) {
          if (metric.metric_name !== key.metricName || metric.aggregation !== aggregation) continue;
          identities.set(`${metric.unit}|${JSON.stringify(metric.labels)}`, {
            unit: metric.unit,
            labels: metric.labels,
          });
        }
      }
      for (const [identity, info] of identities) {
        const rows = summary.variants.map((variant) => {
          const metric = variant.metrics.find(
            (item) =>
              item.metric_name === key.metricName &&
              item.aggregation === aggregation &&
              `${item.unit}|${JSON.stringify(item.labels)}` === identity,
          );
          const value = isFiniteNumber(metric?.median) ? (metric?.median as number) : null;
          const range =
            value !== null &&
            isFiniteNumber(metric?.spread_min) &&
            isFiniteNumber(metric?.spread_max)
              ? ([metric?.spread_min, metric?.spread_max] as [number, number])
              : null;
          return {
            key: variant.name,
            label: variant.name,
            value,
            isBaseline: variant.name.toLowerCase() === "baseline",
            range,
            note: metric ? `${metric.repeat_count} repeats` : "no record",
          };
        });
        if (rows.every((row) => row.value === null)) continue;
        groups.push({
          id: `${key.metricName}|${aggregation}|${identity}`,
          title: `${key.title} · ${aggregationLabel(aggregation)}`,
          metricName: key.metricName,
          aggregation,
          unit: info.unit,
          direction: key.direction,
          labels: labelsText(info.labels),
          rows,
        });
      }
    }
  }
  return groups;
}
