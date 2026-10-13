import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { MetricSample, RequestSample } from "../lib/api";
import { RunRequestCharts, RunTelemetryCharts } from "./RunCharts";

afterEach(cleanup);

const baseSample = {
  run_id: "run-1",
  concurrency_level: 1,
  effective_concurrency: 1,
  outcome: "succeeded",
  started_at: "2026-10-13T19:12:20.000Z",
  completed_at: "2026-10-13T19:12:22.000Z",
  dispatch_offset_ms: 0,
  first_output_offset_ms: 100,
  completed_offset_ms: 2000,
  queue_time_ms: 0.1,
  prompt_tokens: 304,
  prompt_token_count_source: "engine_exact",
  completion_token_count_source: "engine_exact",
  error_class: null,
  output_event_count: 3,
  output_characters: 20,
  partial_output_observed: false,
  content_retained: false,
  created_at: "2026-10-13T19:12:20.000Z",
  updated_at: "2026-10-13T19:12:22.000Z",
  inter_token_latency_ms: [],
} as const;

const samples = [
  {
    ...baseSample,
    id: "s1",
    sequence_number: 1,
    request_id: "r1",
    phase: "measurement",
    latency_ms: 2061.8,
    ttft_ms: 131.4,
    completion_tokens: 37,
    inter_event_latency_ms: [44.2, 46.6, 87.7],
  },
  {
    ...baseSample,
    id: "s2",
    sequence_number: 2,
    request_id: "r2",
    phase: "measurement",
    latency_ms: 7480.5,
    ttft_ms: 111.6,
    completion_tokens: null,
    inter_event_latency_ms: [50.1],
  },
] as unknown as RequestSample[];

function metric(overrides: Partial<MetricSample>): MetricSample {
  return {
    id: String(Math.random()),
    run_id: "run-1",
    metric_name: "ttft_ms",
    value: 1,
    unit: "ms",
    source: "client_measured",
    aggregation: "p50",
    sample_count: 2,
    definition: "Server-reported timing.",
    formula: "R-7 linear-interpolation percentile at 50 (minimum 1 samples)",
    method_version: "v1",
    quality_flags: [],
    metric_kind: "gauge",
    adapter_id: null,
    adapter_version: null,
    engine_version: null,
    observed_at: "2026-10-13T19:12:22.000Z",
    labels: {},
    ...overrides,
  };
}

describe("RunRequestCharts", () => {
  it("charts real sample fields and states every gap explicitly", () => {
    render(
      <RunRequestCharts
        catchingUp={false}
        metrics={[
          metric({
            metric_name: "ollama.eval_duration",
            source: "engine_native",
            aggregation: "raw",
            unit: "ns",
            value: 1.8e9,
            labels: { phase: "measurement", request_id: "r1" },
          }),
        ]}
        samples={samples}
      />,
    );
    expect(
      screen.getByRole("img", { name: /Time to first token per measured request.*2 points/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Decode throughput.*1 points/ })).toBeInTheDocument();
    expect(
      screen.getByText(
        /1 succeeded request\(s\) excluded: the engine reported no completion token count/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Inter-event latency distribution" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/did not provide per-token timestamps/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Request latency percentiles" }).closest("figure"),
    ).toHaveTextContent("Derived in the browser from request samples");
    expect(screen.getByRole("heading", { name: "Generation (eval) duration" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Time per output token percentiles" }).closest("figure"),
    ).toHaveTextContent("No observations are available");
  });

  it("explains a missing engine timing source instead of drawing zeros", () => {
    render(<RunRequestCharts catchingUp={false} metrics={[]} samples={[]} />);
    expect(screen.getByText(/reported no per-request native timings/)).toBeInTheDocument();
    expect(
      screen.getByText("No measured request has produced a first output yet."),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("img")).toHaveLength(0);
  });
});

describe("RunTelemetryCharts", () => {
  it("renders one time series per observed resource metric", () => {
    render(
      <RunTelemetryCharts
        metrics={[
          metric({
            metric_name: "host_cpu_percent",
            source: "system_observed",
            aggregation: "sample",
            unit: "%",
            value: 12,
            observed_at: "2026-10-13T19:12:13.000Z",
            labels: { scope: "local_host" },
          }),
          metric({
            metric_name: "host_cpu_percent",
            source: "system_observed",
            aggregation: "sample",
            unit: "%",
            value: 30,
            observed_at: "2026-10-13T19:12:14.000Z",
            labels: { scope: "local_host" },
          }),
          metric({
            metric_name: "worker_process_rss_bytes",
            source: "system_observed",
            aggregation: "sample",
            unit: "bytes",
            value: 52281344,
            observed_at: "2026-10-13T19:12:14.000Z",
            labels: { scope: "benchmark_worker" },
          }),
        ]}
        runStartedAt="2026-10-13T19:12:12.000Z"
      />,
    );
    expect(
      screen.getByRole("img", { name: /Host CPU utilisation over the run, %\. 2 samples/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Benchmark worker resident memory over the run, MiB/ }),
    ).toBeInTheDocument();
  });

  it("renders nothing when no telemetry values exist", () => {
    const { container } = render(<RunTelemetryCharts metrics={[]} runStartedAt={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
