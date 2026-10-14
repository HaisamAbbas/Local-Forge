import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  cleanupRuns,
  cloneBenchmarkPlan,
  fetchWorkspaceSettings,
  listEngineModels,
  listEngineProfiles,
  startBenchmarkRun,
  type BenchmarkPlan,
  type BenchmarkPreflight,
  type EngineProfile,
  type PlanCloneResponse,
  type Workload,
} from "../lib/api";
import { RepeatWithChangePanel, RunCleanupControl } from "./RunActions";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    cleanupRuns: vi.fn(),
    cloneBenchmarkPlan: vi.fn(),
    fetchWorkspaceSettings: vi.fn(),
    listEngineModels: vi.fn(),
    listEngineProfiles: vi.fn(),
    startBenchmarkRun: vi.fn(),
  };
});

const plan: BenchmarkPlan = {
  id: "plan-1",
  name: "Baseline plan",
  status: "ready",
  machine_profile_id: null,
  engine_profile_id: "engine-1",
  model_artifact_id: "model-1",
  workload_id: "workload-1",
  warmup_requests: 2,
  measured_requests: 20,
  measurement_duration_seconds: null,
  concurrency: 1,
  timeout_seconds: 120,
  retain_prompt_output: false,
  dataset_snapshot_hash: null,
  configuration_snapshot: {},
};

const workload = {
  id: "workload-1",
  max_output_tokens: 128,
  sampling_config: { temperature: 0.7 },
} as unknown as Workload;

function engine(id: string, label: string, adapter = "ollama"): EngineProfile {
  return { id, label, adapter_id: adapter } as EngineProfile;
}

function preflight(overrides: Partial<BenchmarkPreflight> = {}): BenchmarkPreflight {
  return {
    valid: true,
    target: {},
    workload: null,
    supported_options: [],
    unknown_options: [],
    unsupported_options: [],
    missing_prerequisites: [],
    estimated_duration_seconds: null,
    estimated_request_volume: null,
    resource_contention: [],
    remote_data_egress: null,
    blockers: [],
    warnings: [],
    ...overrides,
  };
}

function cloneResponse(overrides: Partial<PlanCloneResponse> = {}): PlanCloneResponse {
  return {
    plan: { ...plan, id: "plan-2", name: "Baseline plan · concurrency 4", concurrency: 4 },
    preflight: preflight(),
    changed_fields: ["concurrency"],
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(listEngineProfiles).mockResolvedValue([
    engine("engine-1", "Local Ollama"),
    engine("engine-2", "Second engine"),
    engine("engine-fake", "Fixture engine", "localforge.fake"),
  ]);
  vi.mocked(fetchWorkspaceSettings).mockResolvedValue({
    settings: {
      show_test_fixture_engines: false,
      default_retain_prompt_output: false,
      default_request_timeout_seconds: 120,
      default_warmup_count: 2,
    },
    test_fixture_adapter_ids: ["localforge.fake"],
  } as unknown as Awaited<ReturnType<typeof fetchWorkspaceSettings>>);
  vi.mocked(listEngineModels).mockResolvedValue([
    { id: "model-1", display_name: "Llama small", revision: null },
    { id: "model-2", display_name: "Qwen large", revision: "q4" },
  ] as Awaited<ReturnType<typeof listEngineModels>>);
});

afterEach(() => cleanup());

