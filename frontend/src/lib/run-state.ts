import type { BenchmarkRun, RequestSample, RunEventRecord } from "./api";

const TERMINAL_RUN_STATUSES = new Set<BenchmarkRun["status"]>([
  "completed",
  "failed",
  "cancelled",
  "interrupted",
]);

export function isTerminalRunStatus(status: BenchmarkRun["status"]): boolean {
  return TERMINAL_RUN_STATUSES.has(status);
}

export function mergeRunEvents(
  current: RunEventRecord[],
  incoming: RunEventRecord[],
  runId: string,
): RunEventRecord[] {
  const bySequence = new Map(current.map((event) => [event.sequence_number, event]));
  for (const event of incoming) {
    if (
      event.run_id !== runId ||
      !Number.isSafeInteger(event.sequence_number) ||
      event.sequence_number < 0 ||
      bySequence.has(event.sequence_number)
    ) {
      continue;
    }
    bySequence.set(event.sequence_number, event);
  }
  return [...bySequence.values()].sort(
    (left, right) => left.sequence_number - right.sequence_number,
  );
}

/** Return the last event that can be resumed without skipping a sequence gap. */
export function runEventCursor(events: RunEventRecord[]): number {
  const sorted = [...events].sort((left, right) => left.sequence_number - right.sequence_number);
  let cursor = -1;
  for (const event of sorted) {
    if (event.sequence_number !== cursor + 1) break;
    cursor = event.sequence_number;
  }
  return cursor;
}

export function mergeRequestSamples(
  current: RequestSample[],
  incoming: RequestSample[],
  runId: string,
): RequestSample[] {
  const bySequence = new Map(current.map((sample) => [sample.sequence_number, sample]));
  for (const sample of incoming) {
    if (
      sample.run_id !== runId ||
      !Number.isSafeInteger(sample.sequence_number) ||
      sample.sequence_number < 0
    ) {
      continue;
    }
    const previous = bySequence.get(sample.sequence_number);
    const isNewer =
      previous === undefined ||
      Date.parse(sample.updated_at) > Date.parse(previous.updated_at) ||
      (sample.updated_at === previous.updated_at &&
        previous.outcome === "in_progress" &&
        sample.outcome !== "in_progress");
    if (isNewer) bySequence.set(sample.sequence_number, sample);
  }
  return [...bySequence.values()].sort(
    (left, right) => left.sequence_number - right.sequence_number,
  );
}

export type RunSampleSummary = {
  warmup: number;
  attempted: number;
  succeeded: number;
  failed: number;
  timedOut: number;
  cancelled: number;
  incomplete: number;
  latencyTotal: number;
  latencySampleCount: number;
  minimumDispatchOffsetMs: number | null;
  maximumCompletedOffsetMs: number | null;
  terminalSampleCount: number;
  successfulTerminalCount: number;
  concurrencyLevels: number[];
};

export function createRunSampleSummary(): RunSampleSummary {
  return {
    warmup: 0,
    attempted: 0,
    succeeded: 0,
    failed: 0,
    timedOut: 0,
    cancelled: 0,
    incomplete: 0,
    latencyTotal: 0,
    latencySampleCount: 0,
    minimumDispatchOffsetMs: null,
    maximumCompletedOffsetMs: null,
    terminalSampleCount: 0,
    successfulTerminalCount: 0,
    concurrencyLevels: [],
  };
}

function withSampleOutcome(summary: RunSampleSummary, sample: RequestSample): RunSampleSummary {
  const next = { ...summary, concurrencyLevels: [...summary.concurrencyLevels] };
  if (sample.phase === "warmup") {
    next.warmup += 1;
    return next;
  }
  next.attempted += 1;
  if (sample.outcome === "succeeded") next.succeeded += 1;
  if (["failed", "disconnected", "interrupted"].includes(sample.outcome)) next.failed += 1;
  if (sample.outcome === "timed_out") next.timedOut += 1;
  if (sample.outcome === "cancelled") next.cancelled += 1;
  if (sample.outcome === "in_progress") next.incomplete += 1;
  if (sample.latency_ms !== null && Number.isFinite(sample.latency_ms)) {
    next.latencyTotal += sample.latency_ms;
    next.latencySampleCount += 1;
  }
  if (sample.dispatch_offset_ms !== null && Number.isFinite(sample.dispatch_offset_ms)) {
    next.minimumDispatchOffsetMs =
      next.minimumDispatchOffsetMs === null
        ? sample.dispatch_offset_ms
        : Math.min(next.minimumDispatchOffsetMs, sample.dispatch_offset_ms);
  }
  if (sample.completed_offset_ms !== null && Number.isFinite(sample.completed_offset_ms)) {
    next.terminalSampleCount += 1;
    next.maximumCompletedOffsetMs =
      next.maximumCompletedOffsetMs === null
        ? sample.completed_offset_ms
        : Math.max(next.maximumCompletedOffsetMs, sample.completed_offset_ms);
    if (sample.outcome === "succeeded") next.successfulTerminalCount += 1;
  }
  if (
    sample.concurrency_level !== null &&
    !next.concurrencyLevels.includes(sample.concurrency_level)
  ) {
    next.concurrencyLevels.push(sample.concurrency_level);
  }
  return next;
}

