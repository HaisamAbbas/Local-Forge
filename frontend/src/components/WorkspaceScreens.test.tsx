import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createEngineProfile,
  deleteEngineProfile,
  discoverEngines,
  enableEngineProfile,
  fetchEngineRecommendations,
  listBenchmarkPlans,
  listEngineCatalog,
  listEngineModels,
  listEngineProfiles,
  listMachineProfiles,
  listRunMetrics,
  listRuns,
  probeEngineProfile,
  startEngineProfile,
  stopEngineProfile,
  updateEngineProfile,
  type AcceleratorDetails,
  type AdapterManifest,
  type BenchmarkPlan,
  type BenchmarkRun,
  type EngineDiscoveryResult,
  type EngineProfile,
  type EngineRecommendationsResult,
  type HardwareDetails,
  type MetricSample,
  type ProbeResponse,
} from "../lib/api";
import {
  recommendationFixture,
  recommendationsFixture,
} from "../lib/engine-recommendations-fixture";
import { EnginesScreen, OverviewScreen, SystemScreen } from "./WorkspaceScreens";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    createEngineProfile: vi.fn(),
    deleteEngineProfile: vi.fn(),
    discoverEngines: vi.fn(),
    enableEngineProfile: vi.fn(),
    fetchEngineRecommendations: vi.fn(),
    fetchSystemProfile: vi.fn(),
    listBenchmarkPlans: vi.fn(),
    listRunMetrics: vi.fn(),
    listEngineCatalog: vi.fn(),
    listEngineModels: vi.fn(),
    listEngineProfiles: vi.fn(),
    listMachineProfiles: vi.fn(),
    listRuns: vi.fn(),
    probeEngineProfile: vi.fn(),
    startEngineProfile: vi.fn(),
    stopEngineProfile: vi.fn(),
    saveCurrentMachineProfile: vi.fn(),
    updateEngineProfile: vi.fn(),
  };
});

const catalogFixture: AdapterManifest = {
  adapter_id: "fake",
  adapter_version: "1.0.0",
  display_name: "Deterministic Fake Engine",
  description: "A local fixture for exercising the UI.",
  category: "test_fixture",
  integration_modes: ["attached_endpoint", "managed_process"],
  operating_systems: ["windows", "linux", "macos"],
  implementation_status: "implemented",
  version_tested: [],
  maintenance_mode: false,
  recommended_for_new_managed_install: true,
  status_details: null,
  declared_capabilities: ["streaming", "health"],
  supported_transports: ["in_process_fake"],
  native_metrics: ["fake.requests_total"],
  config_schema: {},
  known_limitations: ["Does not load a language model."],
  documentation_url: null,
};

const openAiCompatibleFixture: AdapterManifest = {
  adapter_id: "localforge.openai_compatible",
  adapter_version: "1.0.0",
  display_name: "OpenAI-compatible endpoint",
  description: "Connect to an attached chat-completions endpoint.",
  category: "openai_compatible",
  integration_modes: ["attached_endpoint"],
  operating_systems: [],
  implementation_status: "implemented",
  version_tested: [],
  maintenance_mode: false,
  recommended_for_new_managed_install: true,
  status_details: null,
  declared_capabilities: ["chat_completion", "streaming", "model_inventory"],
  supported_transports: ["http", "https", "server_sent_events"],
  native_metrics: [],
  config_schema: {},
  known_limitations: ["The underlying engine remains unknown unless named by the user."],
  documentation_url: null,
};

const llamaCppFixture: AdapterManifest = {
  adapter_id: "localforge.llama_cpp",
  adapter_version: "1.0.0",
  display_name: "llama.cpp / llama-server",
  description: "Attach to or launch an existing local llama-server binary.",
  category: "serving_stack",
  integration_modes: ["attached_endpoint", "managed_process"],
  operating_systems: ["windows", "linux", "macos"],
  implementation_status: "implemented",
  version_tested: [],
  maintenance_mode: false,
  recommended_for_new_managed_install: true,
  status_details: null,
  declared_capabilities: ["chat_completion", "streaming", "native_metrics"],
  supported_transports: ["http", "https", "server_sent_events"],
  native_metrics: ["llamacpp:prompt_tokens_total"],
  config_schema: {},
  known_limitations: ["GGUF files must already exist locally."],
  documentation_url: "https://github.com/ggml-org/llama.cpp",
};

const ollamaFixture: AdapterManifest = {
  adapter_id: "localforge.ollama",
  adapter_version: "1.0.0",
  display_name: "Ollama",
  description: "Attach to an existing local Ollama server.",
  category: "native_api",
  integration_modes: ["attached_endpoint"],
  operating_systems: ["windows", "linux", "macos"],
  implementation_status: "implemented",
  version_tested: [],
  maintenance_mode: false,
  recommended_for_new_managed_install: true,
  status_details: null,
  declared_capabilities: ["health", "model_inventory", "streaming"],
  supported_transports: ["http", "ollama_ndjson"],
  native_metrics: ["ollama.eval_duration"],
  config_schema: {},
  known_limitations: ["Runner details are unknown unless verified."],
  documentation_url: "https://docs.ollama.com/api",
};

function engineFixture(overrides: Partial<EngineProfile> = {}): EngineProfile {
  return {
    id: "profile-1",
    label: "Fixture profile",
    adapter_id: "fake",
    engine_display_name: null,
    adapter_version: "1.0.0",
    integration_mode: "attached_endpoint",
    status: "disabled",
    managed_process_running: false,
    endpoint_url: null,
    is_remote: false,
    remote_access_enabled: false,
    engine_version: null,
    last_probed_at: null,
    capability_snapshot_id: null,
    capabilities: null,
    machine_profile_id: null,
    selected_model_ref: null,
    user_notes: null,
    configured_fields: [],
    credential_configured: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    schema_version: 1,
    ...overrides,
  };
}

