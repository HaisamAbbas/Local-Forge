import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EngineProfile, TuneJob } from "../lib/api";
import { EngineTuner } from "./EngineTuner";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const profile = {
  id: "engine-1",
  label: "My llama.cpp",
  adapter_id: "localforge.llama_cpp",
  integration_mode: "managed_process",
  managed_process_running: false,
  engine_extensions: { llama_cpp: { executable_path: "llama-server", model_path: "m.gguf" } },
} as unknown as EngineProfile;

function job(overrides: Partial<TuneJob>): TuneJob {
  return {
    id: "job-1",
    engine_profile_id: "engine-1",
    engine_label: "My llama.cpp",
    objective: "throughput",
    objective_description: "generation speed (tokens per second)",
    status: "running",
    started_at: "2026-10-15T00:00:00Z",
    finished_at: null,
    total_trials: 3,
    trials: [],
    min_gain_percent: 5,
    baseline_score: null,
    best_score: null,
    improvement_percent: null,
    best_settings: {},
    recommended_launch_settings: null,
    error: null,
    stopping: false,
    ...overrides,
  };
}

const completed = job({
  status: "completed",
  trials: [
    {
      settings: {},
      changed: null,
      score: 20,
      tokens_per_second: 20,
      ttft_ms: 100,
      latency_ms: 50,
      error: null,
    },
    {
      settings: { threads: 8 },
      changed: "threads",
      score: 30,
      tokens_per_second: 30,
      ttft_ms: 100,
      latency_ms: 40,
      error: null,
    },
  ],
  best_settings: { threads: 8 },
  improvement_percent: 50,
  baseline_score: 20,
  best_score: 30,
  recommended_launch_settings: {
    executable_path: "llama-server",
    model_path: "m.gguf",
    threads: 8,
  },
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EngineTuner", () => {
  it("starts a tune and shows progress", async () => {
    const requests: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/session") return json({ token: "session-token" });
        if (url === "/api/v1/tunes" && init?.method === "POST") {
          requests.push({ url, body: JSON.parse(String(init.body)) });
          return json(job({ trials: [] }), 202);
        }
        if (url === "/api/v1/tunes") return json([]);
        return json({ detail: { message: "Unexpected request" } }, 500);
      }),
    );
    render(<EngineTuner profile={profile} onApplied={() => undefined} />);

    fireEvent.change(screen.getByLabelText("Optimise for"), { target: { value: "ttft" } });
    fireEvent.click(screen.getByRole("button", { name: "Start tuning" }));

    expect(await screen.findByText(/Trial 1 of 3/)).toBeInTheDocument();
    expect(requests[0]?.body).toEqual({ engine_profile_id: "engine-1", objective: "ttft" });
    expect(screen.getByRole("button", { name: "Stop tuning" })).toBeInTheDocument();
  });

  it("shows the best settings of a finished tune and applies them to the profile", async () => {
    const patches: unknown[] = [];
    const onApplied = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/v1/session") return json({ token: "session-token" });
        if (url === "/api/v1/tunes") return json([completed]);
        if (url === "/api/v1/engines/engine-1" && init?.method === "PATCH") {
          patches.push(JSON.parse(String(init.body)));
          return json(profile);
        }
        return json({ detail: { message: "Unexpected request" } }, 500);
      }),
    );
    render(<EngineTuner profile={profile} onApplied={onApplied} />);

    expect(await screen.findByText(/Best: CPU threads 8/)).toBeInTheDocument();
    expect(screen.getByText(/\+50% vs your current settings/)).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Current settings 20 tokens\/s/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Apply these settings" }));
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(patches[0]).toMatchObject({
      engine_extensions: { llama_cpp: { threads: 8, model_path: "m.gguf" } },
    });
    expect(await screen.findByText(/used the next time the server starts/)).toBeInTheDocument();
  });

  it("says when the current settings are already good and offers nothing to apply", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/session") return json({ token: "session-token" });
        if (url === "/api/v1/tunes")
          return json([
            job({
              status: "completed",
              best_settings: {},
              recommended_launch_settings: profile.engine_extensions?.llama_cpp ?? {},
            }),
          ]);
        return json({ detail: { message: "Unexpected request" } }, 500);
      }),
    );
    render(<EngineTuner profile={profile} onApplied={() => undefined} />);
    expect(await screen.findByText(/already about as good/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply these settings" })).not.toBeInTheDocument();
  });

  it("reports a failed tune", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/session") return json({ token: "session-token" });
        if (url === "/api/v1/tunes")
          return json([job({ status: "failed", error: "the engine did not start" })]);
        return json({ detail: { message: "Unexpected request" } }, 500);
      }),
    );
    render(<EngineTuner profile={profile} onApplied={() => undefined} />);
    expect(await screen.findByText(/the engine did not start/)).toBeInTheDocument();
  });
});