export function addRequestSampleSummary(
  summary: RunSampleSummary,
  sample: RequestSample,
): RunSampleSummary {
  return withSampleOutcome(summary, sample);
}

/** Replace an in-progress sample with its terminal persisted version. */
export function completeRequestSampleSummary(
  summary: RunSampleSummary,
  previous: RequestSample,
  sample: RequestSample,
): RunSampleSummary {
  const next = { ...summary, concurrencyLevels: [...summary.concurrencyLevels] };
  if (previous.phase === "measurement" && previous.outcome === "in_progress") {
    next.incomplete = Math.max(0, next.incomplete - 1);
  }
  if (sample.phase !== "measurement") return next;
  if (sample.outcome === "succeeded") next.succeeded += 1;
  if (["failed", "disconnected", "interrupted"].includes(sample.outcome)) next.failed += 1;
  if (sample.outcome === "timed_out") next.timedOut += 1;
  if (sample.outcome === "cancelled") next.cancelled += 1;
  if (sample.latency_ms !== null && Number.isFinite(sample.latency_ms)) {
    next.latencyTotal += sample.latency_ms;
    next.latencySampleCount += 1;
  }
  if (sample.completed_offset_ms !== null && Number.isFinite(sample.completed_offset_ms)) {
    next.terminalSampleCount += 1;
    next.maximumCompletedOffsetMs =
      next.maximumCompletedOffsetMs === null
        ? sample.completed_offset_ms
        : Math.max(next.maximumCompletedOffsetMs, sample.completed_offset_ms);
    if (sample.outcome === "succeeded") next.successfulTerminalCount += 1;
  }
  return next;
}

export function summarizedMeanLatency(summary: RunSampleSummary): {
  value: number | null;
  sampleCount: number;
} {
  return {
    value:
      summary.latencySampleCount > 0 ? summary.latencyTotal / summary.latencySampleCount : null,
    sampleCount: summary.latencySampleCount,
  };
}

export function summarizedRequestThroughput(summary: RunSampleSummary): {
  value: number | null;
  sampleCount: number;
  reason: string | null;
} {
  if (summary.concurrencyLevels.length > 1) {
    return {
      value: null,
      sampleCount: 0,
      reason: "Per-level throughput is shown in persisted metrics.",
    };
  }
  if (summary.minimumDispatchOffsetMs === null || summary.terminalSampleCount === 0) {
    return {
      value: null,
      sampleCount: summary.terminalSampleCount,
      reason: "No completed measurement window is available yet.",
    };
  }
  const windowSeconds =
    ((summary.maximumCompletedOffsetMs ?? 0) - summary.minimumDispatchOffsetMs) / 1000;
  if (windowSeconds <= 0) {
    return {
      value: null,
      sampleCount: summary.terminalSampleCount,
      reason: "The observed monotonic measurement window is not positive.",
    };
  }
  return {
    value: summary.successfulTerminalCount / windowSeconds,
    sampleCount: summary.terminalSampleCount,
    reason: null,
  };
}

export function meanObservedLatency(samples: RequestSample[]): {
  value: number | null;
  sampleCount: number;
} {
  const observations = samples
    .filter((sample) => sample.phase === "measurement")
    .map((sample) => sample.latency_ms)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  return {
    value:
      observations.length > 0
        ? observations.reduce((sum, value) => sum + value, 0) / observations.length
        : null,
    sampleCount: observations.length,
  };
}

export function observedRequestThroughput(samples: RequestSample[]): {
  value: number | null;
  sampleCount: number;
  reason: string | null;
} {
  const measured = samples.filter((sample) => sample.phase === "measurement");
  const levels = new Set(
    measured
      .map((sample) => sample.concurrency_level)
      .filter((level): level is number => level !== null),
  );
  if (levels.size > 1) {
    return {
      value: null,
      sampleCount: 0,
      reason: "Per-level throughput is shown in persisted metrics.",
    };
  }
  const dispatchOffsets = measured
    .map((sample) => sample.dispatch_offset_ms)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const terminalSamples = measured.filter(
    (sample) => sample.completed_offset_ms !== null && Number.isFinite(sample.completed_offset_ms),
  );
  const sampleCount = terminalSamples.length;
  const successfulCount = terminalSamples.filter((sample) => sample.outcome === "succeeded").length;
  if (dispatchOffsets.length === 0 || terminalSamples.length === 0) {
    return {
      value: null,
      sampleCount,
      reason: "No completed measurement window is available yet.",
    };
  }
  const firstDispatch = dispatchOffsets.reduce((minimum, value) => Math.min(minimum, value));
  const lastTerminal = terminalSamples.reduce(
    (maximum, sample) => Math.max(maximum, sample.completed_offset_ms ?? 0),
    0,
  );
  const windowSeconds = (lastTerminal - firstDispatch) / 1000;
  if (windowSeconds <= 0) {
    return {
      value: null,
      sampleCount,
      reason: "The observed monotonic measurement window is not positive.",
    };
  }
  return { value: successfulCount / windowSeconds, sampleCount, reason: null };
}
