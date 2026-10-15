import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BenchmarkRun, RunComparison } from "../lib/api";
import { keySpreadMetrics } from "../lib/run-conditions";
import { RepeatLaunchControl } from "./RepeatLaunchControl";
import { ComparisonConditions, RunConditionsCard } from "./RunConditions";

const SHA = "29d8c98fa6b098e200069bfb88b9508dc3e85586d20cba59f8dda9a808165104";

function field(value: unknown, source = "process_command_line") {
  return value === null
    ? { value: null, source: null, status: "not_reported" as const }
    : { value, source };
}

function run(snapshot: Record<string, unknown>): BenchmarkRun {
  return {
    id: "run-1",
    plan_id: "plan-1",
    status: "completed",
    worker_id: null,
    potentially_contended: false,
    profile_snapshot: snapshot as BenchmarkRun["profile_snapshot"],
    started_at: null,
    completed_at: null,
    error_code: null,
    error_message: null,
    created_at: "2026-10-14T00:00:00Z",
    updated_at: "2026-10-14T00:00:00Z",
  };
}

const reading = {
  platform: "windows",
  captured_at: "2026-10-14T00:00:00Z",
  ac_power: false,
  battery_percent: 65,
  battery_present: true,
  battery_saver: false,
  power_plan: "Balanced",
  power_mode: "Best performance",
  platform_profile: null,
  cpu_governor: null,
  low_power_mode: null,
  sources: ["GetSystemPowerStatus"],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RunConditionsCard", () => {
  it("shows settings with sources, the weights hash, power and a cache warning", () => {
    render(
      <RunConditionsCard
        run={run({
          runtime_settings: {
            schema_version: 1,
            adapter_id: "localforge.koboldcpp",
            fields: {
              threads: field(6),
              gpu_layers: field(null),
              context_size: field(4096, "koboldcpp_props"),
              gpu_offload_share: { value: 0.25, source: "ollama_api_ps", observed: "after_run" },
            },
            notes: ["Settings not given on the engine's command line use the engine's defaults."],
          },
          model_identity: {
            sha256: SHA,
            basis: "file_hash",
            size_bytes: 986048576,
            file_name: "qwen2.5-coder-1.5b-q4_k_m.gguf",
          },
          power_state: {
            start: reading,
            end: { ...reading, ac_power: true },
            changed_during_run: true,
            changed_fields: ["ac_power"],
          },
          prompt_cache: {
            code: "prompt_cache_likely",
            affected_requests: 2,
            message: "2 measured request(s) were likely answered from the engine's prompt cache.",
          },
        })}
      />,
    );

    expect(screen.getByRole("row", { name: /CPU threads 6 engine command line/ })).toBeVisible();
    expect(screen.getByRole("row", { name: /GPU layers Not reported/ })).toBeVisible();
    expect(screen.getByRole("row", { name: /Context size 4096 KoboldCpp \/props/ })).toBeVisible();
    expect(screen.getByText("25%")).toBeVisible();
    expect(screen.getByText("Ollama /api/ps (read after the run)")).toBeVisible();
    expect(screen.getByText("29d8c98fa6b0…")).toBeVisible();
    expect(screen.getByText(/On battery \(65%\) · Best performance · Balanced plan/)).toBeVisible();
    expect(screen.getByText(/Power changed during this run/)).toBeVisible();
    expect(screen.getByText(/Prompt cache likely/)).toBeVisible();
  });

  it("explains runs recorded before conditions were captured", () => {
    render(<RunConditionsCard run={run({ captured_at: "2026-01-01T00:00:00Z" })} />);
    expect(screen.getByText(/recorded before run conditions were captured/)).toBeVisible();
  });
});

