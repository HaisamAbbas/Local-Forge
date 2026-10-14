import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  listEngineCatalog,
  listEngineModels,
  listEngineProfiles,
  type AdapterManifest,
  type EngineModel,
  type EngineProfile,
} from "../lib/api";
import { PlaygroundScreen } from "./PlaygroundScreen";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    listEngineCatalog: vi.fn(),
    listEngineModels: vi.fn(),
    listEngineProfiles: vi.fn(),
    streamPlaygroundPrompt: vi.fn(),
  };
});

const manifest: AdapterManifest = {
  adapter_id: "localforge.ollama",
  adapter_version: "1.0.0",
  display_name: "Ollama",
  description: "Attach to an existing local Ollama server.",
  category: "native_api",
  integration_modes: ["attached_endpoint"],
  operating_systems: [],
  implementation_status: "implemented",
  version_tested: [],
  maintenance_mode: false,
  recommended_for_new_managed_install: true,
  status_details: null,
  declared_capabilities: ["chat_completion", "streaming"],
  supported_transports: ["http"],
  native_metrics: [],
  config_schema: {},
  known_limitations: [],
  documentation_url: null,
};

function profile(id: string, label: string): EngineProfile {
  return {
    id,
    label,
    adapter_id: "localforge.ollama",
    engine_display_name: null,
    adapter_version: "1.0.0",
    integration_mode: "attached_endpoint",
    status: "enabled",
    managed_process_running: false,
    endpoint_url: "http://127.0.0.1:11434",
    is_remote: false,
    remote_access_enabled: false,
    engine_version: "0.5.7",
    last_probed_at: "2026-01-01T00:00:00Z",
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
  };
}

function model(id: string, engineId: string, name: string): EngineModel {
  return {
    id,
    engine_profile_id: engineId,
    model_ref: name,
    display_name: name,
    revision: null,
    format: null,
    size_bytes: null,
    tokenizer_identity: null,
    max_context_tokens: null,
    status: "available",
  };
}

beforeEach(() => {
  vi.mocked(listEngineCatalog).mockResolvedValue([manifest]);
  vi.mocked(listEngineProfiles).mockResolvedValue([
    profile("engine-1", "First engine"),
    profile("engine-2", "Second engine"),
  ]);
  vi.mocked(listEngineModels).mockImplementation(async (id) =>
    id === "engine-2"
      ? [model("model-2a", id, "qwen2.5:7b"), model("model-2b", id, "phi4:14b")]
      : [model("model-1", id, "llama3.2:3b")],
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.location.hash = "";
});

describe("playground deep links", () => {
  it("selects the first ready model of the engine named in #playground?engine=", async () => {
    window.location.hash = "#playground?engine=engine-2";
    render(<PlaygroundScreen />);
    await waitFor(() => expect(screen.getByLabelText("Connected model")).toHaveValue("model-2a"));
  });

  it("ignores an unknown engine and keeps the default selection", async () => {
    window.location.hash = "#playground?engine=missing";
    render(<PlaygroundScreen />);
    await waitFor(() => expect(screen.getByLabelText("Connected model")).toHaveValue("model-1"));
  });

  it("follows a deep link that arrives while the screen is open", async () => {
    window.location.hash = "#playground";
    render(<PlaygroundScreen />);
    await waitFor(() => expect(screen.getByLabelText("Connected model")).toHaveValue("model-1"));
    window.location.hash = "#playground?engine=engine-2";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await waitFor(() => expect(screen.getByLabelText("Connected model")).toHaveValue("model-2a"));
  });
});
