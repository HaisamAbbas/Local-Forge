import { describe, expect, it } from "vitest";

import type { BenchmarkRun, RequestSample, RunEventRecord } from "./api";
import {
  addRequestSampleSummary,
  completeRequestSampleSummary,
  createRunSampleSummary,
  isTerminalRunStatus,
  meanObservedLatency,
  mergeRequestSamples,
  mergeRunEvents,
  observedRequestThroughput,
  runEventCursor,
  summarizedMeanLatency,
  summarizedRequestThroughput,
} from "./run-state";

const runId = "run-a";

function event(sequence_number: number, event_run_id = runId): RunEventRecord {
  return {
    schema_version: 1,
    run_id: event_run_id,
    sequence_number,
    event_type: "run_status",
    request_id: null,
    monotonic_offset_ms: sequence_number * 10,
    occurred_at: "2026-01-01T00:00:00Z",
    payload: {},
  };
}

function sample(outcome: RequestSample["outcome"], updated_at: string): RequestSample {
  return {
    id: "sample-1",
    run_id: runId,
    sequence_number: 0,
    request_id: "request-1",
    phase: "measurement",
    concurrency_level: 1,
    effective_concurrency: 1,
    outcome,
    started_at: "2026-01-01T00:00:00Z",
    completed_at: outcome === "in_progress" ? null : updated_at,
    dispatch_offset_ms: 1,
    first_output_offset_ms: null,
    completed_offset_ms: null,
    queue_time_ms: 0,
    latency_ms: null,
    ttft_ms: null,
    prompt_tokens: null,
    completion_tokens: null,
    prompt_token_count_source: "unavailable",
    completion_token_count_source: "unavailable",
    error_class: null,
    output_event_count: 0,
    output_characters: 0,
    partial_output_observed: false,
    content_retained: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at,
  };
}

describe("persisted run state reconciliation", () => {
  it("orders events and rejects duplicate IDs while reconnecting across a gap", () => {
    const firstPage = mergeRunEvents([], [event(0), event(2), event(2)], runId);
    expect(firstPage.map((item) => item.sequence_number)).toEqual([0, 2]);
    expect(runEventCursor(firstPage)).toBe(0);

    const reconnected = mergeRunEvents(firstPage, [event(1), event(2)], runId);
    expect(reconnected.map((item) => item.sequence_number)).toEqual([0, 1, 2]);
    expect(runEventCursor(reconnected)).toBe(2);
  });

  it("ignores stale events from another run", () => {
    expect(mergeRunEvents([event(0)], [event(1, "old-run")], runId)).toEqual([event(0)]);
  });

  it("replaces an in-progress request sample with its persisted terminal update once", () => {
    const initial = sample("in_progress", "2026-01-01T00:00:00Z");
    const completed = sample("succeeded", "2026-01-01T00:00:01Z");
    const refreshed = mergeRequestSamples([initial], [completed, completed], runId);
    expect(refreshed).toHaveLength(1);
    expect(refreshed[0]?.outcome).toBe("succeeded");
  });

  it("recognizes failed and cancelled runs as terminal after reconnect", () => {
    const statuses: BenchmarkRun["status"][] = ["failed", "cancelled", "completed", "interrupted"];
    expect(statuses.every(isTerminalRunStatus)).toBe(true);
    expect(isTerminalRunStatus("running")).toBe(false);
  });

  it("keeps zero latency and zero throughput distinct from unavailable measurements", () => {
    const zeroLatency = {
      ...sample("succeeded", "2026-01-01T00:00:01Z"),
      latency_ms: 0,
      dispatch_offset_ms: 0,
      completed_offset_ms: 1000,
      concurrency_level: 1,
    };
    expect(meanObservedLatency([zeroLatency])).toEqual({ value: 0, sampleCount: 1 });
    const failed = { ...zeroLatency, outcome: "failed" as const };
    expect(observedRequestThroughput([failed])).toEqual({
      value: 0,
      sampleCount: 1,
      reason: null,
    });
    expect(
      observedRequestThroughput([sample("in_progress", "2026-01-01T00:00:00Z")]).value,
    ).toBeNull();
  });

  it("aggregates request pages without retaining every sample and finalizes in-progress counts", () => {
    const initial = sample("in_progress", "2026-01-01T00:00:00Z");
    const runningSummary = addRequestSampleSummary(createRunSampleSummary(), initial);
    const completed = {
      ...initial,
      outcome: "succeeded" as const,
      completed_at: "2026-01-01T00:00:01Z",
      completed_offset_ms: 1001,
      latency_ms: 100,
      updated_at: "2026-01-01T00:00:01Z",
    };
    const finalSummary = completeRequestSampleSummary(runningSummary, initial, completed);

    expect(finalSummary).toMatchObject({
      attempted: 1,
      incomplete: 0,
      succeeded: 1,
      latencySampleCount: 1,
      terminalSampleCount: 1,
    });
    expect(summarizedMeanLatency(finalSummary)).toEqual({ value: 100, sampleCount: 1 });
    expect(summarizedRequestThroughput(finalSummary).value).toBeCloseTo(1);
  });

  it("keeps warmup and failure outcomes separate in the summary", () => {
    const warmup = { ...sample("succeeded", "2026-01-01T00:00:01Z"), phase: "warmup" as const };
    const failed = { ...sample("disconnected", "2026-01-01T00:00:02Z"), sequence_number: 1 };
    const summary = addRequestSampleSummary(
      addRequestSampleSummary(createRunSampleSummary(), warmup),
      failed,
    );

    expect(summary).toMatchObject({ warmup: 1, attempted: 1, failed: 1 });
  });
});
