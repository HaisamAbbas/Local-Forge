import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EngineProfile, ModelRecord } from "../lib/api";
import { ModelsScreen } from "./ModelsScreen";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function profile(id: string, label: string, overrides: Partial<EngineProfile> = {}) {
  return {
    id,
    label,
    adapter_id: "localforge.ollama",
    engine_display_name: "Ollama",
    integration_mode: "attached_endpoint",
    status: "enabled",
    managed_process_running: false,
    is_remote: false,
    ...overrides,
  } as EngineProfile;
}

function model(overrides: Partial<ModelRecord>): ModelRecord {
  return {
    id: "model-1",
    model_ref: "llama3.2:3b",
    display_name: "Llama 3.2 3B",
    status: "available",
    engine_profile_id: "engine-a",
    engine_label: "Local Ollama",
    adapter_id: "localforge.ollama",
    format: "gguf",
    quantization: "Q4_K_M",
    size_bytes: 2 * 1024 ** 3,
    max_context_tokens: 131072,
    revision: "a80c4f17",
    tokenizer_identity: null,
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

const probeResult = {
  profile: profile("engine-a", "Local Ollama"),
  result: { status: "succeeded", models: [{}, {}] },
};

function mockApi(options: { models: ModelRecord[]; remoteProbe?: boolean }) {
  const probes: Array<{ url: string; body: unknown }> = [];
  let modelLoads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/session") return json({ token: "session-token" });
      if (url === "/api/v1/models") {
        modelLoads += 1;
        return json(options.models);
      }
      if (url === "/api/v1/engines")
        return json([profile("engine-a", "Local Ollama"), profile("engine-b", "Empty engine")]);
      if (url.endsWith("/probe")) {
        const body = JSON.parse(String(init?.body)) as { confirm_remote_egress: boolean };
        probes.push({ url, body });
        if (options.remoteProbe && !body.confirm_remote_egress)
          return json(
            {
              detail: {
                code: "remote_confirmation_required",
                message: "Remote egress needs confirmation.",
                host: "gpu.example.test",
              },
            },
            409,
          );
        return json(probeResult);
      }
      return json({ detail: { message: "Unexpected request" } }, 500);
    }),
  );
  return { probes, modelLoads: () => modelLoads };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ModelsScreen", () => {
  it("groups models by engine and shows unknown for unreported fields", async () => {
    mockApi({
      models: [
        model({}),
        model({
          id: "model-2",
          model_ref: "mistral:latest",
          display_name: null,
          status: "missing",
          format: null,
          quantization: null,
          size_bytes: null,
          max_context_tokens: null,
          revision: null,
        }),
      ],
    });
    render(<ModelsScreen />);

    const group = await screen.findByRole("region", { name: "Local Ollama" });
    const known = within(group).getByRole("row", { name: /Llama 3.2 3B/ });
    expect(known).toHaveTextContent("Available");
    expect(known).toHaveTextContent("Q4_K_M");
    expect(known).toHaveTextContent("2 GB");
    expect(known).toHaveTextContent(`${(131072).toLocaleString()} tokens`);
    expect(known).toHaveTextContent("a80c4f17");

    const unknown = within(group).getByRole("row", { name: /mistral:latest/ });
    expect(unknown).toHaveTextContent("Missing");
    expect(within(unknown).getAllByText("Unknown")).toHaveLength(5);
    expect(unknown).not.toHaveTextContent(/\b0 (B|tokens)\b/);

    const empty = screen.getByRole("region", { name: "Empty engine" });
    expect(empty).toHaveTextContent("No models recorded for this engine.");
    expect(within(group).getByRole("link", { name: "View engine" })).toHaveAttribute(
      "href",
      "#engines",
    );
  });

  it("refreshes an engine through the probe API and reloads the inventory", async () => {
    const api = mockApi({ models: [model({})] });
    render(<ModelsScreen />);
    const group = await screen.findByRole("region", { name: "Local Ollama" });

    fireEvent.click(
      within(group).getByRole("button", { name: "Refresh from engine Local Ollama" }),
    );

    expect(
      await within(group).findByText("Probe succeeded · 2 models reported."),
    ).toBeInTheDocument();
    expect(api.probes).toEqual([
      { url: "/api/v1/engines/engine-a/probe", body: { confirm_remote_egress: false } },
    ]);
    await waitFor(() => expect(api.modelLoads()).toBe(2));
  });

  it("asks inline before contacting a remote engine", async () => {
    const api = mockApi({ models: [model({})], remoteProbe: true });
    render(<ModelsScreen />);
    const group = await screen.findByRole("region", { name: "Local Ollama" });

    fireEvent.click(
      within(group).getByRole("button", { name: "Refresh from engine Local Ollama" }),
    );
    expect(await within(group).findByText("gpu.example.test")).toBeInTheDocument();
    fireEvent.click(within(group).getByRole("button", { name: "Contact remote engine" }));

    expect(
      await within(group).findByText("Probe succeeded · 2 models reported."),
    ).toBeInTheDocument();
    expect(api.probes.map((probe) => probe.body)).toEqual([
      { confirm_remote_egress: false },
      { confirm_remote_egress: true },
    ]);
  });
});