const machineFixture = {
  id: "machine-1",
  label: "This machine",
  status: "partially_detected" as const,
  host_name: "dev-host",
  hardware: {
    os_name: "Windows",
    os_version: "10.0",
    architecture: "AMD64",
    cpu_model: "Test CPU",
    cpu_logical_cores: 8,
    memory_total_bytes: 8 * 1024 ** 3,
    accelerators: null,
  },
  provenance: {
    os_name: { source: "detected", confidence: 1, observed_at: "2026-01-01T00:00:00Z" },
    cpu_model: { source: "detected", confidence: 0.9, observed_at: "2026-01-01T00:00:00Z" },
  },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const probeFixture: ProbeResponse = {
  profile: engineFixture(),
  result: {
    adapter_id: "fake",
    adapter_version: "1.0.0",
    status: "succeeded",
    reachable: { state: "supported", details: null, source: "fake_probe" },
    health: { state: "supported", details: null, source: "fake_probe" },
    engine_version: "fake-1",
    models: [
      {
        model_ref: "fake://deterministic-model",
        display_name: "LocalForge Test Model",
        revision: "fixture-1",
        metadata: {},
      },
    ],
    checks: {
      endpoint_reachability: { state: "supported", details: null, source: "fake_probe" },
      health: { state: "supported", details: null, source: "fake_probe" },
      model_inventory: { state: "supported", details: null, source: "fake_probe" },
      streaming: { state: "unknown", details: "Not reported in this probe.", source: null },
      native_metrics: { state: "unsupported", details: null, source: "fake_probe" },
    },
    capabilities: {
      capabilities: {
        health: { state: "supported", details: null, source: "fake_probe" },
        streaming: { state: "unknown", details: null, source: null },
        model_inventory: { state: "supported", details: null, source: "fake_probe" },
        native_metrics: { state: "unsupported", details: null, source: "fake_probe" },
      },
    },
    diagnostics: [],
    probed_at: "2026-01-01T00:00:00Z",
  },
};

const emptyRecommendations: EngineRecommendationsResult = {
  generated_at: "2026-01-01T00:00:00Z",
  method: "Ranked by hardware fit and what is installed; not a speed measurement.",
  machine: {
    summary: "Windows · 8 logical cores · 8 GB memory",
    os: "Windows",
    arch: "AMD64",
    cpu_model: "Test CPU",
    cpu_logical_cores: 8,
    cpu_physical_cores: 4,
    memory_total_bytes: 8 * 1024 ** 3,
    accelerators: [],
    cpu_features: { avx2: true, avx512: null },
  },
  model_size_guidance: {
    basis: "system_memory",
    memory_budget_bytes: 4 * 1024 ** 3,
    comfortable_params_billion_q4: 3,
    max_params_billion_q4: 7,
    text: "Models up to about 3B parameters (Q4) should run comfortably.",
  },
  recommendations: [],
};

beforeEach(() => {
  vi.mocked(fetchEngineRecommendations).mockResolvedValue(emptyRecommendations);
  vi.mocked(listEngineCatalog).mockResolvedValue([catalogFixture]);
  vi.mocked(listEngineProfiles).mockResolvedValue([]);
  vi.mocked(listMachineProfiles).mockResolvedValue([]);
  vi.mocked(createEngineProfile).mockResolvedValue(engineFixture());
  vi.mocked(updateEngineProfile).mockResolvedValue(engineFixture());
  vi.mocked(deleteEngineProfile).mockResolvedValue(undefined);
  vi.mocked(probeEngineProfile).mockResolvedValue(probeFixture);
  vi.mocked(startEngineProfile).mockResolvedValue(engineFixture());
  vi.mocked(stopEngineProfile).mockResolvedValue(engineFixture());
  vi.mocked(listEngineModels).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("overview and system", () => {
  it("shows real machine provenance and a useful no-runs state", async () => {
    const { fetchSystemProfile, listRuns } = await import("../lib/api");
    vi.mocked(fetchSystemProfile).mockResolvedValue(machineFixture);
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(listEngineProfiles).mockResolvedValue([]);

    render(<OverviewScreen />);

    expect(await screen.findByText("No benchmark runs yet")).toBeInTheDocument();
    expect(screen.getAllByText("Test CPU")).not.toHaveLength(0);
    expect(screen.getAllByText(/Detected/)).not.toHaveLength(0);
    expect(screen.getAllByText("Unknown / not detected")).not.toHaveLength(0);
  });

  it("reports loading and offline errors as separate accessible states", async () => {
    const { fetchSystemProfile, listRuns } = await import("../lib/api");
    vi.mocked(fetchSystemProfile).mockImplementation(() => new Promise(() => undefined));
    vi.mocked(listRuns).mockRejectedValue(new Error("Local API offline"));
    vi.mocked(listEngineProfiles).mockResolvedValue([]);

    render(<OverviewScreen />);

    expect(screen.getByText("Reading machine details…")).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByRole("alert")).toHaveTextContent("Local API offline");
  });

  it("labels manually supplied hardware as unverified", async () => {
    const { fetchSystemProfile } = await import("../lib/api");
    vi.mocked(fetchSystemProfile).mockResolvedValue({
      ...machineFixture,
      status: "manual_unverified",
      provenance: { cpu_model: { source: "user_supplied", confidence: 1, observed_at: null } },
    });
    vi.mocked(listMachineProfiles).mockResolvedValue([]);

    render(<SystemScreen />);

    expect(await screen.findByText("Manual Unverified")).toBeInTheDocument();
    expect(screen.getByText("User Supplied")).toBeInTheDocument();
    expect(screen.getByText("8 GB")).toBeInTheDocument();
  });
});

describe("engine management", () => {
  it("shows TGI maintenance status and keeps planned Triton unavailable", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([
      {
        ...catalogFixture,
        adapter_id: "localforge.tgi",
        display_name: "Hugging Face TGI serving endpoint",
        category: "openai_compatible",
        integration_modes: ["attached_endpoint"],
        maintenance_mode: true,
        recommended_for_new_managed_install: false,
        status_details: "Maintenance mode. Connect to an existing deployment.",
        version_tested: ["3.3.7 (local API fixture)"],
      },
      {
        ...catalogFixture,
        adapter_id: "localforge.triton_tensorrt_llm",
        display_name: "NVIDIA Triton + TensorRT-LLM backend",
        implementation_status: "planned",
        integration_modes: [],
        version_tested: [],
        status_details: "Planned; no callable adapter is registered yet.",
      },
    ]);

    render(<EnginesScreen />);

    const tgiCard = (
      await screen.findByRole("heading", {
        name: "Hugging Face TGI serving endpoint",
      })
    ).closest("article");
    expect(tgiCard).not.toBeNull();
    expect(within(tgiCard as HTMLElement).getByText("Maintenance mode")).toBeInTheDocument();
    expect(within(tgiCard as HTMLElement).getByText("Fixture contract tested")).toBeInTheDocument();
    expect(
      within(tgiCard as HTMLElement).getByText("Local fixture: 3.3.7 (local API fixture)"),
    ).toBeInTheDocument();
    expect(
      within(tgiCard as HTMLElement).getByText("Attached endpoint (local or remote)"),
    ).toBeInTheDocument();
    expect(within(tgiCard as HTMLElement).getByText("Windows · Linux · macOS")).toBeInTheDocument();

    const tritonCard = screen
      .getByRole("heading", {
        name: "NVIDIA Triton + TensorRT-LLM backend",
      })
      .closest("article");
    expect(tritonCard).not.toBeNull();
    expect(within(tritonCard as HTMLElement).getByText("Planned")).toBeInTheDocument();
    expect(
      within(tritonCard as HTMLElement).getByText("No callable integration mode"),
    ).toBeInTheDocument();
    expect(
      within(tritonCard as HTMLElement).getByRole("button", { name: "Not available yet" }),
    ).toBeDisabled();
  });

  it("renders library-pipeline settings from the adapter manifest schema", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([
      {
        ...catalogFixture,
        adapter_id: "localforge.openvino_genai",
        display_name: "OpenVINO GenAI library pipeline",
        category: "library_pipeline",
        integration_modes: ["library_pipeline"],
        config_schema: {
          type: "object",
          required: ["model_path"],
          properties: {
            model_path: {
              type: "string",
              format: "local-path",
              title: "Existing local model path",
              description: "Choose an existing model folder.",
            },
            python_executable: {
              type: "string",
              format: "local-executable-path",
              title: "Python executable",
            },
            startup_timeout_seconds: {
              type: "number",
              minimum: 1,
              maximum: 600,
              default: 180,
              title: "Worker startup timeout (seconds)",
            },
            request_timeout_seconds: {
              type: "number",
              minimum: 1,
              maximum: 3600,
              default: 300,
              title: "Request timeout (seconds)",
            },
            device: { type: "string", default: "CPU", title: "Inference device" },
          },
        },
      },
    ]);

    render(<EnginesScreen />);
    const card = (
      await screen.findByRole("heading", { name: "OpenVINO GenAI library pipeline" })
    ).closest("article");
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Add this adapter" }));

    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: "OpenVINO local model" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /Existing local model path/ }), {
      target: { value: "C:\\Models\\openvino-model" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /Python executable/ }), {
      target: { value: "C:\\venv\\Scripts\\python.exe" },
    });
    fireEvent.change(
      within(dialog).getByRole("spinbutton", { name: /Worker startup timeout \(seconds\)/ }),
      { target: { value: "45" } },
    );
    fireEvent.change(within(dialog).getByRole("textbox", { name: /Inference device/ }), {
      target: { value: "GPU" },
    });
    expect(within(dialog).queryByRole("textbox", { name: /Endpoint URL/ })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add engine" }));

    expect(createEngineProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        adapter_id: "localforge.openvino_genai",
        integration_mode: "library_pipeline",
        endpoint_url: null,
        engine_extensions: {
          openvino_genai: {
            model_path: "C:\\Models\\openvino-model",
            python_executable: "C:\\venv\\Scripts\\python.exe",
            startup_timeout_seconds: 45,
            request_timeout_seconds: 300,
            device: "GPU",
          },
        },
      }),
    );
  });

  it("renders arbitrary attached-adapter extension fields without an engine-specific form", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([
      {
        ...catalogFixture,
        adapter_id: "localforge.fixture_serving",
        display_name: "Fixture serving connector",
        category: "openai_compatible",
        integration_modes: ["attached_endpoint"],
        config_schema: {
          type: "object",
          properties: {
            endpoint_url: { type: "string", format: "uri" },
            engine_extensions: {
              type: "object",
              properties: {
                fixture_serving: {
                  type: "object",
                  required: ["streaming_mode"],
                  properties: {
                    streaming_mode: {
                      enum: ["auto", "disabled"],
                      title: "Streaming mode",
                    },
                    metrics_enabled: {
                      type: "boolean",
                      default: false,
                      title: "Read native metrics",
                    },
                  },
                },
              },
            },
          },
        },
      },
    ]);

    render(<EnginesScreen />);
    const card = (
      await screen.findByRole("heading", { name: "Fixture serving connector" })
    ).closest("article");
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Add this adapter" }));
    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: "Schema driven endpoint" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /Endpoint URL/ }), {
      target: { value: "http://127.0.0.1:9001" },
    });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Streaming mode" }), {
      target: { value: "disabled" },
    });
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Read native metrics" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add engine" }));

    expect(createEngineProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        adapter_id: "localforge.fixture_serving",
        endpoint_url: "http://127.0.0.1:9001",
        engine_extensions: {
          fixture_serving: { streaming_mode: "disabled", metrics_enabled: true },
        },
      }),
    );
  });

  it("labels attached profile endpoints as local or remote", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([openAiCompatibleFixture]);
    vi.mocked(listEngineProfiles).mockResolvedValue([
      engineFixture({
        id: "local-profile",
        label: "Local endpoint profile",
        adapter_id: "localforge.openai_compatible",
        endpoint_url: "http://127.0.0.1:9000/v1",
      }),
      engineFixture({
        id: "remote-profile",
        label: "Remote endpoint profile",
        adapter_id: "localforge.openai_compatible",
        endpoint_url: "https://models.example.test/v1",
        is_remote: true,
      }),
    ]);

    render(<EnginesScreen />);

    expect(await screen.findByText(/Local endpoint$/)).toBeInTheDocument();
    expect(screen.getByText(/Remote endpoint$/)).toBeInTheDocument();
  });

  it("stores LocalAI model selection for backend fingerprint discovery", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([
      {
        ...catalogFixture,
        adapter_id: "localforge.localai",
        display_name: "LocalAI serving endpoint",
        integration_modes: ["attached_endpoint"],
        category: "openai_compatible",
      },
    ]);

    render(<EnginesScreen />);
    const card = (
      await screen.findByRole("heading", {
        name: "LocalAI serving endpoint",
      })
    ).closest("article");
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Add this adapter" }));

    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: "LocalAI" },
    });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Connection mode" }), {
      target: { value: "attached_endpoint" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /Endpoint URL/ }), {
      target: { value: "http://127.0.0.1:8080" },
    });
    fireEvent.change(
      within(dialog).getByRole("textbox", { name: /Model ID for backend discovery/ }),
      {
        target: { value: "qwen3-local" },
      },
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Add engine" }));

    expect(createEngineProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        adapter_id: "localforge.localai",
        integration_mode: "attached_endpoint",
        endpoint_url: "http://127.0.0.1:8080",
        engine_extensions: {
          localai: { model_id: "qwen3-local", discover_backend: true },
        },
      }),
    );
  });

  it("supports add, partial probe, inspect, edit and remove from the UI", async () => {
    let savedProfiles = [] as EngineProfile[];
    vi.mocked(listEngineProfiles).mockImplementation(async () => savedProfiles);
    vi.mocked(createEngineProfile).mockImplementation(async (input) => {
      const created = engineFixture({ label: input.label, credential_configured: true });
      savedProfiles = [created];
      return created;
    });
    vi.mocked(updateEngineProfile).mockImplementation(async (_id, input) => {
      const updated = engineFixture({ label: input.label, credential_configured: true });
      savedProfiles = [updated];
      return updated;
    });
    vi.mocked(deleteEngineProfile).mockImplementation(async () => {
      savedProfiles = [];
    });

    render(<EnginesScreen />);
    expect(await screen.findByText("No engine profiles are configured.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add this adapter" }));
    const addDialog = screen.getByRole("dialog", { name: "Add an engine" });
    const nameInput = within(addDialog).getByRole("textbox", { name: "Profile name" });
    expect(nameInput).toHaveFocus();
    fireEvent.keyDown(nameInput, { key: "Tab" });
    fireEvent.change(nameInput, { target: { value: "Fixture profile" } });
    fireEvent.click(within(addDialog).getByRole("button", { name: "Add engine" }));

    expect(await screen.findByRole("heading", { name: "Fixture profile" })).toBeInTheDocument();
    expect(screen.getByText("Configured · value hidden")).toBeInTheDocument();
    expect(screen.queryByText(/secret|token-123|keyring:\/\//i)).not.toBeInTheDocument();
    expect(createEngineProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        label: "Fixture profile",
        adapter_id: "fake",
        integration_mode: "attached_endpoint",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Probe" }));
    expect(
      await screen.findByRole("heading", { name: "Models reported by this probe" }),
    ).toBeInTheDocument();
    const streamingSummary = screen.getByText("Streaming", {
      selector: ".probe-summary-grid span",
    }).parentElement;
    expect(within(streamingSummary as HTMLElement).getByText("Unknown")).toBeInTheDocument();
    expect(screen.getAllByText("Unsupported", { selector: ".capability-state" })).not.toHaveLength(
      0,
    );
    expect(
      screen.getByRole("row", { name: /LocalForge Test Model fake:\/\/deterministic-model/ }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const editDialog = screen.getByRole("dialog", { name: "Edit engine profile" });
    fireEvent.change(within(editDialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: "Renamed fixture" },
    });
    fireEvent.click(within(editDialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("heading", { name: "Renamed fixture" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    const removeDialog = screen.getByRole("dialog", { name: "Remove engine profile?" });
    fireEvent.click(within(removeDialog).getByRole("button", { name: "Remove profile" }));
    expect(await screen.findByText("No engine profiles are configured.")).toBeInTheDocument();
    expect(deleteEngineProfile).toHaveBeenCalledWith("profile-1");
    expect(updateEngineProfile).toHaveBeenCalledWith(
      "profile-1",
      expect.objectContaining({ label: "Renamed fixture" }),
    );
    const editPatch = vi.mocked(updateEngineProfile).mock.calls[0]?.[1];
    expect(editPatch).not.toHaveProperty("endpoint_url");
    expect(editPatch).not.toHaveProperty("integration_mode");
    expect(probeEngineProfile).toHaveBeenCalledWith("profile-1", false);
  });

  it("keeps a successful probe result when refreshing saved models fails", async () => {
    vi.mocked(listEngineProfiles).mockResolvedValue([engineFixture()]);
    vi.mocked(listEngineModels).mockRejectedValue(new Error("Saved model list unavailable"));

    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));

    expect(
      await screen.findByRole("heading", { name: "Models reported by this probe" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Probe status").parentElement).toHaveTextContent("Succeeded");
    expect(screen.getByRole("alert")).toHaveTextContent("Saved model list unavailable");
    expect(probeEngineProfile).toHaveBeenCalledWith("profile-1", false);
  });

  it("saves generic endpoint settings and only accepts a credential reference", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([catalogFixture, openAiCompatibleFixture]);
    render(<EnginesScreen />);

    const endpointHeading = await screen.findByRole("heading", {
      name: "OpenAI-compatible endpoint",
    });
    const card = endpointHeading.closest("article");
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Add this adapter" }));

    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: "Local API" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "API base URL" }), {
      target: { value: "http://127.0.0.1:9000/v1" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: /Model ID/ }), {
      target: { value: "fixture-model" },
    });
    fireEvent.change(within(dialog).getByRole("spinbutton", { name: /Connection retries/ }), {
      target: { value: "2" },
    });
    fireEvent.change(within(dialog).getByPlaceholderText("keyring://LocalForge/account-name"), {
      target: { value: "keyring://LocalForge/test-account" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add engine" }));

    expect(createEngineProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        adapter_id: "localforge.openai_compatible",
        endpoint_url: "http://127.0.0.1:9000/v1",
        engine_extensions: {
          openai_compatible: expect.objectContaining({
            model_id: "fixture-model",
            max_retries: 2,
            streaming_mode: "auto",
          }),
        },
        secret_refs: { api_key: "keyring://LocalForge/test-account" },
      }),
    );
  });

  it("saves llama.cpp managed launch paths and validated tuning options", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([llamaCppFixture]);
    render(<EnginesScreen />);

    const adapterHeading = await screen.findByRole("heading", {
      name: "llama.cpp / llama-server",
    });
    const card = adapterHeading.closest("article");
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Add this adapter" }));

    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: "Local llama-server" },
    });
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Connection mode" }), {
      target: { value: "managed_process" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Local bind URL" }), {
      target: { value: "http://127.0.0.1:9931" },
    });
    fireEvent.change(
      within(dialog).getByRole("textbox", { name: "llama-server executable path" }),
      {
        target: { value: "C:\\llama.cpp\\llama-server.exe" },
      },
    );
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Existing GGUF model path" }), {
      target: { value: "C:\\Models\\small.gguf" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Working directory" }), {
      target: { value: "C:\\llama.cpp\\build\\bin" },
    });
    fireEvent.change(within(dialog).getByRole("spinbutton", { name: "Context size Optional" }), {
      target: { value: "4096" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "GPU layers Optional" }), {
      target: { value: "all" },
    });
    expect(within(dialog).getByText(/never downloads or uploads them/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add engine" }));

    expect(createEngineProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        adapter_id: "localforge.llama_cpp",
        endpoint_url: "http://127.0.0.1:9931",
        integration_mode: "managed_process",
        engine_extensions: {
          llama_cpp: expect.objectContaining({
            executable_path: "C:\\llama.cpp\\llama-server.exe",
            model_path: "C:\\Models\\small.gguf",
            working_directory: "C:\\llama.cpp\\build\\bin",
            context_size: 4096,
            gpu_layers: "all",
          }),
        },
      }),
    );
  });

  it("configures Ollama as local attach-only with no implicit model download", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([ollamaFixture]);
    render(<EnginesScreen />);

    const adapterHeading = await screen.findByRole("heading", { name: "Ollama" });
    const card = adapterHeading.closest("article");
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Add this adapter" }));

    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    expect(within(dialog).getByRole("combobox", { name: "Connection mode" })).toHaveValue(
      "attached_endpoint",
    );
    expect(within(dialog).getByRole("textbox", { name: /Ollama local server URL/ })).toHaveValue(
      "http://127.0.0.1:11434",
    );
    expect(within(dialog).getByText(/never pulls or deletes models/i)).toBeInTheDocument();
    expect(within(dialog).queryByText("Models route")).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: "Local Ollama" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add engine" }));

    expect(createEngineProfile).toHaveBeenCalledWith(
      expect.objectContaining({
        label: "Local Ollama",
        adapter_id: "localforge.ollama",
        integration_mode: "attached_endpoint",
        endpoint_url: "http://127.0.0.1:11434",
        engine_extensions: {
          ollama: expect.objectContaining({ timeout_seconds: 30, streaming_mode: "auto" }),
        },
      }),
    );
  });

  it("starts and stops managed engine processes from the profile card", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([llamaCppFixture]);
    let savedProfile = engineFixture({
      adapter_id: "localforge.llama_cpp",
      integration_mode: "managed_process",
      endpoint_url: "http://127.0.0.1:9931",
    });
    vi.mocked(listEngineProfiles).mockImplementation(async () => [savedProfile]);
    vi.mocked(startEngineProfile).mockImplementation(async () => {
      savedProfile = { ...savedProfile, status: "enabled", managed_process_running: true };
      return savedProfile;
    });
    vi.mocked(stopEngineProfile).mockImplementation(async () => {
      savedProfile = { ...savedProfile, status: "disabled", managed_process_running: false };
      return savedProfile;
    });

    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });
    expect(screen.getByRole("button", { name: "Probe" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Start server" }));
    expect(await screen.findByRole("button", { name: "Stop server" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Probe" })).toBeEnabled();
    expect(startEngineProfile).toHaveBeenCalledWith("profile-1");

    fireEvent.click(screen.getByRole("button", { name: "Stop server" }));
    expect(await screen.findByRole("button", { name: "Start server" })).toBeInTheDocument();
    expect(stopEngineProfile).toHaveBeenCalledWith("profile-1");
  });

  it("keeps the catalog error retryable and the profile empty state visible", async () => {
    vi.mocked(listEngineCatalog).mockRejectedValue(new Error("Local API unavailable"));
    vi.mocked(listEngineProfiles).mockResolvedValue([]);

    render(<EnginesScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Local API unavailable");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByText("No engine profiles are configured.")).toBeInTheDocument();
  });

  it("shows remote egress details before the confirmed probe call", async () => {
    const networkManifest = {
      ...catalogFixture,
      adapter_id: "network.adapter",
      category: "openai_compatible",
    };
    const remoteProfile = engineFixture({
      adapter_id: "network.adapter",
      endpoint_url: "https://inference.example.test/v1",
      is_remote: true,
    });
    vi.mocked(listEngineCatalog).mockResolvedValue([networkManifest]);
    vi.mocked(listEngineProfiles).mockResolvedValue([remoteProfile]);
    vi.mocked(probeEngineProfile)
      .mockRejectedValueOnce(
        new ApiError(
          "This profile targets remote host inference.example.test. Confirm remote use before probing.",
          409,
          "remote_confirmation_required",
          "inference.example.test",
        ),
      )
      .mockResolvedValueOnce(probeFixture);

    render(<EnginesScreen />);
    const card = await screen.findByRole("heading", { name: "Fixture profile" });
    expect(card).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));

    const disclosure = await screen.findByRole("dialog", { name: "Confirm remote probe" });
    expect(disclosure).toHaveTextContent("inference.example.test");
    expect(disclosure).toHaveTextContent("prompts and model requests will also leave this machine");
    expect(probeEngineProfile).toHaveBeenNthCalledWith(1, "profile-1", false);

    fireEvent.click(within(disclosure).getByRole("button", { name: /I understand/ }));
    await screen.findByRole("heading", { name: "Models reported by this probe" });
    expect(probeEngineProfile).toHaveBeenNthCalledWith(2, "profile-1", true);
  });

  it("does not claim a remote fixture endpoint was contacted", async () => {
    vi.mocked(listEngineProfiles).mockResolvedValue([
      engineFixture({ endpoint_url: "https://inference.example.test/v1", is_remote: true }),
    ]);

    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });
    fireEvent.click(screen.getByRole("button", { name: "Probe" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "is a local test fixture and does not contact https://inference.example.test/v1",
    );
    expect(probeEngineProfile).not.toHaveBeenCalled();
  });

  it("marks orphaned adapter actions unavailable while keeping removal possible", async () => {
    vi.mocked(listEngineProfiles).mockResolvedValue([
      engineFixture({ adapter_id: "removed.adapter" }),
    ]);

    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });

    expect(screen.getByText(/adapter removed\.adapter is not installed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Probe" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove" })).toBeEnabled();
  });
});

