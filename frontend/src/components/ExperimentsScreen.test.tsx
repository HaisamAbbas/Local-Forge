import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  compareRuns,
  fetchWorkspaceSettings,
  listBenchmarkPlans,
  listEngineProfiles,
  listExperimentGroups,
  listRuns,
  type BenchmarkPlan,
  type BenchmarkRun,
  type EngineProfile,
  type RunComparison,
  type WorkspaceSettingsResponse,
} from "../lib/api";
import { ExperimentsScreen } from "./ExperimentsScreen";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    compareRuns: vi.fn(),
    fetchWorkspaceSettings: vi.fn(),
    listBenchmarkPlans: vi.fn(),
    listEngineProfiles: vi.fn(),
    listExperimentGroups: vi.fn(),
    listRuns: vi.fn(),
  };
});

const engines = [
  { id: "engine-real", label: "Ollama", adapter_id: "ollama" },
  { id: "engine-fake", label: "Fixture", adapter_id: "localforge.fake" },
] as EngineProfile[];

function plan(id: string, name: string, engineId: string): BenchmarkPlan {
  return {
    id,
    name,
    status: "ready",
    engine_profile_id: engineId,
    machine_profile_id: null,
    model_artifact_id: null,
    workload_id: "workload-1",
    warmup_requests: 0,
    measured_requests: 10,
    measurement_duration_seconds: null,
    concurrency: 1,
    timeout_seconds: 120,
    retain_prompt_output: false,
    dataset_snapshot_hash: null,
    configuration_snapshot: {},
  };
}

function run(id: string, planId: string): BenchmarkRun {
  return {
    id,
    plan_id: planId,
    status: "completed",
    worker_id: null,
    potentially_contended: false,
    started_at: "2026-10-01T00:00:00Z",
    completed_at: "2026-10-01T00:01:00Z",
    error_code: null,
    error_message: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:01:00Z",
  };
}

function settings(show: boolean): WorkspaceSettingsResponse {
  return {
    settings: {
      show_test_fixture_engines: show,
      default_retain_prompt_output: false,
      default_request_timeout_seconds: 120,
      default_warmup_count: 2,
    },
    test_fixture_adapter_ids: ["localforge.fake"],
  } as unknown as WorkspaceSettingsResponse;
}

function sharedMetric(
  name: string,
  aggregation: string,
  unit: string,
  values: Record<string, number | null>,
) {
  return {
    metric_name: name,
    unit,
    source: "client_measured",
    aggregation,
    definition: name,
    formula: name,
    method_version: "1",
    metric_kind: "gauge",
    labels: {},
    baseline_run_id: "run-a",
    runs: Object.fromEntries(
      Object.entries(values).map(([id, value]) => [
        id,
        { value, sample_count: 10, record_count: 1 },
      ]),
    ),
    observed_deltas: {},
  };
}

const comparison = {
  run_ids: ["run-a", "run-b", "run-c"],
  baseline_run_id: "run-a",
  declared_variable: null,
  grade: "B",
  label: "Mostly controlled",
  rationale: "One change.",
  controlled_comparison_badge: false,
  causal_interpretation_allowed: false,
  dimensions: [],
  workload_changes: [],
  excluded_metrics: [],
  shared_metrics: [
    sharedMetric("output_throughput_tokens_per_second", "run_rate", "tokens/s", {
      "run-a": 40,
      "run-b": 20,
      "run-c": 30,
    }),
    sharedMetric("request_latency_ms", "p95", "ms", { "run-a": 900, "run-b": 950, "run-c": 500 }),
    sharedMetric("ttft_ms", "p95", "ms", { "run-a": 80, "run-b": 70, "run-c": null }),
  ],
  run_statuses: {},
} as unknown as RunComparison;

beforeEach(() => {
  vi.mocked(listEngineProfiles).mockResolvedValue(engines);
  vi.mocked(listBenchmarkPlans).mockResolvedValue([
    plan("plan-a", "Real A", "engine-real"),
    plan("plan-b", "Real B", "engine-real"),
    plan("plan-c", "Real C", "engine-real"),
    plan("plan-fake", "Fake plan", "engine-fake"),
  ]);
  vi.mocked(listRuns).mockResolvedValue([
    run("run-a", "plan-a"),
    run("run-b", "plan-b"),
    run("run-c", "plan-c"),
    run("run-fake", "plan-fake"),
  ]);
  vi.mocked(listExperimentGroups).mockResolvedValue([]);
  vi.mocked(compareRuns).mockResolvedValue(comparison);
});

afterEach(() => cleanup());

describe("experiments test-fixture filtering", () => {
  it("hides fixture plans and runs when Settings hides fixture engines", async () => {
    vi.mocked(fetchWorkspaceSettings).mockResolvedValue(settings(false));
    render(<ExperimentsScreen />);
    await screen.findByRole("heading", { name: "Compare completed runs" });
    expect(screen.queryByText("Fake plan")).toBeNull();
    expect(screen.getAllByRole("checkbox", { name: /Real/ })).toHaveLength(3);
    const baseline = screen.getByLabelText("Baseline plan");
    expect(within(baseline).queryByRole("option", { name: "Fake plan" })).toBeNull();
    expect(screen.getByText(/1 plan\(s\) and their runs use test-fixture engines/)).toBeVisible();
  });

  it("shows fixtures when Settings enables them", async () => {
    vi.mocked(fetchWorkspaceSettings).mockResolvedValue(settings(true));
    render(<ExperimentsScreen />);
    await screen.findByRole("heading", { name: "Compare completed runs" });
    expect(screen.getAllByText("Fake plan").length).toBeGreaterThan(0);
  });
});

describe("latency vs throughput chart", () => {
  it("plots compared runs, marks the frontier and notes omitted runs", async () => {
    vi.mocked(fetchWorkspaceSettings).mockResolvedValue(settings(false));
    render(<ExperimentsScreen />);
    for (const name of ["Real A", "Real B", "Real C"])
      fireEvent.click(await screen.findByRole("checkbox", { name: new RegExp(name) }));
    fireEvent.click(screen.getByRole("button", { name: "Compare 3" }));

    const figure = await screen.findByRole("figure", { name: "Latency vs throughput" });
    expect(within(figure).getByRole("img")).toHaveAttribute(
      "aria-label",
      expect.stringContaining("3 runs, 2 Pareto-efficient"),
    );
    const rows = within(figure).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringMatching(/^Real B.*20.*950.*No$/),
      expect.stringMatching(/^Real C.*30.*500.*Yes$/),
      expect.stringMatching(/^Real A \(baseline\).*40.*900.*Yes$/),
    ]);

    fireEvent.change(screen.getByLabelText("Latency axis"), { target: { value: "ttft_ms" } });
    expect(within(figure).getByText(/Not plotted: Real C/)).toBeVisible();
    expect(within(figure).getByRole("img")).toHaveAttribute(
      "aria-label",
      expect.stringContaining("2 runs, 2 Pareto-efficient"),
    );
  });
});
