import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createEngineProfile,
  deleteEngineProfile,
  listEngineCatalog,
  listEngineModels,
  listEngineProfiles,
  listMachineProfiles,
  probeEngineProfile,
  startEngineProfile,
  stopEngineProfile,
  updateEngineProfile,
  type AcceleratorDetails,
  type AdapterManifest,
  type EngineProfile,
  type HardwareDetails,
  type ProbeResponse,
} from "../lib/api";
import { EnginesScreen, OverviewScreen, SystemScreen } from "./WorkspaceScreens";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    createEngineProfile: vi.fn(),
    deleteEngineProfile: vi.fn(),
    fetchSystemProfile: vi.fn(),
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

beforeEach(() => {
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

    fireEvent.click(screen.getByRole("button", { name: "Add your first engine" }));
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
