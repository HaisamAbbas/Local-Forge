import { afterEach, describe, expect, it, vi } from "vitest";

import { discoverEngines, type EngineProfile } from "./api";
import { engineIdFromHash, engineSetupSteps } from "./engine-onboarding";

function profile(overrides: Partial<EngineProfile> = {}): EngineProfile {
  return {
    id: "p1",
    label: "Engine",
    adapter_id: "localforge.ollama",
    engine_display_name: null,
    adapter_version: null,
    integration_mode: "attached_endpoint",
    status: "disabled",
    managed_process_running: false,
    endpoint_url: "http://127.0.0.1:11434",
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

const states = (p: EngineProfile) =>
  Object.fromEntries(engineSetupSteps(p).map((step) => [step.id, step.state]));

afterEach(() => vi.unstubAllGlobals());

describe("engine setup steps", () => {
  it("highlights probe first for a new attached profile", () => {
    expect(states(profile())).toEqual({
      added: "done",
      probe: "next",
      enable: "pending",
      playground: "pending",
      benchmark: "pending",
    });
  });

  it("marks a failed probe and leaves enabling pending", () => {
    const steps = states(profile({ status: "needs_attention", last_probed_at: "2026-01-01" }));
    expect(steps.probe).toBe("failed");
    expect(steps.enable).toBe("pending");
  });

  it("asks a managed profile to start its server before probing", () => {
    const steps = states(profile({ integration_mode: "managed_process" }));
    expect(steps.start).toBe("next");
    expect(steps.probe).toBe("pending");
  });

  it("makes Benchmark the primary step once enabled", () => {
    expect(states(profile({ status: "enabled", last_probed_at: "2026-01-01" }))).toEqual({
      added: "done",
      probe: "done",
      enable: "done",
      playground: "done",
      benchmark: "next",
    });
  });
});

describe("engine deep links", () => {
  it("reads the engine id only for the named screen", () => {
    expect(engineIdFromHash("#workloads?engine=abc", "workloads")).toBe("abc");
    expect(engineIdFromHash("#workloads?engine=abc", "playground")).toBeNull();
    expect(engineIdFromHash("#workloads", "workloads")).toBeNull();
  });
});

describe("discoverEngines", () => {
  it("requests the discovery route, with optional ports", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ scanned: [], candidates: [], duration_ms: 3 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(discoverEngines()).resolves.toEqual({
      scanned: [],
      candidates: [],
      duration_ms: 3,
    });
    await discoverEngines([8080, 5001]);
    expect(fetchMock.mock.calls.map((call) => String((call as unknown[])[0]))).toEqual([
      "/api/v1/engines/discover",
      "/api/v1/engines/discover?ports=8080,5001",
    ]);
  });
});
