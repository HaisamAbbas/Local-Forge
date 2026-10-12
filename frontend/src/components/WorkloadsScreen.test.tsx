import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createBenchmarkPlan,
  createWorkload,
  importDataset,
  listBenchmarkPlans,
  listDatasets,
  listEngineModels,
  listEngineProfiles,
  listMachineProfiles,
  listWorkloads,
  preflightBenchmarkPlan,
  preflightSavedPlan,
  startBenchmarkRun,
  type BenchmarkPlanResponse,
  type BenchmarkPreflight,
  type DatasetImportResponse,
  type EngineModel,
  type EngineProfile,
  type Workload,
} from "../lib/api";
import { WorkloadsScreen } from "./WorkloadsScreen";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    createBenchmarkPlan: vi.fn(),
    createWorkload: vi.fn(),
    enableEngineProfile: vi.fn(),
    importDataset: vi.fn(),
    listBenchmarkPlans: vi.fn(),
    listDatasets: vi.fn(),
    listEngineModels: vi.fn(),
    listEngineProfiles: vi.fn(),
    listMachineProfiles: vi.fn(),
    listWorkloads: vi.fn(),
    preflightBenchmarkPlan: vi.fn(),
    preflightSavedPlan: vi.fn(),
    replayWorkload: vi.fn(),
    startBenchmarkRun: vi.fn(),
  };
});

const engine: EngineProfile = {
  id: "engine-1",
  label: "Local fixture",
  adapter_id: "fake",
  engine_display_name: "Deterministic Fake Engine",
  adapter_version: "1.0.0",
  integration_mode: "attached_endpoint",
  status: "enabled",
  endpoint_url: "http://127.0.0.1:8080",
  is_remote: false,
  remote_access_enabled: false,
  engine_version: "fake-1",
  last_probed_at: "2026-01-01T00:00:00Z",
  capability_snapshot_id: "capability-1",
  capabilities: null,
  machine_profile_id: "machine-1",
  selected_model_ref: "fake://model",
  user_notes: null,
  configured_fields: [],
  credential_configured: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  schema_version: 1,
};

const model: EngineModel = {
  id: "model-1",
  engine_profile_id: engine.id,
  model_ref: "fake://model",
  display_name: "Fixture model",
  revision: "1",
  format: null,
  size_bytes: null,
  tokenizer_identity: "fixture-tokenizer",
  max_context_tokens: 4096,
  status: "available",
};

const workload: Workload = {
  id: "workload-1",
  name: "My benchmark plan",
  kind: "chat",
  status: "ready",
  input_mode: "synthetic",
  dataset_id: null,
  preset: "mixed_chat",
  request_count: 20,
  duration_seconds: null,
  concurrency: 1,
  concurrency_levels: [],
  traffic_mode: "closed_loop",
  arrival_rate_per_second: null,
  warmup_count: 2,
  target_input_tokens: 128,
  target_output_tokens: 64,
  max_output_tokens: 64,
  prompt_sizing_method: "characters",
  random_seed: 0,
  sampling_config: {},
};

function preflight(overrides: Partial<BenchmarkPreflight> = {}): BenchmarkPreflight {
  return {
    valid: true,
    target: {
      engine_label: engine.label,
      model_name: model.display_name,
      model_context_limit: model.max_context_tokens,
      machine_label: "This machine",
      endpoint: engine.endpoint_url,
      workload_name: workload.name,
    },
    workload: null,
    supported_options: ["chat_completion"],
    unknown_options: [],
    unsupported_options: [],
    missing_prerequisites: [],
    estimated_duration_seconds: 4,
    estimated_request_volume: 22,
    resource_contention: [],
    remote_data_egress: null,
    blockers: [],
    warnings: [],
    ...overrides,
  };
}

const planResponse: BenchmarkPlanResponse = {
  plan: {
    id: "plan-1",
    name: workload.name,
    status: "ready",
    machine_profile_id: "machine-1",
    engine_profile_id: engine.id,
    model_artifact_id: model.id,
    workload_id: workload.id,
    warmup_requests: 2,
    measured_requests: 20,
    measurement_duration_seconds: null,
    concurrency: 1,
    timeout_seconds: 120,
    retain_prompt_output: false,
    dataset_snapshot_hash: null,
    configuration_snapshot: {},
  },
  preflight: preflight(),
};