describe("add engine default", () => {
  it("opens the first implemented, non-fixture adapter instead of a planned entry", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([
      {
        ...catalogFixture,
        adapter_id: "localforge.exllamav3",
        display_name: "ExLlamaV3 library pipeline",
        category: "library_pipeline",
        implementation_status: "planned",
        integration_modes: [],
      },
      catalogFixture,
      llamaCppFixture,
    ]);

    render(<EnginesScreen />);
    expect(await screen.findByText("No engine profiles are configured.")).toBeInTheDocument();
    await screen.findByRole("heading", { name: "llama.cpp / llama-server" });

    fireEvent.click(screen.getByRole("button", { name: "Add engine" }));
    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    expect(within(dialog).getByText(/^llama\.cpp \/ llama-server:/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close dialog" }));

    fireEvent.click(screen.getByRole("button", { name: "Add manually" }));
    expect(
      within(screen.getByRole("dialog", { name: "Add an engine" })).getByText(
        /^llama\.cpp \/ llama-server:/,
      ),
    ).toBeInTheDocument();
  });

  it("disables adding when only planned connectors are listed", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([
      { ...catalogFixture, implementation_status: "planned", integration_modes: [] },
    ]);

    render(<EnginesScreen />);
    await screen.findByText("No engine profiles are configured.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Add engine" })).toBeDisabled());
  });
});

