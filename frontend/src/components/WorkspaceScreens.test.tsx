import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  updateEngineProfile,
  type AdapterManifest,
  type EngineProfile,
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
  declared_capabilities: ["chat_completion", "streaming", "model_inventory"],
  supported_transports: ["http", "https", "server_sent_events"],
  native_metrics: [],
  config_schema: {},
  known_limitations: ["The underlying engine remains unknown unless named by the user."],
  documentation_url: null,
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