describe("ComparisonConditions", () => {
  it("states the thread mismatch and verified weights in plain language", () => {
    const comparison = {
      run_ids: ["a", "b"],
      baseline_run_id: "a",
      declared_variable: null,
      grade: "not_comparable",
      label: "Not comparable",
      rationale: "",
      controlled_comparison_badge: false,
      causal_interpretation_allowed: false,
      confounders: [
        "These runs also differ in CPU threads (6 vs 12); the speed difference may come from that, not the engine.",
      ],
      dimensions: [
        {
          key: "runtime_settings",
          label: "Engine runtime settings",
          state: "mismatch",
          values: [],
          summary: "CPU threads (6 vs 12)",
        },
        {
          key: "model_weights",
          label: "Model weights",
          state: "verified_identical",
          values: [],
          digests: [SHA, SHA],
        },
        { key: "power_state", label: "Power state", state: "unknown", values: [] },
      ],
      workload_changes: [],
      excluded_metrics: [],
      shared_metrics: [],
      run_statuses: {},
    } as unknown as RunComparison;
    render(<ComparisonConditions comparison={comparison} />);

    expect(
      screen.getByText(/differ in CPU threads \(6 vs 12\); the speed difference/),
    ).toBeVisible();
    expect(screen.getByText("Differs in CPU threads (6 vs 12).")).toBeVisible();
    expect(screen.getByText("Verified identical")).toBeVisible();
    expect(screen.getByText("29d8c98fa6b0… · 29d8c98fa6b0…")).toBeVisible();
    expect(screen.getByText("Power state was not captured for every run.")).toBeVisible();
  });
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RepeatLaunchControl", () => {
  it("repeats a plan, waits for each run, and shows median, range and CV", async () => {
    const group = {
      id: "group-1",
      name: "Plan — repeated ×2",
      status: "draft",
      run_ids: [],
      description: null,
      baseline_plan_id: "plan-1",
      independent_variable: null,
      baseline_value: null,
      variants: [],
      repetitions: 2,
      randomize_order: false,
      order_seed: 0,
      estimated_duration_seconds: 20,
      trials: [],
    };
    let launched = 0;
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method ?? "GET").toUpperCase();
        calls.push(`${method} ${url}`);
        if (url === "/api/v1/session") return json({ token: "t" });
        if (url === "/api/v1/benchmark-plans/plan-1/repeat") {
          expect(JSON.parse(String(init?.body))).toEqual({ count: 2 });
          return json({ group, estimated_run_count: 2, estimated_duration_seconds: 20 }, 201);
        }
        if (url === "/api/v1/experiments/group-1/start")
          return json({ ...group, status: "running" });
        if (url === "/api/v1/experiments/group-1/next") {
          launched += 1;
          return json({
            group: { ...group, status: "running" },
            run: launched <= 2 ? { ...run({}), id: `run-${launched}` } : null,
          });
        }
        if (url === "/api/v1/experiments/group-1")
          return json({ ...group, status: launched >= 2 ? "completed" : "running" });
        if (url === "/api/v1/experiments/group-1/summary") {
          return json({
            variants: [
              {
                name: "Baseline",
                metrics: [
                  {
                    metric_name: "ttft_ms",
                    aggregation: "p50",
                    unit: "ms",
                    median: 120,
                    spread_min: 100,
                    spread_max: 140,
                    repeat_count: 2,
                    cv: 0.236,
                    unstable: true,
                  },
                ],
              },
            ],
          });
        }
        return json({ detail: { message: `Unexpected ${url}` } }, 500);
      }),
    );
    const started: string[] = [];
    render(<RepeatLaunchControl planId="plan-1" onRunStarted={(item) => started.push(item.id)} />);
    fireEvent.change(screen.getByLabelText("Repeat count"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Run 2 times" }));

    expect(await screen.findByText(/Finished 2 repeats/)).toBeVisible();
    expect(started).toEqual(["run-1", "run-2"]);
    expect(
      screen.getByRole("row", { name: /TTFT p50\s*ms 120 100–140 CV 23.6% · unstable/ }),
    ).toBeVisible();
    expect(screen.getByRole("row", { name: /Latency p50 Unavailable/ })).toBeVisible();
    await waitFor(() => expect(calls).toContain("POST /api/v1/experiments/group-1/start"));
  });

  it("selects the three key metrics for the spread table", () => {
    const rows = keySpreadMetrics([]);
    expect(rows.map((row) => row.label)).toEqual(["TTFT p50", "Latency p50", "Generation tok/s"]);
  });
});
