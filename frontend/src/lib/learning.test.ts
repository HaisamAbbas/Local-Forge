import { describe, expect, it } from "vitest";

import type { BenchmarkPlan, MetricSample } from "./api";
import {
  EXPERIMENT_GUIDES,
  METRIC_GLOSSARY,
  diagnoseRun,
  diagnosisContextFromPlan,
  formatMetricValue,
  glossaryAnchorId,
  glossaryEntry,
  glossaryHref,
  learningHref,
  parseLearningHash,
  searchGlossary,
  type DiagnosisFinding,
} from "./learning";

const RUN_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

let nextId = 0;
function metric(
  name: string,
  value: number | null,
  overrides: Partial<MetricSample> = {},
): MetricSample {
  nextId += 1;
  return {
    id: `m-${nextId}`,
    run_id: RUN_ID,
    metric_name: name,
    value,
    unit: "ms",
    source: "client_measured",
    aggregation: "mean",
    sample_count: 50,
    definition: "",
    formula: "",
    method_version: "localforge.metrics.v1",
    quality_flags: [],
    metric_kind: "gauge",
    adapter_id: null,
    adapter_version: null,
    engine_version: null,
    observed_at: `2026-10-14T10:00:${String(nextId % 60).padStart(2, "0")}Z`,
    labels: {},
    ...overrides,
  };
}

function find(findings: DiagnosisFinding[], id: string): DiagnosisFinding {
  const finding = findings.find((item) => item.id === id);
  if (!finding) throw new Error(`missing finding ${id}`);
  return finding;
}

function engineRaw(name: string, valueNs: number, phase: string, second: number): MetricSample {
  return metric(name, valueNs, {
    unit: "ns",
    source: "engine_native",
    aggregation: "raw",
    labels: { request_id: `r-${second}`, phase },
    observed_at: `2026-10-14T10:00:${String(second).padStart(2, "0")}Z`,
  });
}

function system(name: string, value: number, unit = "%"): MetricSample {
  return metric(name, value, { unit, source: "system_observed", aggregation: "sample" });
}

describe("metric glossary", () => {
  it("covers every metric name the backend records", () => {
    const backendNames = [
      "request_latency_ms",
      "ttft_ms",
      "queue_time_ms",
      "inter_event_latency_ms",
      "inter_token_latency_ms",
      "tpot_ms_per_token",
      "input_throughput_tokens_per_second",
      "output_throughput_tokens_per_second",
      "generation_throughput_tokens_per_second",
      "prompt_processing_throughput_tokens_per_second",
      "request_throughput_requests_per_second",
      "requests_attempted",
      "requests_succeeded",
      "requests_failed",
      "requests_disconnected",
      "requests_timed_out",
      "requests_cancelled",
      "requests_interrupted",
      "error_rate_percent",
      "ollama.total_duration",
      "ollama.load_duration",
      "ollama.prompt_eval_duration",
      "ollama.eval_duration",
      "ollama.prompt_eval_count",
      "ollama.eval_count",
      "ollama.prompt_eval_rate_tokens_per_second",
      "ollama.eval_rate_tokens_per_second",
      "host_cpu_percent",
      "host_memory_used_percent",
      "host_memory_available_bytes",
      "worker_process_cpu_percent",
      "worker_process_rss_bytes",
    ];
    for (const name of backendNames) expect(glossaryEntry(name)?.name).toBe(name);
    expect(Object.keys(METRIC_GLOSSARY).sort()).toEqual([...backendNames].sort());
  });

  it("gives each entry a formula, source and caveats, and links only to known entries", () => {
    for (const entry of Object.values(METRIC_GLOSSARY)) {
      expect(entry.summary.length).toBeGreaterThan(20);
      expect(entry.formula).not.toBe("");
      expect(entry.caveats.length).toBeGreaterThan(0);
      for (const related of entry.related) expect(glossaryEntry(related)).not.toBeNull();
    }
    expect(METRIC_GLOSSARY.inter_token_latency_ms?.caveats.join(" ")).toMatch(/Ollama/);
    expect(METRIC_GLOSSARY.ttft_ms?.caveats.join(" ")).toMatch(/p95 needs 20/);
  });

  it("searches names, labels and aliases with optional category", () => {
    expect(searchGlossary("ttft").map((entry) => entry.name)).toContain("ttft_ms");
    expect(searchGlossary("first token")[0]?.name).toBe("ttft_ms");
    expect(searchGlossary("", "system").every((entry) => entry.category === "system")).toBe(true);
    expect(searchGlossary("zzz-nothing")).toEqual([]);
  });

  it("does not resolve prototype keys as entries", () => {
    expect(glossaryEntry("toString")).toBeNull();
    expect(glossaryEntry("constructor")).toBeNull();
  });

  it("every guide watches glossary metrics", () => {
    for (const guide of EXPERIMENT_GUIDES) {
      for (const name of guide.watch) expect(glossaryEntry(name)).not.toBeNull();
    }
    expect(EXPERIMENT_GUIDES.filter((guide) => guide.kind === "preset").map((g) => g.id)).toEqual([
      "fixed_prefill",
      "fixed_decode",
      "mixed_chat",
      "concurrency_sweep",
      "target_request_rate",
      "long_context",
    ]);
  });
});