describe("system accelerator details", () => {
  it("labels integrated GPUs with shared memory and shows physical cores", async () => {
    const { fetchSystemProfile } = await import("../lib/api");
    const hardware: HardwareDetails & { accelerators: AcceleratorDetails[] } = {
      ...machineFixture.hardware,
      cpu_physical_cores: 12,
      accelerators: [
        {
          name: "Intel(R) Graphics",
          vendor: "Intel",
          memory_bytes: null,
          runtime: null,
          driver_version: "32.0.101.8801",
          integrated: true,
          memory_kind: "shared",
          detected_by: "windows-video-controller",
        },
      ],
    };
    vi.mocked(fetchSystemProfile).mockResolvedValue({ ...machineFixture, hardware });

    render(<SystemScreen />);

    expect(await screen.findByText("Intel(R) Graphics")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Intel · Integrated · 32.0.101.8801 · Shared system memory · via windows-video-controller",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Physical cores")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });
});

function runFixture(id: string, overrides: Partial<BenchmarkRun> = {}): BenchmarkRun {
  return {
    id,
    plan_id: `plan-${id}`,
    status: "completed",
    worker_id: null,
    potentially_contended: false,
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:01:00Z",
    error_code: null,
    error_message: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:01:00Z",
    ...overrides,
  };
}

