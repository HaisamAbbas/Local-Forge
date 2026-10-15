import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchRunVerdict, type RunVerdict as RunVerdictData } from "../lib/api";
import {
  EMPTY_TARGETS,
  formToQualityChecks,
  formToTargets,
  targetsToForm,
  EMPTY_QUALITY,
} from "../lib/targets-form";
import { RunVerdict, RunVerdictBadge } from "./RunVerdict";
import { PrefillEstimateNote } from "./WorkloadTargets";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return { ...actual, fetchRunVerdict: vi.fn() };
});

afterEach(cleanup);

const verdict: RunVerdictData = {
  run_id: "r1",
  plan_id: "p1",
  run_status: "completed",
  overall: "misses_targets",
  headline: "Misses 1 target",
  summary: "1 of 2 targets met, 1 missed.",
  targets: null,
  target_source: "run_plan_snapshot",
  evaluations: [
    {
      target: "ttft_p50_ms_max",
      label: "First token (p50)",
      comparison: "at_most",
      threshold: 5000,
      unit: "ms",
      observed: 58000,
      sample_count: 3,
      status: "fail",
      explanation: "First token took 58 s at p50 against a 5.0 s target.",
    },
    {
      target: "quality_pass_rate_min",
      label: "Quality pass rate",
      comparison: "at_least",
      threshold: 80,
      unit: "%",
      observed: null,
      sample_count: 0,
      status: "unknown",
      explanation: "No quality checks ran in this run.",
    },
  ],
  passed: 0,
  failed: 1,
  unknown: 1,
  concurrency_level: null,
  method_version: "localforge.verdict.v1",
  quality_method_version: null,
};

describe("RunVerdict", () => {
  it("shows the headline, per-target status and explanations", async () => {
    vi.mocked(fetchRunVerdict).mockResolvedValue(verdict);
    render(<RunVerdict runId="r1" runStatus="completed" />);
    expect(await screen.findByText("Misses 1 target")).toBeInTheDocument();
    expect(screen.getByText("Miss")).toBeInTheDocument();
    expect(screen.getByText("Not enough data")).toBeInTheDocument();
    expect(screen.getByText(/First token took 58 s at p50/)).toBeInTheDocument();
  });

  it("renders nothing when the verdict cannot be loaded and a badge only with targets", async () => {
    vi.mocked(fetchRunVerdict).mockRejectedValueOnce(new Error("offline"));
    const { container } = render(<RunVerdict runId="r2" runStatus="completed" />);
    await waitFor(() => expect(fetchRunVerdict).toHaveBeenCalledWith("r2"));
    expect(container).toBeEmptyDOMElement();
    vi.mocked(fetchRunVerdict).mockResolvedValue(verdict);
    render(<RunVerdictBadge runId="r3" />);
    expect(await screen.findByText("Misses 1 target")).toBeInTheDocument();
  });
});

describe("targets form", () => {
  it("converts seconds to milliseconds, validates, and clears empty targets", () => {
    expect(formToTargets(EMPTY_TARGETS).targets).toBeNull();
    const ok = formToTargets({ ...EMPTY_TARGETS, ttftP50Seconds: "5", errorRatePercent: "1" });
    expect(ok.targets?.ttft_p50_ms_max).toBe(5000);
    expect(ok.targets?.error_rate_percent_max).toBe(1);
    expect(ok.targets?.latency_p50_ms_max).toBeNull();
    const bad = formToTargets({
      ...EMPTY_TARGETS,
      errorRatePercent: "150",
      generationTokensPerSecond: "0",
    });
    expect(bad.errors.errorRatePercent).toBeTruthy();
    expect(bad.errors.generationTokensPerSecond).toBeTruthy();
    expect(targetsToForm(ok.targets).ttftP50Seconds).toBe("5");
  });

  it("builds quality checks only when something is enabled", () => {
    expect(formToQualityChecks(EMPTY_QUALITY)).toBeNull();
    const checks = formToQualityChecks({
      ...EMPTY_QUALITY,
      validJson: true,
      requiredKeys: "a, b",
      mustContain: "x",
    });
    expect(checks?.json_required_keys).toEqual(["a", "b"]);
    expect(checks?.must_contain).toEqual(["x"]);
  });
});

describe("PrefillEstimateNote", () => {
  it("states the expected first token and warns above the target", () => {
    render(
      <PrefillEstimateNote
        estimate={{
          available: true,
          input_tokens: 8192,
          prompt_tokens_per_second: 140,
          expected_first_token_seconds: 58.5,
          run_count: 2,
          request_count: 4,
          source_metric: null,
          message: "",
        }}
        ttftTargetSeconds={5}
      />,
    );
    expect(screen.getByText(/Expected first token ≈ 59 s/)).toBeInTheDocument();
    expect(screen.getByText(/above your 5\.0 s first-token target/)).toBeInTheDocument();
  });
});
