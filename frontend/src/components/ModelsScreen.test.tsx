import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  EngineProfile,
  ModelPull,
  ModelRecord,
  ModelSuggestion,
  ModelSuggestionsResult,
} from "../lib/api";
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
      if (url.startsWith("/api/v1/models/suggestions")) return json(suggestions("chat", []));
      if (url === "/api/v1/models/pulls") return json([]);
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

function suggestion(overrides: Partial<ModelSuggestion> = {}): ModelSuggestion {
  return {
    rank: 1,
    model_id: "llama-3.2-3b-instruct",
    name: "Llama 3.2 3B Instruct",
    family: "Llama 3.2",
    publisher: "Meta",
    params_b: 3.2,
    active_params_b: null,
    q4_size_gb: 2,
    download_size_gb: 2,
    context_tokens: 131072,
    license: "Llama 3.2 Community License",
    strengths: ["chat", "multilingual", "small_fast"],
    notes: null,
    fit: "fits",
    memory_required_gb: 2.9,
    speed: {
      tokens_per_second: 10.7,
      basis: "measured",
      label: "estimated from your runs",
      verdict: "comfortable for chat",
    },
    score: 78,
    reasons: ["Fits comfortably: needs about 2.9 GB of the 27.3 GB available for models"],
    cautions: [],
    downloaded: [],
    runnable_with: ["localforge.ollama"],
    sources: {
      ollama_tag: "llama3.2:3b",
      gguf_repo: "bartowski/Llama-3.2-3B-Instruct-GGUF",
      gguf_file: "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
      gguf_url: "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF",
      openvino_repo: null,
      onnx_repo: null,
    },
    ...overrides,
  };
}

function suggestions(
  useCase: ModelSuggestionsResult["use_case"],
  items: ModelSuggestion[],
  targets: ModelSuggestionsResult["pull_targets"] = [
    {
      engine_profile_id: "engine-a",
      label: "Local Ollama",
      endpoint_url: "http://127.0.0.1:11434",
      enabled: true,
    },
  ],
): ModelSuggestionsResult {
  return {
    generated_at: "2026-10-14T00:00:00Z",
    use_case: useCase,
    use_case_label: useCase === "chat" ? "Chat" : "Coding",
    use_cases: ["chat", "coding", "document_qa", "small_fast", "reasoning"],
    target_tokens_per_second: 10,
    catalog_version: "2026.10.1",
    catalog_date: "2026-10-14",
    method: "Ranked from a curated catalog.",
    machine_summary: "Windows 11 · 31.5 GB RAM",
    memory_basis: "system_memory",
    memory_budget_bytes: 27_300_000_000,
    speed_basis: "measured",
    read_gib_per_second: 19.6,
    ollama_reachable: true,
    pull_targets: targets,
    suggestions: items,
  };
}

function pullView(overrides: Partial<ModelPull>): ModelPull {
  return {
    id: "pull-1",
    engine_profile_id: "engine-a",
    engine_label: "Local Ollama",
    ollama_tag: "llama3.2:3b",
    catalog_model_id: "llama-3.2-3b-instruct",
    status: "running",
    phase: "pulling manifest",
    bytes_completed: null,
    bytes_total: null,
    percent: null,
    error: null,
    probe_status: "not_started",
    probe_message: null,
    started_at: "2026-10-14T00:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

function mockSuggestionApi(options: {
  chat: ModelSuggestion[];
  coding?: ModelSuggestion[];
  targets?: ModelSuggestionsResult["pull_targets"];
  pollSequence?: ModelPull[];
}) {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  const polls = [...(options.pollSequence ?? [])];
  let modelLoads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (url === "/api/v1/session") return json({ token: "session-token" });
      if (url.startsWith("/api/v1/models/suggestions")) {
        const coding = url.endsWith("use_case=coding");
        return json(
          suggestions(
            coding ? "coding" : "chat",
            coding ? (options.coding ?? []) : options.chat,
            options.targets,
          ),
        );
      }
      if (url === "/api/v1/models/pulls") return json([]);
      if (url === "/api/v1/models/pull") return json(pullView({ phase: "pulling manifest" }), 202);
      if (url === "/api/v1/models/pulls/pull-1/cancel")
        return json(pullView({ status: "cancelled", phase: "cancelled" }));
      if (url === "/api/v1/models/pulls/pull-1")
        return json(polls.length > 1 ? polls.shift() : (polls[0] ?? pullView({})));
      if (url === "/api/v1/models") {
        modelLoads += 1;
        return json([]);
      }
      if (url === "/api/v1/engines") return json([profile("engine-a", "Local Ollama")]);
      return json({ detail: { message: "Unexpected request" } }, 500);
    }),
  );
  return { calls, modelLoads: () => modelLoads };
}