beforeEach(() => {
  vi.mocked(listEngineProfiles).mockResolvedValue([engine]);
  vi.mocked(listMachineProfiles).mockResolvedValue([]);
  vi.mocked(listDatasets).mockResolvedValue([]);
  vi.mocked(listWorkloads).mockResolvedValue([]);
  vi.mocked(listBenchmarkPlans).mockResolvedValue([]);
  vi.mocked(listEngineModels).mockResolvedValue([model]);
  vi.mocked(createWorkload).mockResolvedValue(workload);
  vi.mocked(preflightBenchmarkPlan).mockResolvedValue(preflight());
  vi.mocked(preflightSavedPlan).mockResolvedValue(preflight());
  vi.mocked(createBenchmarkPlan).mockResolvedValue(planResponse);
  vi.mocked(startBenchmarkRun).mockResolvedValue({
    run: {
      id: "run-1",
      plan_id: "plan-1",
      status: "queued",
      worker_id: "worker-1",
      potentially_contended: false,
      started_at: null,
      completed_at: null,
      error_code: null,
      error_message: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    conflicting_resources: [],
  });
});

afterEach(() => cleanup());

async function chooseReadyTarget() {
  await screen.findByRole("option", { name: /Fixture model/ });
  await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue(model.id));
}

describe("workload builder and plan review", () => {
  it("submits closed-loop concurrency and saves then launches a valid plan", async () => {
    render(<WorkloadsScreen />);
    await chooseReadyTarget();

    fireEvent.change(screen.getByLabelText("Virtual-client concurrency"), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByLabelText("Synthetic prompt seed"), {
      target: { value: "23" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review preflight" }));

    await screen.findByRole("heading", { name: "Preflight review" });
    const sentWorkload = vi.mocked(createWorkload).mock.calls[0]?.[0];
    expect(sentWorkload).toMatchObject({
      traffic_mode: "closed_loop",
      concurrency: 3,
      arrival_rate_per_second: null,
      random_seed: 23,
      input_mode: "synthetic",
      target_input_tokens: 512,
      target_output_tokens: 128,
    });
    expect(screen.getByText("22 requests")).toBeInTheDocument();
    expect(screen.getByText("4 seconds")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await screen.findByRole("button", { name: "Launch benchmark" });
    fireEvent.click(screen.getByRole("button", { name: "Launch benchmark" }));
    expect(await screen.findByText("run-1")).toBeInTheDocument();
    expect(startBenchmarkRun).toHaveBeenCalledWith("plan-1", false);
  });

  it("sends an open-loop rate with its in-flight concurrency and shows server warnings", async () => {
    vi.mocked(createWorkload).mockResolvedValue({
      ...workload,
      traffic_mode: "open_loop",
      preset: "target_request_rate",
    });
    vi.mocked(preflightBenchmarkPlan).mockResolvedValue(
      preflight({
        warnings: [{ code: "context_limit_unknown", message: "Model context limit is unknown." }],
        unknown_options: ["model_context_limit"],
        estimated_duration_seconds: 10,
      }),
    );
    render(<WorkloadsScreen />);
    await chooseReadyTarget();

    fireEvent.change(screen.getByLabelText("Workload template"), {
      target: { value: "target_request_rate" },
    });
    fireEvent.change(screen.getByLabelText("Maximum in-flight requests"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review preflight" }));
    await screen.findByRole("heading", { name: "Preflight review" });

    expect(vi.mocked(createWorkload).mock.calls[0]?.[0]).toMatchObject({
      traffic_mode: "open_loop",
      arrival_rate_per_second: 5,
      preset: "target_request_rate",
      concurrency: 3,
    });
    expect(screen.getByText(/Model context limit is unknown/)).toBeInTheDocument();
    expect(screen.getByText(/model_context_limit/)).toBeInTheDocument();
    expect(screen.getByText("10 seconds")).toBeInTheDocument();
  });

  it("omits an optional output target while keeping a valid default output limit", async () => {
    render(<WorkloadsScreen />);
    await chooseReadyTarget();
    fireEvent.change(screen.getByLabelText("Output token target and maximum"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review preflight" }));

    await screen.findByRole("heading", { name: "Preflight review" });
    expect(vi.mocked(createWorkload).mock.calls[0]?.[0]).toMatchObject({
      target_output_tokens: null,
      max_output_tokens: 256,
    });
  });

  it("blocks a known context-limit exceedance from launching", async () => {
    const blockedPreflight = preflight({
      valid: false,
      blockers: [
        {
          code: "context_limit_exceeded",
          message: "Requested prompt and output exceed the known model context limit.",
        },
      ],
    });
    vi.mocked(preflightBenchmarkPlan).mockResolvedValue(blockedPreflight);
    vi.mocked(createBenchmarkPlan).mockResolvedValue({
      ...planResponse,
      plan: { ...planResponse.plan, status: "blocked" },
      preflight: blockedPreflight,
    });
    render(<WorkloadsScreen />);
    await chooseReadyTarget();
    fireEvent.click(screen.getByRole("button", { name: "Review preflight" }));
    await screen.findByText(/exceed the known model context limit/);
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    const launchButton = await screen.findByRole("button", { name: "Launch benchmark" });
    expect(launchButton).toBeDisabled();
    expect(startBenchmarkRun).not.toHaveBeenCalled();
  });

  it("keeps form values and presents field-associated messages after server validation fails", async () => {
    vi.mocked(createWorkload).mockRejectedValue(
      new ApiError("Some workload settings need attention.", 422, null, null, {
        arrival_rate_per_second: "Enter a positive target rate.",
      }),
    );
    render(<WorkloadsScreen />);
    await chooseReadyTarget();
    fireEvent.change(screen.getByLabelText("Workload template"), {
      target: { value: "target_request_rate" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review preflight" }));

    const message = await screen.findByText("Enter a positive target rate.", {
      selector: ".field-error",
    });
    expect(message).toHaveAttribute("id", "error-arrival-rate");
    expect(screen.getByLabelText("Target request rate per second")).toHaveValue(5);
    expect(
      screen.getByRole("heading", { name: "Build a repeatable workload" }),
    ).toBeInTheDocument();
  });

  it("uses the redacted dataset preview and surfaces a blocker without launching", async () => {
    const imported: DatasetImportResponse = {
      dataset: {
        id: "dataset-1",
        name: "Private prompts",
        source: "imported_file",
        status: "ready",
        item_count: 1,
        content_hash: "source-hash",
        snapshot_hash: null,
        content_retained: false,
        format: "jsonl",
        prompt_field: "prompt",
        expected_answer_field: null,
        row_limit: 10000,
        shuffle_seed: 0,
        prompt_sizing_method: "characters",
        tokenizer_identity: null,
        count_method: "characters",
        duplicate_count: 0,
        redacted_summary: "1 source row; prompt content was not retained.",
      },
      preview: [
        {
          row_number: 1,
          prompt_preview: "[redacted prompt, 24 characters]",
          prompt_size: 24,
          expected_answer_present: false,
        },
      ],
      parsed_rows: 1,
      selected_rows: 1,
      duplicate_rows: 0,
      rows_excluded_by_limit: 0,
      count_method: "characters",
    };
    vi.mocked(importDataset).mockResolvedValue(imported);
    vi.mocked(createWorkload).mockResolvedValue({
      ...workload,
      input_mode: "dataset",
      dataset_id: "dataset-1",
    });
    vi.mocked(preflightBenchmarkPlan).mockResolvedValue(
      preflight({
        valid: false,
        blockers: [
          {
            code: "dataset_content_not_retained",
            message: "The imported dataset cannot be replayed.",
          },
        ],
      }),
    );
    vi.mocked(createBenchmarkPlan).mockResolvedValue({
      ...planResponse,
      plan: { ...planResponse.plan, status: "blocked" },
      preflight: preflight({
        valid: false,
        blockers: [
          {
            code: "dataset_content_not_retained",
            message: "The imported dataset cannot be replayed.",
          },
        ],
      }),
    });
    render(<WorkloadsScreen />);
    await chooseReadyTarget();
    fireEvent.change(screen.getByLabelText("Request source"), { target: { value: "dataset" } });
    fireEvent.change(screen.getByLabelText(/Select a JSONL or CSV file/), {
      target: {
        files: [
          new File(['{"prompt":"private prompt"}'], "private.jsonl", { type: "application/jsonl" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review preflight" }));

    await screen.findByText(/redacted prompt, 24 characters/);
    expect(screen.queryByText("private prompt")).not.toBeInTheDocument();
    await screen.findByRole("heading", { name: "Preflight review" });
    expect(screen.getByText(/cannot be replayed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await screen.findByRole("button", { name: "Launch benchmark" });
    expect(screen.getByRole("button", { name: "Launch benchmark" })).toBeDisabled();
    expect(startBenchmarkRun).not.toHaveBeenCalled();
  });

  it("re-preflights and launches a saved plan without creating a duplicate plan", async () => {
    vi.mocked(listBenchmarkPlans).mockResolvedValue([planResponse.plan]);
    render(<WorkloadsScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Review / replay" }));
    await screen.findByRole("heading", { name: "Preflight review" });
    expect(preflightSavedPlan).toHaveBeenCalledWith("plan-1");
    fireEvent.click(screen.getByRole("button", { name: "Launch benchmark" }));
    await screen.findByText("run-1");
    expect(createBenchmarkPlan).not.toHaveBeenCalled();
    expect(startBenchmarkRun).toHaveBeenCalledWith("plan-1", false);
  });
});
