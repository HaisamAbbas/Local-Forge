import { describe, expect, it } from "vitest";

import type { ExperimentSummary, MetricSample, RunComparison } from "./api";
import {
  comparisonChartGroups,
  decodeThroughputPerRequest,
  displayUnit,
  engineTimingsPerRequest,
  experimentChartGroups,
  histogramBins,
  percentileSummary,
  pooledGaps,
  quantileR7,
  telemetrySeries,
  ttftPerRequest,
  type RequestSampleWithGaps,
} from "./chart-data";
import { formatTick, integerTicks, nearestIndex, niceTicks, roundedBarPath } from "./chart-scale";

function sample(overrides: Partial<RequestSampleWithGaps>): RequestSampleWithGaps {
  return {
    id: `sample-${overrides.sequence_number ?? 0}`,
    run_id: "run-1",
    sequence_number: 0,
    request_id: `request-${overrides.sequence_number ?? 0}`,
    phase: "measurement",
    concurrency_level: 1,
    effective_concurrency: 1,
    outcome: "succeeded",
    started_at: "2026-10-13T19:12:12.000Z",
    completed_at: "2026-10-13T19:12:14.000Z",
    dispatch_offset_ms: 0,
    first_output_offset_ms: 100,
    completed_offset_ms: 2000,
    queue_time_ms: 0.1,
    latency_ms: 2000,
    ttft_ms: 100,
    prompt_tokens: 304,
    completion_tokens: 38,
    prompt_token_count_source: "engine_exact",
    completion_token_count_source: "engine_exact",
    error_class: null,
    output_event_count: 38,
    output_characters: 175,
    partial_output_observed: false,
    content_retained: false,
    created_at: "2026-10-13T19:12:12.000Z",
    updated_at: "2026-10-13T19:12:14.000Z",
    inter_event_latency_ms: [],
    inter_token_latency_ms: [],
    ...overrides,
  };
}

function metric(overrides: Partial<MetricSample>): MetricSample {
  return {
    id: `metric-${Math.random()}`,
    run_id: "run-1",
    metric_name: "ttft_ms",
    value: 1,
    unit: "ms",
    source: "client_measured",
    aggregation: "p50",
    sample_count: 4,
    definition: "definition",
    formula: "R-7 linear-interpolation percentile at 50 (minimum 1 samples)",
    method_version: "v1",
    quality_flags: [],
    metric_kind: "gauge",
    adapter_id: null,
    adapter_version: null,
    engine_version: null,
    observed_at: "2026-10-13T19:12:20.000Z",
    labels: { concurrency_level: "1", effective_concurrency: "1" },
    ...overrides,
  };
}

describe("per-request series", () => {
  it("plots measured TTFT only and counts requests without a first output", () => {
    const series = ttftPerRequest([
      sample({ sequence_number: 0, phase: "warmup", ttft_ms: 103 }),
      sample({ sequence_number: 1, ttft_ms: 131.4 }),
      sample({ sequence_number: 2, ttft_ms: null, outcome: "failed" }),
      sample({ sequence_number: 3, ttft_ms: null, outcome: "in_progress" }),
    ]);
    expect(series.points).toEqual([
      { key: "1", requestNumber: 2, value: 131.4, detail: "succeeded" },
    ]);
    expect(series.missing).toBe(1);
  });

  it("derives decode throughput from completion tokens over latency minus TTFT", () => {
    const series = decodeThroughputPerRequest([
      sample({ sequence_number: 1, completion_tokens: 37, latency_ms: 2061.8, ttft_ms: 131.4 }),
      sample({ sequence_number: 2, completion_tokens: null }),
      sample({ sequence_number: 3, latency_ms: null }),
      sample({ sequence_number: 4, latency_ms: 100, ttft_ms: 100 }),
      sample({ sequence_number: 5, outcome: "failed" }),
    ]);
    expect(series.points).toHaveLength(1);
    expect(series.points[0]?.value).toBeCloseTo(37 / ((2061.8 - 131.4) / 1000), 6);
    expect(series.missingTokenCount).toBe(1);
    expect(series.missingTiming).toBe(1);
    expect(series.emptyWindow).toBe(1);
    expect(series.tokenSources).toEqual(["engine_exact"]);
  });
});