function metricFixture(
  runId: string,
  metricName: string,
  aggregation: string,
  value: number,
  unit: string,
): MetricSample {
  return {
    id: `${runId}-${metricName}-${aggregation}`,
    run_id: runId,
    metric_name: metricName,
    value,
    unit,
    source: "client_measured",
    aggregation,
    sample_count: 10,
    definition: "",
    formula: "",
    method_version: "1",
    quality_flags: [],
    metric_kind: "gauge",
    adapter_id: null,
    adapter_version: null,
    engine_version: null,
    observed_at: "2026-01-01T00:01:00Z",
    labels: {},
  };
}

describe("overview run trend", () => {
  it("charts TTFT p50 and generation throughput for completed runs and names skipped runs", async () => {
    const { fetchSystemProfile } = await import("../lib/api");
    vi.mocked(fetchSystemProfile).mockResolvedValue(machineFixture);
    vi.mocked(listRuns).mockResolvedValue([
      runFixture("run-newest-0000", { completed_at: "2026-01-03T00:00:00Z" }),
      runFixture("run-failed-0000", { status: "failed", completed_at: "2026-01-04T00:00:00Z" }),
      runFixture("run-oldest-0000", { completed_at: "2026-01-02T00:00:00Z" }),
    ]);
    vi.mocked(listBenchmarkPlans).mockResolvedValue([
      { id: "plan-run-newest-0000", name: "Newest plan" } as BenchmarkPlan,
      { id: "plan-run-oldest-0000", name: "Oldest plan" } as BenchmarkPlan,
    ]);
    vi.mocked(listRunMetrics).mockImplementation(async (id) => ({
      metrics:
        id === "run-newest-0000"
          ? [metricFixture(id, "ttft_ms", "p50", 120, "ms")]
          : [
              metricFixture(id, "ttft_ms", "p50", 95, "ms"),
              metricFixture(
                id,
                "generation_throughput_tokens_per_second",
                "run_rate",
                42.5,
                "tokens/s",
              ),
            ],
    }));

    render(<OverviewScreen />);

    expect(
      await screen.findByRole("heading", { name: "Throughput and latency trend" }),
    ).toBeInTheDocument();
    expect(listRunMetrics).toHaveBeenCalledTimes(2);
    expect(listRunMetrics).not.toHaveBeenCalledWith("run-failed-0000");
    expect(
      screen.getByText(/1 run skipped for generation throughput \(not recorded\): Newest plan\./),
    ).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent(/Oldest plan.*95 ms.*42\.5 tokens\/s/);
    expect(rows[1]).toHaveTextContent(/Newest plan.*120 ms.*Not recorded/);
    expect(screen.getByRole("link", { name: /Newest plan · run-newe/ })).toHaveAttribute(
      "href",
      "#runs?run=run-newest-0000",
    );
    expect(
      screen.getByRole("img", { name: /TTFT p50 for 2 recent completed runs/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /generation throughput for 1 recent completed runs/ }),
    ).toBeInTheDocument();
  });
});

