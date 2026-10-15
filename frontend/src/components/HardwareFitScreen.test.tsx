import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HardwareFitModel, HardwareFitResult } from "../lib/api";
import { HardwareFitScreen } from "./HardwareFitScreen";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fitModel(overrides: Partial<HardwareFitModel>): HardwareFitModel {
  return {
    model_id: "m",
    name: "Model",
    family: "Fam",
    publisher: "Pub",
    params_b: 7,
    active_params_b: null,
    context_tokens: 32768,
    license: "Apache-2.0",
    strengths: ["chat"],
    notes: null,
    quantization: "Q4_K_M",
    quantization_bits: 4.85,
    lower_quality: false,
    size_gb: 4.4,
    memory_required_gb: 5.4,
    run_mode: "cpu",
    tier: "good",
    tokens_per_second: null,
    speed_basis: "unknown",
    speed_label: "unknown",
    score: 60,
    explanation: "Runs on the CPU from system RAM.",
    downloaded: false,
    sources: {
      ollama_tag: null,
      gguf_repo: null,
      gguf_file: null,
      gguf_url: null,
      openvino_repo: null,
      onnx_repo: null,
    },
    ...overrides,
  };
}

const result: HardwareFitResult = {
  generated_at: "2026-10-15T00:00:00Z",
  catalog_version: "test",
  method: "Estimates only.",
  detected_by: "a fresh scan of this machine",
  machine: {
    summary: "Test machine",
    os: "Windows",
    cpu_model: "Test CPU",
    cpu_logical_cores: 8,
    cpu_physical_cores: 4,
    memory_total_gb: 16,
    accelerators: [
      {
        name: "Intel Graphics",
        vendor: "Intel",
        memory_gb: null,
        kind: "integrated",
        runtime: null,
        driver_version: null,
      },
    ],
    accelerators_detected: true,
    compute_path: "cpu_only",
    compute_path_label: "CPU and system RAM (no discrete GPU detected)",
    gpu_budget_gb: null,
    ram_budget_gb: 10,
    notes: ["An integrated GPU shares system RAM, so it does not add memory."],
  },
  models: [
    fitModel({
      model_id: "small",
      name: "Small Chat 1B",
      params_b: 1,
      tier: "good",
      strengths: ["chat", "small_fast"],
      sources: {
        ollama_tag: "small:1b",
        gguf_repo: null,
        gguf_file: null,
        gguf_url: null,
        openvino_repo: null,
        onnx_repo: null,
      },
    }),
    fitModel({
      model_id: "code",
      name: "Coder 7B",
      tier: "marginal",
      strengths: ["coding"],
      downloaded: true,
    }),
    fitModel({
      model_id: "huge",
      name: "Huge 70B",
      params_b: 70,
      tier: "too_tight",
      run_mode: "none",
      quantization: "Q2_K",
      explanation: "Needs about 29 GB even at the smallest quantization.",
    }),
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockApi(urls: string[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      urls.push(url);
      if (url.startsWith("/api/v1/hardware-fit")) return json(result);
      return json({ detail: { message: "Unexpected request" } }, 500);
    }),
  );
}

describe("HardwareFitScreen", () => {
  it("shows the detected machine and hides models that do not run well by default", async () => {
    mockApi();
    render(<HardwareFitScreen />);

    expect(await screen.findByText("Test CPU")).toBeInTheDocument();
    expect(screen.getByText("4 cores, 8 threads")).toBeInTheDocument();
    expect(screen.getByText(/Intel Graphics \(integrated, shares RAM\)/)).toBeInTheDocument();
    expect(screen.getByText(/integrated GPU shares system RAM/)).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 catalog models run well here/)).toBeInTheDocument();

    expect(screen.getByRole("row", { name: "Small Chat 1B" })).toHaveTextContent("Good fit");
    expect(screen.queryByRole("row", { name: "Coder 7B" })).not.toBeInTheDocument();
    expect(screen.queryByRole("row", { name: "Huge 70B" })).not.toBeInTheDocument();
    expect(screen.getByText("ollama pull small:1b")).toBeInTheDocument();
  });

  it("filters by what runs, use, download state and search", async () => {
    mockApi();
    render(<HardwareFitScreen />);
    await screen.findByText("Test CPU");

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "all" } });
    expect(screen.getAllByRole("row")).toHaveLength(1 + 3);
    const huge = screen.getByRole("row", { name: "Huge 70B" });
    expect(huge).toHaveTextContent("Too large");
    expect(huge).toHaveTextContent("Smallest: Q2_K");

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "downloaded" } });
    expect(screen.getByRole("row", { name: "Coder 7B" })).toHaveTextContent("Downloaded");
    expect(screen.queryByRole("row", { name: "Small Chat 1B" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Good for"), { target: { value: "coding" } });
    expect(screen.getByRole("row", { name: "Coder 7B" })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: "Small Chat 1B" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Good for"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Search"), { target: { value: "nothing like this" } });
    expect(screen.getByText(/No models match these filters/)).toBeInTheDocument();
  });

  it("detects again on request", async () => {
    const urls: string[] = [];
    mockApi(urls);
    render(<HardwareFitScreen />);
    await screen.findByText("Test CPU");

    fireEvent.click(screen.getByRole("button", { name: "Detect again" }));
    await waitFor(() => expect(urls).toContain("/api/v1/hardware-fit?refresh=true"));
    expect(await screen.findByRole("button", { name: "Detect again" })).toBeEnabled();
  });

  it("reports a load failure and lets the user retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: { message: "Detection failed." } }, 500)),
    );
    render(<HardwareFitScreen />);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