describe("deep links", () => {
  it("parses metric and run from the learning hash", () => {
    expect(parseLearningHash(`#learning?metric=ttft_ms&run=${RUN_ID}`)).toEqual({
      metric: "ttft_ms",
      run: RUN_ID,
    });
    expect(parseLearningHash("#learning?metric=ollama.load_duration").metric).toBe(
      "ollama.load_duration",
    );
  });

  it("ignores unknown metrics, bad run ids and other screens", () => {
    expect(parseLearningHash("#learning?metric=nope&run=123")).toEqual({ metric: null, run: null });
    expect(parseLearningHash("#runs?metric=ttft_ms")).toEqual({ metric: null, run: null });
    expect(parseLearningHash("")).toEqual({ metric: null, run: null });
  });

  it("builds hrefs and anchor ids", () => {
    expect(glossaryHref("ttft_ms")).toBe("#learning?metric=ttft_ms");
    expect(learningHref()).toBe("#learning");
    expect(learningHref({ run: RUN_ID })).toBe(`#learning?run=${RUN_ID}`);
    expect(glossaryAnchorId("ollama.load_duration")).toBe("metric-ollama-load_duration");
  });
});

describe("formatting", () => {
  it("scales nanoseconds and bytes", () => {
    expect(formatMetricValue(2_500_000_000, "ns")).toBe("2,500 ms");
    expect(formatMetricValue(2 * 1024 ** 3, "bytes")).toBe("2 GiB");
    expect(formatMetricValue(12.345, "%")).toBe("12.3%");
  });
});