function failedProbe(message: string): ProbeResponse {
  return {
    profile: engineFixture({ status: "needs_attention", last_probed_at: "2026-01-01T00:00:00Z" }),
    result: {
      ...probeFixture.result,
      status: "failed",
      reachable: { state: "unsupported", details: null, source: "probe" },
      diagnostics: [{ code: "connection_refused", message, severity: "error", retryable: true }],
    },
  };
}

function checklistFor(label = "Fixture profile") {
  return screen.getByRole("list", { name: `Next steps for ${label}` });
}

describe("engine setup checklist", () => {
  it("walks from probe to enable to Playground and Benchmark links", async () => {
    let profile = engineFixture();
    vi.mocked(listEngineProfiles).mockImplementation(async () => [profile]);
    vi.mocked(probeEngineProfile).mockImplementation(async () => {
      profile = engineFixture({ last_probed_at: "2026-01-01T00:00:00Z" });
      return probeFixture;
    });
    vi.mocked(enableEngineProfile).mockImplementation(async () => {
      profile = engineFixture({ status: "enabled", last_probed_at: "2026-01-01T00:00:00Z" });
      return profile;
    });

    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });
    let checklist = checklistFor();
    expect(within(checklist).getByText("✓ Added")).toBeInTheDocument();
    const probeButton = within(checklist).getByRole("button", { name: "Probe" });
    expect(probeButton.closest("li")).toHaveAttribute("aria-current", "step");
    expect(probeButton).toHaveClass("button--primary");
    expect(within(checklist).getByRole("button", { name: "Enable" })).toBeDisabled();
    expect(within(checklist).queryByRole("link")).not.toBeInTheDocument();

    fireEvent.click(probeButton);
    await waitFor(() =>
      expect(within(checklistFor()).getByRole("button", { name: "Enable" })).toBeEnabled(),
    );
    checklist = checklistFor();
    expect(within(checklist).getByText(/✓ Probed/)).toBeInTheDocument();
    expect(within(checklist).getByRole("button", { name: "Enable" }).closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    // Only one enable prompt: the old separate notice is folded into the checklist.
    expect(screen.queryByText(/This engine is still disabled/)).not.toBeInTheDocument();

    fireEvent.click(within(checklist).getByRole("button", { name: "Enable" }));
    await waitFor(() => expect(within(checklistFor()).getByText("✓ Enabled")).toBeInTheDocument());
    checklist = checklistFor();
    expect(within(checklist).getByRole("link", { name: "Try in Playground" })).toHaveAttribute(
      "href",
      "#playground?engine=profile-1",
    );
    const benchmark = within(checklist).getByRole("link", { name: "Benchmark" });
    expect(benchmark).toHaveAttribute("href", "#workloads?engine=profile-1");
    expect(benchmark.closest("li")).toHaveAttribute("aria-current", "step");
    expect(enableEngineProfile).toHaveBeenCalledWith("profile-1");
  });

  it("keeps a failed probe disabled and shows the problem on the card", async () => {
    let profile = engineFixture();
    vi.mocked(listEngineProfiles).mockImplementation(async () => [profile]);
    vi.mocked(probeEngineProfile).mockImplementation(async () => {
      const response = failedProbe("Connection refused at http://127.0.0.1:11434.");
      profile = response.profile;
      return response;
    });

    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });
    fireEvent.click(within(checklistFor()).getByRole("button", { name: "Probe" }));

    expect(
      await screen.findByText(/Connection refused at http:\/\/127\.0\.0\.1:11434\./, {
        selector: ".engine-probe-problem",
      }),
    ).toHaveAttribute("role", "alert");
    const checklist = checklistFor();
    expect(within(checklist).getByRole("button", { name: "Probe" }).closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(within(checklist).getByRole("button", { name: "Enable" })).toBeDisabled();
    expect(enableEngineProfile).not.toHaveBeenCalled();
  });

  it("puts starting the server first for managed profiles", async () => {
    vi.mocked(listEngineProfiles).mockResolvedValue([
      engineFixture({ integration_mode: "managed_process" }),
    ]);
    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });
    const checklist = checklistFor();
    expect(
      within(checklist).getByRole("button", { name: "Start server" }).closest("li"),
    ).toHaveAttribute("aria-current", "step");
    expect(within(checklist).getByRole("button", { name: "Probe" })).toBeDisabled();
  });
});