describe("run clean up", () => {
  it("previews with a dry run, then deletes the same filter on confirm", async () => {
    vi.mocked(cleanupRuns)
      .mockResolvedValueOnce({ run_ids: ["a", "b"], count: 2, dry_run: true })
      .mockResolvedValueOnce({ run_ids: ["a", "b"], count: 2, dry_run: false });
    const onCleaned = vi.fn();
    render(<RunCleanupControl onCleaned={onCleaned} />);

    fireEvent.click(screen.getByRole("button", { name: "Clean up" }));
    fireEvent.change(screen.getByLabelText("Age"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Keep latest"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview clean up" }));

    expect(await screen.findByText(/2 finished runs will be deleted permanently/)).toBeVisible();
    const previewCall = vi.mocked(cleanupRuns).mock.calls[0]![0];
    expect(previewCall).toMatchObject({ statuses: null, keep_latest: 3, dry_run: true });
    const ageMs = Date.now() - Date.parse(previewCall.before!);
    expect(Math.round(ageMs / 86_400_000)).toBe(7);

    fireEvent.click(screen.getByRole("button", { name: "Delete 2 runs" }));
    await waitFor(() => expect(onCleaned).toHaveBeenCalledWith(["a", "b"]));
    expect(vi.mocked(cleanupRuns).mock.calls[1]![0]).toEqual({ ...previewCall, dry_run: false });
    expect(screen.getByText("Deleted 2 finished runs.")).toBeVisible();
  });

  it("requires a filter and reports when nothing matches", async () => {
    vi.mocked(cleanupRuns).mockResolvedValue({ run_ids: [], count: 0, dry_run: true });
    render(<RunCleanupControl onCleaned={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Clean up" }));
    fireEvent.change(screen.getByLabelText("Age"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Preview clean up" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Age"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview clean up" }));
    expect(await screen.findByText(/No finished runs match/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
  });
});

describe("repeat with one change", () => {
  it("clones with one numeric change, shows preflight, and starts a run", async () => {
    vi.mocked(cloneBenchmarkPlan).mockResolvedValue(
      cloneResponse({
        preflight: preflight({
          warnings: [{ code: "high_concurrency", message: "Concurrency is high." }],
        }),
      }),
    );
    vi.mocked(startBenchmarkRun).mockResolvedValue({
      run: { id: "run-new" } as Awaited<ReturnType<typeof startBenchmarkRun>>["run"],
      conflicting_resources: [],
    });
    const onStarted = vi.fn();
    render(
      <RepeatWithChangePanel
        onClose={vi.fn()}
        onStarted={onStarted}
        plan={plan}
        workload={workload}
      />,
    );

    expect(screen.getByText(/Current value:/)).toHaveTextContent("Current value: 1");
    fireEvent.change(screen.getByLabelText("New concurrency"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changed plan" }));
    expect(await screen.findByText(/different value/)).toBeVisible();
    expect(cloneBenchmarkPlan).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("New concurrency"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changed plan" }));
    expect(await screen.findByText(/Concurrency is high/)).toBeVisible();
    expect(cloneBenchmarkPlan).toHaveBeenCalledWith("plan-1", {
      name: null,
      changes: { concurrency: 4 },
    });
    expect(screen.getByText(/changed concurrency/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("run-new"));
    expect(startBenchmarkRun).toHaveBeenCalledWith("plan-2", false);
  });

  it("shows workload values and clears temperature with a blank value", async () => {
    vi.mocked(cloneBenchmarkPlan).mockResolvedValue(cloneResponse());
    render(
      <RepeatWithChangePanel
        onClose={vi.fn()}
        onStarted={vi.fn()}
        plan={plan}
        workload={workload}
      />,
    );
    fireEvent.change(screen.getByLabelText("Setting to change"), {
      target: { value: "temperature" },
    });
    expect(screen.getByText(/Current value:/)).toHaveTextContent("0.7");
    fireEvent.change(screen.getByLabelText("Name for the new plan (optional)"), {
      target: { value: "Greedy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changed plan" }));
    await waitFor(() =>
      expect(cloneBenchmarkPlan).toHaveBeenCalledWith("plan-1", {
        name: "Greedy",
        changes: { temperature: null },
      }),
    );
  });

  it("picks a model from the engine's models and blocks start on preflight blockers", async () => {
    vi.mocked(cloneBenchmarkPlan).mockResolvedValue(
      cloneResponse({
        plan: { ...plan, id: "plan-3", status: "blocked" },
        preflight: preflight({
          valid: false,
          blockers: [{ code: "model_unavailable", message: "The model is not loaded." }],
        }),
        changed_fields: ["model_artifact_id"],
      }),
    );
    render(
      <RepeatWithChangePanel
        onClose={vi.fn()}
        onStarted={vi.fn()}
        plan={plan}
        workload={workload}
      />,
    );
    fireEvent.change(screen.getByLabelText("Setting to change"), {
      target: { value: "model_artifact_id" },
    });
    await screen.findByRole("option", { name: "Qwen large · q4" });
    expect(screen.getByText(/Current value:/)).toHaveTextContent("Llama small");
    expect(listEngineModels).toHaveBeenCalledWith("engine-1");
    fireEvent.change(screen.getByLabelText("New model"), { target: { value: "model-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changed plan" }));
    expect(await screen.findByText(/The model is not loaded/)).toBeVisible();
    expect(cloneBenchmarkPlan).toHaveBeenCalledWith("plan-1", {
      name: null,
      changes: { model_artifact_id: "model-2" },
    });
    expect(screen.queryByRole("button", { name: "Start run" })).toBeNull();
  });

  it("hides fixture engines from the engine picker and maps API field errors", async () => {
    vi.mocked(cloneBenchmarkPlan).mockRejectedValue(
      new ApiError("Invalid", 422, null, null, { engine_profile_id: "Engine is disabled." }),
    );
    render(
      <RepeatWithChangePanel
        onClose={vi.fn()}
        onStarted={vi.fn()}
        plan={plan}
        workload={workload}
      />,
    );
    fireEvent.change(screen.getByLabelText("Setting to change"), {
      target: { value: "engine_profile_id" },
    });
    await screen.findByRole("option", { name: "Second engine" });
    expect(screen.queryByRole("option", { name: "Fixture engine" })).toBeNull();
    fireEvent.change(screen.getByLabelText("New engine profile"), {
      target: { value: "engine-2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changed plan" }));
    expect(await screen.findByText("Engine is disabled.")).toBeVisible();
  });
});