describe("gaps and histogram", () => {
  it("prefers token gaps, falls back to event gaps, and never mixes warmup in", () => {
    expect(
      pooledGaps([
        sample({ sequence_number: 0, phase: "warmup", inter_token_latency_ms: [999] }),
        sample({ sequence_number: 1, inter_event_latency_ms: [40, 50] }),
        sample({ sequence_number: 2, inter_event_latency_ms: [60] }),
      ]),
    ).toEqual({ kind: "event", values: [40, 50, 60], requestCount: 2 });
    expect(
      pooledGaps([sample({ inter_token_latency_ms: [10], inter_event_latency_ms: [40] })]).kind,
    ).toBe("token");
    expect(pooledGaps([sample({})])).toEqual({ kind: null, values: [], requestCount: 0 });
  });

  it("bins every value exactly once on clean edges", () => {
    const values = [41.9, 44, 45.1, 52.1, 55.3, 87.7, 241.6];
    const bins = histogramBins(values);
    expect(bins.reduce((sum, bin) => sum + bin.count, 0)).toBe(values.length);
    expect(bins[0]?.start).toBeLessThanOrEqual(41.9);
    expect(bins.at(-1)?.end).toBeGreaterThanOrEqual(241.6);
    expect(histogramBins([])).toEqual([]);
    expect(histogramBins([5, 5])).toEqual([{ start: 5, end: 5, count: 2 }]);
  });
});

describe("percentiles", () => {
  it("matches the backend R-7 method", () => {
    expect(quantileR7([2061.8, 7480.5, 7348.9, 6948.3], 0.5)).toBeCloseTo(7148.6, 1);
    expect(quantileR7([], 0.5)).toBeNull();
  });

  it("uses persisted percentiles and explains unavailable tails", () => {
    const summary = percentileSummary(
      [
        metric({
          aggregation: "p95",
          value: null,
          quality_flags: ["insufficient_samples_for_stable_tail"],
          formula: "R-7 linear-interpolation percentile at 95 (minimum 20 samples)",
        }),
        metric({ aggregation: "p50", value: 144.4 }),
        metric({ aggregation: "mean", value: 147 }),
      ],
      "ttft_ms",
      [1, 2, 3],
      "ms",
    );
    expect(summary.source).toBe("api");
    expect(summary.rows.map((row) => row.label)).toEqual(["p50", "p95"]);
    expect(summary.rows[0]?.value).toBe(144.4);
    expect(summary.rows[1]).toMatchObject({
      value: null,
      reason: "insufficient samples for stable tail · needs ≥20 samples",
    });
  });

  it("derives provisional percentiles with the same minimum-sample gates", () => {
    const summary = percentileSummary([], "ttft_ms", [100, 200, 300, 400], "ms");
    expect(summary.source).toBe("derived");
    expect(summary.rows.find((row) => row.label === "p50")?.value).toBe(250);
    expect(summary.rows.find((row) => row.label === "p90")).toMatchObject({
      value: null,
      reason: "insufficient samples · needs ≥10, have 4",
    });
    expect(percentileSummary([], "ttft_ms", [], "ms").source).toBe("none");
  });
});

describe("telemetry and engine timings", () => {
  it("groups system_observed samples into time series relative to run start", () => {
    const series = telemetrySeries(
      [
        metric({
          id: "a",
          metric_name: "host_memory_available_bytes",
          source: "system_observed",
          aggregation: "sample",
          unit: "bytes",
          value: 5373378560,
          observed_at: "2026-10-13T19:12:20.000Z",
          labels: { scope: "local_host" },
        }),
        metric({
          id: "b",
          metric_name: "host_memory_available_bytes",
          source: "system_observed",
          aggregation: "sample",
          unit: "bytes",
          value: 5368709120,
          observed_at: "2026-10-13T19:12:19.000Z",
          labels: { scope: "local_host" },
        }),
        metric({
          id: "c",
          metric_name: "host_cpu_percent",
          source: "system_observed",
          aggregation: "sample",
          unit: "%",
          value: null,
          labels: { scope: "local_host" },
        }),
        metric({ id: "d", metric_name: "ttft_ms" }),
      ],
      "2026-10-13T19:12:12.000Z",
    );
    expect(series).toHaveLength(1);
    expect(series[0]).toMatchObject({
      metricName: "host_memory_available_bytes",
      unit: "GiB",
      sourceUnit: "bytes",
    });
    expect(series[0]?.points.map((point) => point.t)).toEqual([7, 8]);
    expect(series[0]?.points[0]?.value).toBeCloseTo(5);
    expect(displayUnit("%", 99)).toEqual({ unit: "%", divisor: 1 });
  });

  it("maps per-request engine timings to measured request positions in milliseconds", () => {
    const samples = [
      sample({ sequence_number: 0, phase: "warmup", request_id: "warm" }),
      sample({ sequence_number: 1, request_id: "r1" }),
    ];
    const series = engineTimingsPerRequest(
      [
        metric({
          metric_name: "ollama.total_duration",
          source: "engine_native",
          aggregation: "raw",
          unit: "ns",
          value: 2e9,
          labels: { phase: "measurement", request_id: "r1" },
        }),
        metric({
          metric_name: "ollama.eval_duration",
          source: "engine_native",
          aggregation: "raw",
          unit: "ns",
          value: 1.8e9,
          labels: { phase: "measurement", request_id: "r1" },
        }),
        metric({
          metric_name: "ollama.eval_duration",
          source: "engine_native",
          aggregation: "raw",
          unit: "ns",
          value: 7e9,
          labels: { phase: "warmup", request_id: "warm" },
        }),
        metric({
          metric_name: "ollama.eval_duration",
          source: "engine_native",
          aggregation: "raw",
          unit: "unknown",
          value: null,
          labels: { snapshot: "before" },
          quality_flags: ["missing_native_metric"],
        }),
      ],
      samples,
    );
    expect(series.map((item) => item.metricName)).toEqual([
      "ollama.eval_duration",
      "ollama.total_duration",
    ]);
    expect(series[0]?.points).toEqual([{ key: expect.any(String), requestNumber: 2, value: 1800 }]);
    expect(series[0]?.unit).toBe("ms");
  });
});

