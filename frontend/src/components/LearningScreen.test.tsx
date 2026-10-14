import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getRun,
  listBenchmarkPlans,
  listRunMetrics,
  listRuns,
  type BenchmarkPlan,
  type BenchmarkRun,
  type MetricSample,
} from "../lib/api";
import { LearningScreen } from "./LearningScreen";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    listRuns: vi.fn(),
    getRun: vi.fn(),
    listRunMetrics: vi.fn(),
    listBenchmarkPlans: vi.fn(),
  };
});

const RUN_A = "0f8fad5b-d9cb-469f-a165-70867728950e";
const RUN_B = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

function run(id: string, status: string, completedAt: string): BenchmarkRun {
  return {
    id,
    plan_id: "plan-1",
    status,
    worker_id: null,
    potentially_contended: false,
    started_at: completedAt,
    completed_at: completedAt,
    error_code: null,
    error_message: null,
    created_at: completedAt,
    updated_at: completedAt,
  };
}

function metric(name: string, value: number | null, aggregation = "mean"): MetricSample {
  return {
    id: `${name}-${aggregation}`,
    run_id: RUN_A,
    metric_name: name,
    value,
    unit: "ms",
    source: "client_measured",
    aggregation,
    sample_count: 40,
    definition: "",
    formula: "",
    method_version: "localforge.metrics.v1",
    quality_flags: [],
    metric_kind: "gauge",
    adapter_id: null,
    adapter_version: null,
    engine_version: null,
    observed_at: "2026-10-14T10:00:00Z",
    labels: {},
  };
}

const plan = {
  id: "plan-1",
  name: "Chat baseline",
  concurrency: 1,
  configuration_snapshot: { workload: { traffic_mode: "closed_loop" } },
} as unknown as BenchmarkPlan;

beforeEach(() => {
  window.location.hash = "#learning";
  vi.mocked(listRuns).mockResolvedValue([
    run(RUN_A, "completed", "2026-10-14T10:00:00Z"),
    run(RUN_B, "failed", "2026-10-14T11:00:00Z"),
  ]);
  vi.mocked(listBenchmarkPlans).mockResolvedValue([plan]);
  vi.mocked(listRunMetrics).mockResolvedValue({
    metrics: [metric("ttft_ms", 900), metric("request_latency_ms", 1000)],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.location.hash = "";
});

describe("learning screen", () => {
  it("lists glossary entries and filters them by search", () => {
    render(<LearningScreen />);
    const glossary = screen.getByRole("region", { name: "Metric glossary" });
    expect(glossary.querySelector("#metric-ttft_ms")).not.toBeNull();

    fireEvent.change(within(glossary).getByLabelText("Search metrics"), {
      target: { value: "rss" },
    });
    expect(glossary.querySelector("#metric-worker_process_rss_bytes")).not.toBeNull();
    expect(glossary.querySelector("#metric-ttft_ms")).toBeNull();
    expect(within(glossary).getByText("1 metric shown")).toBeInTheDocument();
  });

  it("filters by category with pressed-state buttons", () => {
    render(<LearningScreen />);
    const glossary = screen.getByRole("region", { name: "Metric glossary" });
    const system = within(glossary).getByRole("button", { name: "Machine resources" });
    fireEvent.click(system);
    expect(system).toHaveAttribute("aria-pressed", "true");
    expect(glossary.querySelector("#metric-host_cpu_percent")).not.toBeNull();
    expect(glossary.querySelector("#metric-request_latency_ms")).toBeNull();
  });

  it("opens the deep-linked metric from the hash", async () => {
    window.location.hash = "#learning?metric=ollama.load_duration";
    render(<LearningScreen />);
    const entry = document.getElementById("metric-ollama-load_duration");
    expect(entry).toHaveAttribute("open");
    expect(entry).toHaveClass("learning-metric--selected");
    expect(document.getElementById("metric-ttft_ms")).not.toHaveAttribute("open");

    window.location.hash = "#learning?metric=ttft_ms";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await waitFor(() => expect(document.getElementById("metric-ttft_ms")).toHaveAttribute("open"));
  });

  it("shows preset and classic experiment guides linked to workloads", () => {
    render(<LearningScreen />);
    const guides = screen.getByRole("region", { name: "Experiment guides" });
    expect(
      within(guides).getByRole("heading", { name: "Fixed prompt prefill" }),
    ).toBeInTheDocument();
    expect(
      within(guides).getByRole("heading", { name: "Output length versus TPOT" }),
    ).toBeInTheDocument();
    const build = within(guides).getAllByRole("link", { name: /Build this workload/ });
    expect(build.length).toBe(10);
    for (const link of build) expect(link).toHaveAttribute("href", "#workloads");
    expect(
      within(guides).getAllByRole("link", { name: "Time to first token (TTFT)" })[0],
    ).toHaveAttribute("href", "#learning?metric=ttft_ms");
  });

  it("diagnoses the most recent completed run with linked evidence", async () => {
    render(<LearningScreen />);
    const panel = screen.getByRole("region", { name: "Diagnose a run" });
    await within(panel).findByRole("heading", { name: "Share of latency before the first token" });
    expect(listRunMetrics).toHaveBeenCalledWith(RUN_A);
    expect(within(panel).getByText(/consistent with prompt processing/)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Mean TTFT" })).toHaveAttribute(
      "href",
      "#learning?metric=ttft_ms",
    );
    expect(within(panel).getByText(/Not enough data \(/)).toBeInTheDocument();
    const select = within(panel).getByLabelText("Completed run");
    expect(within(select).getAllByRole("option")).toHaveLength(1);
    expect(within(select).getByRole("option").textContent).toMatch(/Chat baseline/);
  });

  it("fetches a deep-linked run that is missing from the list", async () => {
    const linked = "9b2d5f3a-1c4e-4a6b-8d7f-0a1b2c3d4e5f";
    window.location.hash = `#learning?run=${linked}`;
    vi.mocked(getRun).mockResolvedValue(run(linked, "completed", "2026-10-14T12:00:00Z"));
    render(<LearningScreen />);
    await waitFor(() => expect(listRunMetrics).toHaveBeenCalledWith(linked));
    expect(getRun).toHaveBeenCalledWith(linked);
  });

  it("explains when there are no completed runs", async () => {
    vi.mocked(listRuns).mockResolvedValue([run(RUN_B, "failed", "2026-10-14T11:00:00Z")]);
    render(<LearningScreen />);
    expect(await screen.findByText(/No completed runs yet/)).toBeInTheDocument();
  });

  it("shows an error when measurements fail to load", async () => {
    vi.mocked(listRunMetrics).mockRejectedValue(new Error("offline"));
    render(<LearningScreen />);
    expect(
      await screen.findByText(/Measurements could not be loaded: offline/),
    ).toBeInTheDocument();
  });
});