describe("setup after adding an engine", () => {
  function addFromDialog(name: string, mode?: string) {
    fireEvent.click(screen.getByRole("button", { name: "Add manually" }));
    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Profile name" }), {
      target: { value: name },
    });
    if (mode)
      fireEvent.change(within(dialog).getByRole("combobox", { name: "Connection mode" }), {
        target: { value: mode },
      });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add engine" }));
  }

  it("probes and enables a new attached profile automatically", async () => {
    let saved: EngineProfile[] = [];
    vi.mocked(listEngineProfiles).mockImplementation(async () => saved);
    vi.mocked(createEngineProfile).mockImplementation(async (input) => {
      saved = [engineFixture({ label: input.label })];
      return saved[0]!;
    });
    vi.mocked(probeEngineProfile).mockImplementation(async () => {
      saved = [engineFixture({ label: "New engine", last_probed_at: "2026-01-01T00:00:00Z" })];
      return probeFixture;
    });
    vi.mocked(enableEngineProfile).mockImplementation(async () => {
      saved = [
        engineFixture({
          label: "New engine",
          status: "enabled",
          last_probed_at: "2026-01-01T00:00:00Z",
        }),
      ];
      return saved[0]!;
    });

    render(<EnginesScreen />);
    await screen.findByText("No engine profiles are configured.");
    addFromDialog("New engine");

    expect(
      await screen.findByText("New engine is ready. Try it in the Playground or benchmark it."),
    ).toBeInTheDocument();
    const create = vi.mocked(createEngineProfile).mock.invocationCallOrder[0]!;
    const probe = vi.mocked(probeEngineProfile).mock.invocationCallOrder[0]!;
    const enable = vi.mocked(enableEngineProfile).mock.invocationCallOrder[0]!;
    expect(create).toBeLessThan(probe);
    expect(probe).toBeLessThan(enable);
    expect(probeEngineProfile).toHaveBeenCalledWith("profile-1", false);
    expect(within(checklistFor("New engine")).getByText("✓ Enabled")).toBeInTheDocument();
  });

  it("leaves a new profile disabled when its automatic probe fails", async () => {
    let saved: EngineProfile[] = [];
    vi.mocked(listEngineProfiles).mockImplementation(async () => saved);
    vi.mocked(createEngineProfile).mockImplementation(async (input) => {
      saved = [engineFixture({ label: input.label })];
      return saved[0]!;
    });
    vi.mocked(probeEngineProfile).mockImplementation(async () => {
      const response = failedProbe("Nothing is listening on that port.");
      saved = [{ ...response.profile, label: "Offline engine" }];
      return response;
    });

    render(<EnginesScreen />);
    await screen.findByText("No engine profiles are configured.");
    addFromDialog("Offline engine");

    expect(
      await screen.findByText(/Nothing is listening on that port\./, {
        selector: ".engine-probe-problem",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/its probe found a problem\. It stays disabled\./)).toBeInTheDocument();
    expect(enableEngineProfile).not.toHaveBeenCalled();
    expect(screen.getByText("Needs Attention")).toBeInTheDocument();
  });

  it("does not start or probe a new managed profile", async () => {
    let saved: EngineProfile[] = [];
    vi.mocked(listEngineProfiles).mockImplementation(async () => saved);
    vi.mocked(createEngineProfile).mockImplementation(async (input) => {
      saved = [engineFixture({ label: input.label, integration_mode: "managed_process" })];
      return saved[0]!;
    });

    render(<EnginesScreen />);
    await screen.findByText("No engine profiles are configured.");
    addFromDialog("Managed engine", "managed_process");

    expect(
      await screen.findByText("Added Managed engine. Next step: start its server."),
    ).toBeInTheDocument();
    expect(
      within(checklistFor("Managed engine")).getByRole("button", { name: "Start server" }),
    ).toBeInTheDocument();
    expect(probeEngineProfile).not.toHaveBeenCalled();
    expect(startEngineProfile).not.toHaveBeenCalled();
  });
});

describe("scan this machine", () => {
  const scanResult: EngineDiscoveryResult = {
    scanned: [
      { port: 11434, status: "found" },
      { port: 8080, status: "found" },
      { port: 5001, status: "no_response" },
    ],
    candidates: [
      {
        adapter_id: "localforge.ollama",
        display_name: "Ollama",
        endpoint_url: "http://127.0.0.1:11434",
        engine_version: "0.5.7",
        models: ["llama3.2:3b", "qwen2.5:7b", "phi4:14b", "gemma3:4b"],
        evidence: "GET /api/version answered with an Ollama version.",
        existing_profile_ids: [],
      },
      {
        adapter_id: "fake",
        display_name: "Deterministic Fake Engine",
        endpoint_url: "http://127.0.0.1:8080",
        engine_version: null,
        models: [],
        evidence: "Fixture banner.",
        existing_profile_ids: ["profile-1"],
      },
    ],
    duration_ms: 132,
  };

  it("leads the empty state with a scan and lists found and already-added engines", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([catalogFixture, ollamaFixture]);
    vi.mocked(discoverEngines).mockResolvedValue(scanResult);

    render(<EnginesScreen />);
    await screen.findByText("No engine profiles are configured.");
    const scanButton = screen.getByRole("button", { name: "Scan this machine" });
    expect(scanButton).toHaveClass("button--primary");
    expect(screen.getByRole("button", { name: "Add manually" })).toHaveClass("button--quiet");
    fireEvent.click(scanButton);

    const panel = await screen.findByRole("region", { name: "Engines running on this computer" });
    expect(
      within(panel).getByText(/only checks local ports on this computer \(127\.0\.0\.1\)/),
    ).toBeInTheDocument();
    expect(await within(panel).findByRole("heading", { name: "Ollama" })).toBeInTheDocument();
    expect(within(panel).getByText("0.5.7")).toBeInTheDocument();
    expect(within(panel).getByText("http://127.0.0.1:11434")).toBeInTheDocument();
    expect(
      within(panel).getByText("4 models: llama3.2:3b, qwen2.5:7b, phi4:14b and 1 more"),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText("GET /api/version answered with an Ollama version."),
    ).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Add Ollama" })).toBeEnabled();
    expect(within(panel).getByText("Already added")).toBeInTheDocument();
    expect(within(panel).getByText("Checked 3 ports on 127.0.0.1 in 132 ms")).toBeInTheDocument();
    expect(within(panel).getByText("Port 5001: nothing answering")).toBeInTheDocument();
    expect(screen.getByText("Scan finished. Found 2 engines.")).toBeInTheDocument();
  });

  it("links an already-added engine to its profile card", async () => {
    vi.mocked(listEngineProfiles).mockResolvedValue([engineFixture()]);
    vi.mocked(discoverEngines).mockResolvedValue(scanResult);
    render(<EnginesScreen />);
    await screen.findByRole("heading", { name: "Fixture profile" });
    fireEvent.click(screen.getByRole("button", { name: "Scan this machine" }));
    const show = await screen.findByRole("button", {
      name: "Show profile for Deterministic Fake Engine",
    });
    fireEvent.click(show);
    expect(screen.getByRole("heading", { name: "Fixture profile" })).toHaveFocus();
  });

  it("adds a found engine in one click, then probes and enables it", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([catalogFixture, ollamaFixture]);
    let saved: EngineProfile[] = [];
    vi.mocked(listEngineProfiles).mockImplementation(async () => saved);
    vi.mocked(discoverEngines).mockResolvedValue({
      ...scanResult,
      candidates: [scanResult.candidates[0]!],
    });
    const ollamaProfile = (overrides: Partial<EngineProfile> = {}) =>
      engineFixture({
        id: "ollama-1",
        label: "Ollama on this machine",
        adapter_id: "localforge.ollama",
        endpoint_url: "http://127.0.0.1:11434",
        ...overrides,
      });
    vi.mocked(createEngineProfile).mockImplementation(async () => {
      saved = [ollamaProfile()];
      return saved[0]!;
    });
    vi.mocked(probeEngineProfile).mockImplementation(async () => {
      saved = [ollamaProfile({ last_probed_at: "2026-01-01T00:00:00Z" })];
      return { ...probeFixture, profile: saved[0]! };
    });
    vi.mocked(enableEngineProfile).mockImplementation(async () => {
      saved = [ollamaProfile({ status: "enabled", last_probed_at: "2026-01-01T00:00:00Z" })];
      return saved[0]!;
    });

    render(<EnginesScreen />);
    await screen.findByText("No engine profiles are configured.");
    fireEvent.click(screen.getByRole("button", { name: "Scan this machine" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add Ollama" }));

    expect(
      await screen.findByText(
        "Ollama on this machine is ready. Try it in the Playground or benchmark it.",
      ),
    ).toBeInTheDocument();
    expect(createEngineProfile).toHaveBeenCalledWith({
      label: "Ollama on this machine",
      adapter_id: "localforge.ollama",
      engine_display_name: null,
      integration_mode: "attached_endpoint",
      endpoint_url: "http://127.0.0.1:11434",
      user_notes: null,
    });
    expect(probeEngineProfile).toHaveBeenCalledWith("ollama-1", false);
    expect(enableEngineProfile).toHaveBeenCalledWith("ollama-1");
    const order = [createEngineProfile, probeEngineProfile, enableEngineProfile].map(
      (mock) => vi.mocked(mock).mock.invocationCallOrder[0]!,
    );
    expect(order).toEqual([...order].sort((a, b) => a - b));
    const panel = screen.getByRole("region", { name: "Engines running on this computer" });
    expect(within(panel).getByText("Already added")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Add Ollama" })).not.toBeInTheDocument();
  });

  it("explains what to do when nothing is found", async () => {
    vi.mocked(discoverEngines).mockResolvedValue({
      scanned: [{ port: 11434, status: "no_response" }],
      candidates: [],
      duration_ms: 40,
    });
    render(<EnginesScreen />);
    await screen.findByText("No engine profiles are configured.");
    fireEvent.click(screen.getByRole("button", { name: "Scan this machine" }));

    const panel = await screen.findByRole("region", { name: "Engines running on this computer" });
    expect(await within(panel).findByText("No running engines were found.")).toBeInTheDocument();
    expect(within(panel).getByText(/Start your engine/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Scan again" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Add manually" })).toBeInTheDocument();
    expect(screen.getByText("Scan finished. No running engines were found.")).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: "Scan again" }));
    await waitFor(() => expect(discoverEngines).toHaveBeenCalledTimes(2));
  });
});