describe("comparison groups", () => {
  const comparison = {
    run_ids: ["aaaaaaaa-1", "bbbbbbbb-2"],
    baseline_run_id: "aaaaaaaa-1",
    shared_metrics: [
      {
        metric_name: "ttft_ms",
        aggregation: "p50",
        unit: "ms",
        labels: {},
        runs: {
          "aaaaaaaa-1": { value: 144.4, sample_count: 4, record_count: 1 },
          "bbbbbbbb-2": { value: 2894.5, sample_count: 4, record_count: 1 },
        },
      },
      {
        metric_name: "ttft_ms",
        aggregation: "p95",
        unit: "ms",
        labels: {},
        runs: {
          "aaaaaaaa-1": { value: null, sample_count: 0, record_count: 1 },
          "bbbbbbbb-2": { value: null, sample_count: 0, record_count: 1 },
        },
      },
      {
        metric_name: "output_throughput_tokens_per_second",
        aggregation: "run_rate",
        unit: "tokens/s",
        labels: {},
        runs: { "aaaaaaaa-1": { value: 17.6, sample_count: 4, record_count: 1 } },
      },
      { metric_name: "queue_time_ms", aggregation: "p50", unit: "ms", labels: {}, runs: {} },
    ],
  } as unknown as RunComparison;

  it("charts headline metrics with values, keeps missing runs explicit, highlights baseline", () => {
    const groups = comparisonChartGroups(comparison);
    expect(groups.map((group) => group.title)).toEqual([
      "Time to first token · p50",
      "Output throughput · run rate",
    ]);
    expect(groups[0]?.rows[0]).toMatchObject({
      label: "Run aaaaaaaa",
      value: 144.4,
      isBaseline: true,
    });
    expect(groups[1]?.rows[1]).toMatchObject({ value: null, isBaseline: false, note: "no record" });
  });

  it("uses medians and min–max spread for experiment variants", () => {
    const summary = {
      variants: [
        {
          name: "Baseline",
          metrics: [
            {
              metric_name: "ttft_ms",
              aggregation: "p50",
              unit: "ms",
              labels: {},
              median: 140,
              spread_min: 130,
              spread_max: 150,
              repeat_count: 3,
            },
          ],
        },
        { name: "Variant 1", metrics: [] },
      ],
    } as unknown as ExperimentSummary;
    const groups = experimentChartGroups(summary);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.rows[0]).toMatchObject({ value: 140, range: [130, 150], isBaseline: true });
    expect(groups[0]?.rows[1]).toMatchObject({ value: null, range: null, isBaseline: false });
  });
});

describe("scale helpers", () => {
  it("produces clean zero-based ticks and compact labels", () => {
    expect(niceTicks(0, 7480)).toEqual([0, 2000, 4000, 6000, 8000]);
    expect(niceTicks(0, 0)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    expect(formatTick(2500)).toBe("2.5K");
    expect(formatTick(0.125)).toBe("0.125");
    expect(integerTicks(2, 5)).toEqual([2, 3, 4, 5]);
    expect(nearestIndex([10, 20, 30], 24)).toBe(1);
    expect(nearestIndex([], 5)).toBeNull();
    expect(roundedBarPath(0, 0, 0, 10, 4, "up")).toBe("");
    expect(roundedBarPath(0, 0, 20, 10, 4, "right")).toContain("Q20,0 20,4");
  });
});