describe("ModelsScreen suggestions", () => {
  it("shows ranked model cards with fit, estimated speed, download state and sources", async () => {
    const api = mockSuggestionApi({
      chat: [
        suggestion({
          rank: 1,
          model_id: "qwen2.5-3b-instruct",
          name: "Qwen2.5 3B Instruct",
          license: "Qwen Research License",
          downloaded: [
            {
              source: "ollama",
              model_ref: "qwen2.5:3b",
              engine_profile_id: "engine-a",
              engine_label: "Local Ollama",
              adapter_id: "localforge.ollama",
            },
          ],
          sources: { ...suggestion().sources, ollama_tag: "qwen2.5:3b" },
        }),
        suggestion({
          rank: 2,
          fit: "tight",
          cautions: ["Tight fit: close other apps"],
          speed: {
            tokens_per_second: 4.2,
            basis: "measured",
            label: "estimated from your runs",
            verdict: "usable for batch work, slow for chat",
          },
        }),
      ],
      coding: [suggestion({ model_id: "coder", name: "Qwen2.5-Coder 1.5B Instruct" })],
    });
    render(<ModelsScreen />);

    const section = await screen.findByRole("region", { name: "Suggested for this machine" });
    const first = await within(section).findByRole("listitem", { name: "Qwen2.5 3B Instruct" });
    expect(first).toHaveTextContent("Fits");
    expect(first).toHaveTextContent("Downloaded");
    expect(first).toHaveTextContent("~11 tok/s · estimated from your runs");
    expect(first).toHaveTextContent("Qwen Research License");
    expect(within(first).getByRole("link", { name: "Benchmark" })).toHaveAttribute(
      "href",
      "#workloads?engine=engine-a",
    );
    expect(within(first).queryByRole("button", { name: "Pull with Ollama" })).toBeNull();

    const second = within(section).getByRole("listitem", { name: "Llama 3.2 3B Instruct" });
    expect(second).toHaveTextContent("Tight fit");
    expect(second).toHaveTextContent("usable for batch work, slow for chat");
    expect(second).toHaveTextContent("Caution: Tight fit: close other apps");
    expect(within(second).getByRole("link", { name: "Get the GGUF" })).toHaveAttribute(
      "href",
      "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF",
    );
    expect(second).toHaveTextContent("Llama-3.2-3B-Instruct-Q4_K_M.gguf");
    expect(section).toHaveTextContent("Speeds are estimated from your runs");
    expect(section).toHaveTextContent("Catalog 2026.10.1");

    fireEvent.click(within(section).getByRole("tab", { name: "Coding" }));
    expect(
      await within(section).findByRole("listitem", { name: "Qwen2.5-Coder 1.5B Instruct" }),
    ).toBeInTheDocument();
    expect(within(section).getByRole("tab", { name: "Coding" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(api.calls.some((call) => call.url.endsWith("use_case=coding"))).toBe(true);
    expect(api.calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("asks for confirmation with the download size, then shows progress and the result", async () => {
    const api = mockSuggestionApi({
      chat: [suggestion({})],
      pollSequence: [
        pullView({
          phase: "pulling aaa",
          bytes_completed: 800_000_000,
          bytes_total: 2_000_000_000,
          percent: 40,
        }),
        pullView({
          status: "succeeded",
          phase: "success",
          bytes_completed: 2_000_000_000,
          bytes_total: 2_000_000_000,
          percent: 100,
          probe_status: "succeeded",
          probe_message: "Engine refreshed · 3 models reported.",
        }),
      ],
    });
    render(<ModelsScreen pullPollIntervalMs={5} />);
    const card = await screen.findByRole("listitem", { name: "Llama 3.2 3B Instruct" });

    fireEvent.click(within(card).getByRole("button", { name: "Pull with Ollama" }));
    const confirm = within(card).getByRole("group", {
      name: "Confirm download of Llama 3.2 3B Instruct",
    });
    expect(confirm).toHaveTextContent(
      "Download about 2 GB (llama3.2:3b) with Ollama on Local Ollama?",
    );
    expect(confirm).toHaveTextContent("registry.ollama.ai");
    expect(api.calls.some((call) => call.url === "/api/v1/models/pull")).toBe(false);

    fireEvent.click(within(confirm).getByRole("button", { name: "Download 2 GB" }));
    const progress = await within(card).findByRole("progressbar", {
      name: "Download progress for Llama 3.2 3B Instruct",
    });
    expect(progress).toBeInTheDocument();
    const pullCall = api.calls.find((call) => call.url === "/api/v1/models/pull");
    expect(pullCall?.body).toEqual({
      engine_profile_id: "engine-a",
      ollama_tag: "llama3.2:3b",
      confirm: true,
    });

    expect(
      await within(card).findByText(/40% · 800 MB of 2.0 GB · pulling aaa/),
    ).toBeInTheDocument();
    expect(
      await within(card).findByText(
        /Downloaded with Ollama\. Engine refreshed · 3 models reported\./,
      ),
    ).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Benchmark" })).toHaveAttribute(
      "href",
      "#workloads?engine=engine-a",
    );
    await waitFor(() => expect(api.modelLoads()).toBeGreaterThanOrEqual(2));
  });

  it("cancels in the confirmation step without calling the API, and can cancel a download", async () => {
    const api = mockSuggestionApi({ chat: [suggestion({})] });
    render(<ModelsScreen pullPollIntervalMs={5000} />);
    const card = await screen.findByRole("listitem", { name: "Llama 3.2 3B Instruct" });

    fireEvent.click(within(card).getByRole("button", { name: "Pull with Ollama" }));
    fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(within(card).getByRole("button", { name: "Pull with Ollama" })).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "POST")).toBe(false);

    fireEvent.click(within(card).getByRole("button", { name: "Pull with Ollama" }));
    fireEvent.click(within(card).getByRole("button", { name: "Download 2 GB" }));
    fireEvent.click(await within(card).findByRole("button", { name: "Cancel download" }));
    expect(await within(card).findByText(/Download cancelled\./)).toBeInTheDocument();
    expect(
      api.calls.some(
        (call) => call.url === "/api/v1/models/pulls/pull-1/cancel" && call.method === "POST",
      ),
    ).toBe(true);
  });

  it("points to Engines when there is no enabled local Ollama to pull with", async () => {
    mockSuggestionApi({ chat: [suggestion({})], targets: [] });
    render(<ModelsScreen />);
    const card = await screen.findByRole("listitem", { name: "Llama 3.2 3B Instruct" });
    expect(card).toHaveTextContent("Add a local Ollama engine to pull this model.");
    expect(within(card).getByRole("link", { name: "Open Engines" })).toHaveAttribute(
      "href",
      "#engines",
    );
    expect(within(card).queryByRole("button", { name: "Pull with Ollama" })).toBeNull();
  });
});