describe("engine recommendations placement", () => {
  async function renderOverview(profiles: EngineProfile[]) {
    const { fetchSystemProfile } = await import("../lib/api");
    vi.mocked(fetchSystemProfile).mockResolvedValue(machineFixture);
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(listEngineProfiles).mockResolvedValue(profiles);
    vi.mocked(fetchEngineRecommendations).mockResolvedValue(recommendationsFixture());
    render(<OverviewScreen />);
    return screen.findByRole("region", { name: "Best engine fit for this machine" });
  }

  it("leads Overview with recommendations when no engine is set up", async () => {
    const section = await renderOverview([]);
    const welcome = screen.getByRole("heading", {
      name: "Benchmark the model running on your machine.",
    });
    expect(
      section.compareDocumentPosition(welcome) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      await within(section).findByRole("button", { name: "Show all 4 engines" }),
    ).toBeInTheDocument();
  });

  it("shows a compact top-three card lower on Overview once engines exist", async () => {
    const section = await renderOverview([engineFixture()]);
    const welcome = screen.getByRole("heading", {
      name: "Benchmark the model running on your machine.",
    });
    expect(
      section.compareDocumentPosition(welcome) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
    const list = await within(section).findByRole("list", {
      name: "Engines ranked for this machine",
    });
    expect(within(list).getAllByRole("heading", { level: 3 })).toHaveLength(3);
    expect(within(section).queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
  });

  it("sits above the catalog on Engines and adds through the shared setup flow", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([catalogFixture, ollamaFixture]);
    vi.mocked(fetchEngineRecommendations).mockResolvedValue(
      recommendationsFixture([recommendationFixture()]),
    );
    let saved: EngineProfile[] = [];
    vi.mocked(listEngineProfiles).mockImplementation(async () => saved);
    const ollama = (overrides: Partial<EngineProfile> = {}) =>
      engineFixture({
        id: "ollama-1",
        label: "Ollama on this machine",
        adapter_id: "localforge.ollama",
        endpoint_url: "http://127.0.0.1:11434",
        ...overrides,
      });
    vi.mocked(createEngineProfile).mockImplementation(async () => {
      saved = [ollama()];
      return saved[0]!;
    });
    vi.mocked(probeEngineProfile).mockImplementation(async () => {
      saved = [ollama({ last_probed_at: "2026-01-01T00:00:00Z" })];
      return { ...probeFixture, profile: saved[0]! };
    });
    vi.mocked(enableEngineProfile).mockImplementation(async () => {
      saved = [ollama({ status: "enabled", last_probed_at: "2026-01-01T00:00:00Z" })];
      return saved[0]!;
    });

    render(<EnginesScreen />);
    const section = await screen.findByRole("region", { name: "Best engine fit for this machine" });
    const catalog = screen.getByRole("heading", { name: "Engine catalog" });
    expect(
      section.compareDocumentPosition(catalog) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.click(
      await within(section).findByRole("button", { name: "Add Ollama on this machine" }),
    );

    expect(await within(section).findByRole("link", { name: "Benchmark Ollama" })).toHaveAttribute(
      "href",
      "#workloads?engine=ollama-1",
    );
    const order = [createEngineProfile, probeEngineProfile, enableEngineProfile].map(
      (mock) => vi.mocked(mock).mock.invocationCallOrder[0]!,
    );
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(
      screen.getByText(
        "Ollama on this machine is ready. Try it in the Playground or benchmark it.",
      ),
    ).toBeInTheDocument();
    expect(
      within(checklistFor("Ollama on this machine")).getByText("✓ Enabled"),
    ).toBeInTheDocument();
  });

  it("opens the Add dialog in managed mode for an installed llama.cpp", async () => {
    vi.mocked(listEngineCatalog).mockResolvedValue([catalogFixture, llamaCppFixture]);
    vi.mocked(fetchEngineRecommendations).mockResolvedValue(
      recommendationsFixture([
        recommendationFixture({
          adapter_id: "localforge.llama_cpp",
          display_name: "llama.cpp",
          status: "installed",
          endpoint_url: null,
          status_detail: "llama-server was found but is not running.",
        }),
      ]),
    );

    render(<EnginesScreen />);
    const section = await screen.findByRole("region", { name: "Best engine fit for this machine" });
    await screen.findByRole("heading", { name: "llama.cpp / llama-server" });
    fireEvent.click(await within(section).findByText("How to start it"));
    fireEvent.click(within(section).getByRole("button", { name: "Add as managed" }));

    const dialog = screen.getByRole("dialog", { name: "Add an engine" });
    expect(within(dialog).getByRole("combobox", { name: "Connection mode" })).toHaveValue(
      "managed_process",
    );
  });
});
