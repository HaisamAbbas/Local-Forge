import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteRun,
  fetchWorkspaceSettings,
  getRun,
  listBenchmarkPlans,
  listEngineProfiles,
  listMachineProfiles,
  listRunEvents,
  listRunMetrics,
  listRuns,
  listRunSamples,
  listWorkloads,
  type BenchmarkPlan,
  type BenchmarkRun,
} from "../lib/api";
import { RunsScreen } from "./RunsScreen";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    deleteRun: vi.fn(),
    fetchWorkspaceSettings: vi.fn(),
    getRun: vi.fn(),
    listBenchmarkPlans: vi.fn(),
    listEngineProfiles: vi.fn(),
    listMachineProfiles: vi.fn(),
    listRunEvents: vi.fn(),
    listRunMetrics: vi.fn(),
    listRuns: vi.fn(),
    listRunSamples: vi.fn(),
    listWorkloads: vi.fn(),
  };
});

function run(overrides: Partial<BenchmarkRun> = {}): BenchmarkRun {
  return {
    id: "run-1-aaaa",
    plan_id: "plan-1",
    status: "completed",
    worker_id: null,
    potentially_contended: false,
    started_at: "2026-10-01T00:00:00Z",
    completed_at: "2026-10-01T00:01:00Z",
    error_code: null,
    error_message: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:01:00Z",
    ...overrides,
  };
}

const plan = {
  id: "plan-1",
  name: "Baseline plan",
  status: "ready",
  engine_profile_id: "engine-1",
  machine_profile_id: null,
  model_artifact_id: "model-1",
  workload_id: "workload-1",
  warmup_requests: 0,
  measured_requests: 2,
  measurement_duration_seconds: null,
  concurrency: 1,
  timeout_seconds: 120,
  retain_prompt_output: false,
  dataset_snapshot_hash: null,
  configuration_snapshot: {},
} as BenchmarkPlan;

beforeEach(() => {
  vi.mocked(listRuns).mockResolvedValue([run()]);
  vi.mocked(listBenchmarkPlans).mockResolvedValue([plan]);
  vi.mocked(getRun).mockResolvedValue(run());
  vi.mocked(listRunMetrics).mockResolvedValue({ metrics: [] });
  vi.mocked(listRunEvents).mockResolvedValue({
    events: [],
    next_sequence_number: null,
    terminal: true,
  });
  vi.mocked(listRunSamples).mockResolvedValue({ samples: [], next_sequence_number: null });
  vi.mocked(listEngineProfiles).mockResolvedValue([]);
  vi.mocked(listMachineProfiles).mockResolvedValue([]);
  vi.mocked(listWorkloads).mockResolvedValue([]);
  vi.mocked(fetchWorkspaceSettings).mockRejectedValue(new Error("offline"));
});

afterEach(() => cleanup());

describe("run detail deletion", () => {
  it("confirms inline, deletes, and clears the selection", async () => {
    vi.mocked(deleteRun).mockResolvedValue(undefined);
    const onSelectRun = vi.fn();
    render(<RunsScreen onSelectRun={onSelectRun} selectedRunId="run-1-aaaa" />);

    fireEvent.click(await screen.findByRole("button", { name: "Delete run" }));
    expect(screen.getByText(/This cannot be undone/)).toBeVisible();
    expect(deleteRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith(null));
    expect(deleteRun).toHaveBeenCalledWith("run-1-aaaa");
  });

  it("explains a run_active conflict and keeps the run selected", async () => {
    vi.mocked(deleteRun).mockRejectedValue(new ApiError("Conflict", 409, "run_active"));
    const onSelectRun = vi.fn();
    render(<RunsScreen onSelectRun={onSelectRun} selectedRunId="run-1-aaaa" />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete run" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByText(/This run is still active/)).toBeVisible();
    expect(onSelectRun).not.toHaveBeenCalled();
  });

  it("opens the repeat-with-one-change form once the plan loads", async () => {
    vi.mocked(getRun).mockResolvedValue(run({ status: "completed" }));
    render(<RunsScreen onSelectRun={vi.fn()} selectedRunId="run-1-aaaa" />);
    fireEvent.click(await screen.findByRole("button", { name: "Repeat with one change" }));
    expect(screen.getByRole("heading", { name: "Repeat with one change" })).toBeVisible();
    expect(screen.getByLabelText("Setting to change")).toBeVisible();
  });
});