describe("diagnosis", () => {
  it("reports not enough data for every rule on an empty run, never zero values", () => {
    const findings = diagnoseRun([]);
    expect(findings.length).toBeGreaterThan(5);
    for (const finding of findings) {
      expect(finding.status).toBe("insufficient_data");
      expect(finding.statement).toMatch(/^Not enough data/);
      expect(finding.evidence).toEqual([]);
    }
  });

  it("treats unavailable values as missing rather than zero", () => {
    const findings = diagnoseRun([
      metric("ttft_ms", null, { quality_flags: ["unavailable_no_samples"] }),
      metric("request_latency_ms", 1000),
      metric("error_rate_percent", null, { aggregation: "run_rate", unit: "%" }),
    ]);
    expect(find(findings, "ttft_share").status).toBe("insufficient_data");
    expect(find(findings, "errors").status).toBe("insufficient_data");
  });

  it("flags TTFT dominating request latency with careful wording", () => {
    const finding = find(
      diagnoseRun([metric("ttft_ms", 800), metric("request_latency_ms", 1000)]),
      "ttft_share",
    );
    expect(finding.status).toBe("observed");
    expect(finding.statement).toMatch(/80%/);
    expect(finding.statement).toMatch(/consistent with prompt processing/);
    expect(finding.evidence.map((item) => item.value)).toEqual([800, 1000]);
  });

  it("describes decode-dominated runs as no signal", () => {
    const finding = find(
      diagnoseRun([metric("ttft_ms", 100), metric("request_latency_ms", 1000)]),
      "ttft_share",
    );
    expect(finding.status).toBe("not_observed");
    expect(finding.statement).toMatch(/generation \(decode\) dominating/);
  });

  it("detects a cold model load on the first request", () => {
    const metrics = [
      engineRaw("ollama.load_duration", 3_000_000_000, "measurement", 1),
      engineRaw("ollama.load_duration", 20_000_000, "measurement", 2),
      engineRaw("ollama.load_duration", 15_000_000, "measurement", 3),
    ];
    const finding = find(diagnoseRun(metrics), "cold_load");
    expect(finding.status).toBe("observed");
    expect(finding.severity).toBe("attention");
    expect(finding.statement).toMatch(/consistent with a cold model load/);
    expect(finding.statement).toMatch(/measurement phase/);
    expect(finding.evidence[0]?.value).toBe(3000);
  });

  it("marks a warmup cold load as informational", () => {
    const metrics = [
      engineRaw("ollama.load_duration", 3_000_000_000, "warmup", 1),
      engineRaw("ollama.load_duration", 20_000_000, "measurement", 2),
    ];
    const finding = find(diagnoseRun(metrics), "cold_load");
    expect(finding.severity).toBe("info");
    expect(finding.statement).toMatch(/excluded from the measurement summaries/);
  });

  it("notes repeated reloads when load stays high", () => {
    const metrics = [1, 2, 3].map((second) =>
      engineRaw("ollama.load_duration", 2_000_000_000, "measurement", second),
    );
    expect(find(diagnoseRun(metrics), "cold_load").statement).toMatch(/reloaded between requests/);
  });

  it("flags host CPU saturation with a quiet benchmark worker", () => {
    const metrics = [
      ...[95, 97, 99].map((value) => system("host_cpu_percent", value)),
      ...[10, 12, 8].map((value) =>
        system("worker_process_cpu_percent", value, "% of one logical CPU"),
      ),
    ];
    const findings = diagnoseRun(metrics);
    const cpu = find(findings, "host_cpu");
    expect(cpu.status).toBe("observed");
    expect(cpu.statement).toMatch(/consistent with the engine, or another process/);
    expect(find(findings, "client_busy").status).toBe("not_observed");
  });

  it("needs at least three CPU samples", () => {
    const metrics = [system("host_cpu_percent", 99), system("worker_process_cpu_percent", 5)];
    expect(find(diagnoseRun(metrics), "host_cpu").status).toBe("insufficient_data");
  });

  it("flags a busy load generator", () => {
    const metrics = [90, 95, 100].map((value) =>
      system("worker_process_cpu_percent", value, "% of one logical CPU"),
    );
    expect(find(diagnoseRun(metrics), "client_busy").status).toBe("observed");
  });

  it("reports error rate with a breakdown and timeout explanation", () => {
    const rate = { aggregation: "run_rate", unit: "%", source: "derived" as const };
    const total = { aggregation: "run_total", unit: "requests", source: "derived" as const };
    const finding = find(
      diagnoseRun([
        metric("error_rate_percent", 10, rate),
        metric("requests_attempted", 50, total),
        metric("requests_timed_out", 4, total),
        metric("requests_failed", 1, total),
        metric("requests_cancelled", 0, total),
      ]),
      "errors",
    );
    expect(finding.status).toBe("observed");
    expect(finding.severity).toBe("attention");
    expect(finding.statement).toMatch(/4 timed out, 1 failed|1 failed, 4 timed out/);
    expect(finding.statement).toMatch(/Timeouts are consistent with/);
    expect(finding.evidence.some((item) => item.metric === "requests_cancelled")).toBe(false);
  });

  it("reports a clean run as no signal", () => {
    const finding = find(
      diagnoseRun([
        metric("error_rate_percent", 0, { aggregation: "run_rate", unit: "%" }),
        metric("requests_attempted", 50, { aggregation: "run_total", unit: "requests" }),
      ]),
      "errors",
    );
    expect(finding.status).toBe("not_observed");
    expect(finding.statement).toMatch(/Every measurement request succeeded/);
  });

  it("flags p95 far above p50 and needs p95 to exist", () => {
    const tail = diagnoseRun([
      metric("request_latency_ms", 1000, { aggregation: "p50" }),
      metric("request_latency_ms", 3500, { aggregation: "p95" }),
    ]);
    expect(find(tail, "tail_latency").status).toBe("observed");
    expect(find(tail, "tail_latency").statement).toMatch(/3\.5× the median/);

    const small = diagnoseRun([
      metric("request_latency_ms", 1000, { aggregation: "p50", sample_count: 12 }),
      metric("request_latency_ms", null, {
        aggregation: "p95",
        sample_count: 12,
        quality_flags: ["insufficient_samples_for_stable_tail"],
      }),
    ]);
    const finding = find(small, "tail_latency");
    expect(finding.status).toBe("insufficient_data");
    expect(finding.statement).toMatch(/at least 20.*this run has 12/);
  });

  it("flags queue time and mentions concurrency", () => {
    const finding = find(
      diagnoseRun([metric("queue_time_ms", 40)], { concurrency: 4, trafficMode: "open_loop" }),
      "queue_time",
    );
    expect(finding.status).toBe("observed");
    expect(finding.statement).toMatch(/at concurrency 4/);
    const quiet = find(
      diagnoseRun([metric("queue_time_ms", 0)], { concurrency: 4, trafficMode: "closed_loop" }),
      "queue_time",
    );
    expect(quiet.status).toBe("not_observed");
    expect(quiet.statement).toMatch(/closed-loop/);
  });

  it("compares concurrency sweep levels", () => {
    const at = (level: string) => ({ labels: { concurrency_level: level } });
    const metrics = [
      metric("output_throughput_tokens_per_second", 50, { aggregation: "run_rate", ...at("1") }),
      metric("output_throughput_tokens_per_second", 52, { aggregation: "run_rate", ...at("8") }),
      metric("request_latency_ms", 1000, { aggregation: "p50", ...at("1") }),
      metric("request_latency_ms", 7000, { aggregation: "p50", ...at("8") }),
    ];
    const finding = find(diagnoseRun(metrics), "concurrency_scaling");
    expect(finding.status).toBe("observed");
    expect(finding.statement).toMatch(
      /consistent with the engine processing requests largely one at a time/,
    );
    expect(find(diagnoseRun([metric("request_latency_ms", 1)]), "concurrency_scaling").status).toBe(
      "insufficient_data",
    );
  });

  it("flags host memory pressure", () => {
    const finding = find(
      diagnoseRun([
        system("host_memory_used_percent", 93),
        system("host_memory_available_bytes", 512 * 1024 ** 2, "bytes"),
      ]),
      "memory_pressure",
    );
    expect(finding.status).toBe("observed");
    expect(finding.evidence[1]?.unit).toBe("bytes");
  });

  it("splits engine time by phase, ignoring warmup", () => {
    const metrics = [
      engineRaw("ollama.prompt_eval_duration", 900_000_000, "measurement", 1),
      engineRaw("ollama.eval_duration", 100_000_000, "measurement", 2),
      engineRaw("ollama.eval_duration", 9_000_000_000, "warmup", 3),
    ];
    const finding = find(diagnoseRun(metrics), "engine_phases");
    expect(finding.status).toBe("observed");
    expect(finding.statement).toMatch(/prompt evaluation 90%, generation 10%/);
  });

  it("orders observed attention findings first", () => {
    const findings = diagnoseRun([metric("ttft_ms", 800), metric("request_latency_ms", 1000)]);
    expect(findings[0]?.id).toBe("ttft_share");
    const statuses = findings.map((finding) => finding.status);
    expect(statuses.indexOf("insufficient_data")).toBeGreaterThan(statuses.indexOf("observed"));
  });

  it("never uses definitive root-cause wording", () => {
    const findings = diagnoseRun([
      metric("ttft_ms", 800),
      metric("request_latency_ms", 1000),
      metric("queue_time_ms", 40),
    ]);
    for (const finding of findings) {
      expect(finding.statement).not.toMatch(/root cause is|caused by|because of/i);
    }
  });

  it("reads concurrency and traffic mode from a plan snapshot", () => {
    const plan = {
      concurrency: 8,
      configuration_snapshot: { workload: { traffic_mode: "open_loop" } },
    } as unknown as BenchmarkPlan;
    expect(diagnosisContextFromPlan(plan)).toEqual({ concurrency: 8, trafficMode: "open_loop" });
    expect(diagnosisContextFromPlan(undefined)).toEqual({ concurrency: null, trafficMode: null });
  });
});
