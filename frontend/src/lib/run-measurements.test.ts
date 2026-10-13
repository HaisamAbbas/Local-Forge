import { describe, expect, it } from "vitest";

import type { MetricSample } from "./api";
import {
  describeFlag,
  engineSummary,
  filterRecords,
  findRunMetric,
  formatMeasurement,
  loadLevels,
  outcomeRecords,
  sourceCounts,
  telemetrySummaries,
  throughputRecords,
  timingMatrix,
  unavailableReason,
} from "./run-measurements";

let counter = 0;

function metric(overrides: Partial<MetricSample>): MetricSample {
  counter += 1;
  return {
    id: `m-${counter}`,
    run_id: "run",
    metric_name: "ttft_ms",
    value: 1,
    unit: "ms",
    source: "client_measured",
    aggregation: "p50",
    sample_count: 20,
    definition: "definition",
    formula: "formula",
    method_version: "v1",
    quality_flags: [],
    metric_kind: "gauge",
    adapter_id: null,
    adapter_version: null,
    engine_version: null,
    observed_at: "2026-10-13T19:00:00Z",
    labels: { concurrency_level: "1", effective_concurrency: "1" },
    ...overrides,
  };
}

describe("timingMatrix", () => {
  it("puts mean and percentiles for one metric on one row, keeping unavailable cells", () => {
    const rows = timingMatrix(
      [
        metric({ metric_name: "ttft_ms", aggregation: "mean", value: 2185 }),
        metric({ metric_name: "ttft_ms", aggregation: "p50", value: 2364 }),
        metric({
          metric_name: "ttft_ms",
          aggregation: "p99",
          value: null,
          quality_flags: ["insufficient_samples_for_stable_tail"],
        }),
        metric({ metric_name: "request_latency_ms", aggregation: "p50", value: 6026 }),
      ],
      "1",
    );

    expect(rows.map((row) => row.name)).toEqual(["ttft_ms", "request_latency_ms"]);
    const ttft = rows[0]!;
    expect(ttft.cells.mean?.value).toBe(2185);
    expect(ttft.cells.p50?.value).toBe(2364);
    expect(ttft.cells.p90).toBeNull();
    expect(unavailableReason(ttft.cells.p99!)).toBe("Too few samples for this percentile");
  });

  it("ignores per-request, telemetry, and engine-native records", () => {
    const rows = timingMatrix([
      metric({ labels: { request_id: "r1" } }),
      metric({ source: "system_observed", aggregation: "sample" }),
      metric({ source: "engine_native", aggregation: "p50" }),
    ]);
    expect(rows).toEqual([]);
  });

  it("separates concurrency levels in a sweep", () => {
    const metrics = [
      metric({ value: 10, labels: { concurrency_level: "1" } }),
      metric({ value: 30, labels: { concurrency_level: "4" } }),
    ];
    expect(loadLevels(metrics)).toEqual(["1", "4"]);
    expect(timingMatrix(metrics, "4")[0]?.cells.p50?.value).toBe(30);
    expect(findRunMetric(metrics, "ttft_ms", "p50", "1")?.value).toBe(10);
  });
});

describe("throughput and outcome records", () => {
  it("returns labelled run-level records in display order", () => {
    const metrics = [
      metric({ metric_name: "requests_succeeded", aggregation: "run_total", value: 20 }),
      metric({ metric_name: "requests_attempted", aggregation: "run_total", value: 20 }),
      metric({
        metric_name: "generation_throughput_tokens_per_second",
        aggregation: "run_rate",
        value: 20.9,
      }),
    ];
    expect(outcomeRecords(metrics, "1").map((item) => item.label)).toEqual([
      "Attempted",
      "Succeeded",
    ]);
    expect(throughputRecords(metrics, "1")[0]?.metric.value).toBe(20.9);
  });
});

describe("engineSummary", () => {
  it("summarises per-request measurement values in ms and sets aside empty records", () => {
    const summary = engineSummary([
      metric({
        source: "engine_native",
        aggregation: "raw",
        metric_name: "ollama.eval_duration",
        unit: "ns",
        value: 2_000_000,
        labels: { request_id: "a", phase: "measurement" },
      }),
      metric({
        source: "engine_native",
        aggregation: "raw",
        metric_name: "ollama.eval_duration",
        unit: "ns",
        value: 4_000_000,
        labels: { request_id: "b", phase: "measurement" },
      }),
      metric({
        source: "engine_native",
        aggregation: "raw",
        metric_name: "ollama.eval_duration",
        unit: "ns",
        value: 99_000_000,
        labels: { request_id: "w", phase: "warmup" },
      }),
      metric({
        source: "engine_native",
        aggregation: "p50",
        metric_name: "ollama.eval_duration",
        unit: "ns",
        value: 3_000_000,
        labels: { concurrency_level: "1" },
      }),
      metric({
        source: "engine_native",
        aggregation: "delta",
        metric_name: "ollama.eval_duration",
        unit: "unknown",
        value: null,
        labels: {},
        quality_flags: ["missing_native_metric"],
      }),
    ]);
    expect(summary.perRequest).toHaveLength(1);
    expect(summary.perRequest[0]).toMatchObject({
      unit: "ms",
      stats: { count: 2, mean: 3, min: 2, max: 4 },
    });
    expect(summary.runLevel).toEqual([]);
    expect(summary.unavailableCount).toBe(1);
    expect(summary.unavailableReasons).toEqual(["The engine did not report this value"]);
  });
});

describe("telemetrySummaries", () => {
  it("reduces samples to min/mean/max/last in a readable unit", () => {
    const gib = 1024 ** 3;
    const summaries = telemetrySummaries([
      metric({
        source: "system_observed",
        aggregation: "sample",
        metric_name: "host_memory_available_bytes",
        unit: "bytes",
        value: 4 * gib,
        observed_at: "2026-10-13T19:00:02Z",
        labels: { scope: "local_host" },
      }),
      metric({
        source: "system_observed",
        aggregation: "sample",
        metric_name: "host_memory_available_bytes",
        unit: "bytes",
        value: 2 * gib,
        observed_at: "2026-10-13T19:00:01Z",
        labels: { scope: "local_host" },
      }),
    ]);
    expect(summaries[0]).toMatchObject({
      unit: "GiB",
      last: 4,
      stats: { count: 2, mean: 3, min: 2, max: 4 },
    });
  });
});

describe("record explorer helpers", () => {
  it("counts sources and filters by source and name", () => {
    const metrics = [
      metric({ metric_name: "ttft_ms" }),
      metric({ metric_name: "host_cpu_percent", source: "system_observed" }),
    ];
    expect(sourceCounts(metrics)).toEqual([
      { source: "all", count: 2 },
      { source: "client_measured", count: 1 },
      { source: "system_observed", count: 1 },
    ]);
    expect(filterRecords(metrics, "system_observed", "")).toHaveLength(1);
    expect(filterRecords(metrics, "all", "TTFT")).toHaveLength(1);
  });
});

describe("formatting", () => {
  it("uses fewer decimals for larger values and a dash for missing ones", () => {
    expect(formatMeasurement(16863.96)).toBe("16,864");
    expect(formatMeasurement(20.909, "tokens/s")).toBe("20.91 tokens/s");
    expect(formatMeasurement(null)).toBe("—");
    expect(describeFlag("unavailable_something_new")).toBe("something new");
  });
});
