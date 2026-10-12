import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  cancelBenchmarkRun,
  getRun,
  listBenchmarkPlans,
  listEngineProfiles,
  listMachineProfiles,
  listRunEvents,
  listRunMetrics,
  listRuns,
  listRunSamples,
  listWorkloads,
  type BenchmarkPlan,
  type BenchmarkRun,
  type EngineProfile,
  type MachineProfile,
  type MetricSample,
  type RequestSample,
  type RunEventRecord,
  type RunEventPage,
  type RunSamplesResponse,
  type Workload,
} from "../lib/api";
import {
  addRequestSampleSummary,
  completeRequestSampleSummary,
  createRunSampleSummary,
  isTerminalRunStatus,
  mergeRequestSamples,
  mergeRunEvents,
  summarizedMeanLatency,
  summarizedRequestThroughput,
} from "../lib/run-state";

const EVENT_TYPES = [
  "run_status",
  "warning",
  "request_started",
  "first_output",
  "output_chunk",
  "engine_usage",
  "request_completed",
  "request_failed",
  "request_cancelled",
  "run_metric",
  "system_sample",
] as const;

type StreamConnection = "loading" | "connecting" | "connected" | "reconnecting" | "complete";
type RunDetail = {
  run: BenchmarkRun | null;
  events: RunEventRecord[];
  systemSample: RunEventRecord | null;
  samples: RequestSample[];
  incompleteSamples: RequestSample[];
  sampleSummary: ReturnType<typeof createRunSampleSummary>;
  metrics: MetricSample[];
  connection: StreamConnection;
  loading: boolean;
  catchingUp: boolean;
  error: string | null;
  metadata: RunMetadata | null;
  metadataError: string | null;
  cancelPending: boolean;
  cancelError: string | null;
};
type RunMetadata = {
  plan: BenchmarkPlan | null;
  engine: Pick<
    EngineProfile,
    | "id"
    | "label"
    | "adapter_id"
    | "engine_display_name"
    | "adapter_version"
    | "engine_version"
    | "integration_mode"
    | "capabilities"
    | "selected_model_ref"
  > | null;
  engineSource: "captured" | "current";
  machine: Pick<MachineProfile, "id" | "label" | "status" | "hardware"> | null;
  machineSource: "captured" | "current";
  workload: Workload | null;
};

const EMPTY_DETAIL: RunDetail = {
  run: null,
  events: [],
  systemSample: null,
  samples: [],
  incompleteSamples: [],
  sampleSummary: createRunSampleSummary(),
  metrics: [],
  connection: "loading",
  loading: true,
  catchingUp: false,
  error: null,
  metadata: null,
  metadataError: null,
  cancelPending: false,
  cancelError: null,
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "The local API request failed.";
}

function withoutRequestBodies(sample: RequestSample): RequestSample {
  const safeSample = { ...sample };
  delete safeSample.prompt_text;
  delete safeSample.output_text;
  return safeSample;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

async function configurationFingerprint(plan: BenchmarkPlan): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const snapshot = {
    plan_id: plan.id,
    name: plan.name,
    engine_profile_id: plan.engine_profile_id,
    machine_profile_id: plan.machine_profile_id,
    model_artifact_id: plan.model_artifact_id,
    workload_id: plan.workload_id,
    warmup_requests: plan.warmup_requests,
    measured_requests: plan.measured_requests,
    measurement_duration_seconds: plan.measurement_duration_seconds,
    concurrency: plan.concurrency,
    timeout_seconds: plan.timeout_seconds,
    retain_prompt_output: plan.retain_prompt_output,
    configuration_snapshot: plan.configuration_snapshot,
  };
  const bytes = new TextEncoder().encode(stableJson(snapshot));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

function formatDate(value: string | null): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleString();
}

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? `${hours}h ${minutes}m ${remainder}s`
    : minutes > 0
      ? `${minutes}m ${remainder}s`
      : `${remainder}s`;
}

function eventSummary(event: RunEventRecord): string {
  const payload = event.payload;
  switch (event.event_type) {
    case "run_status":
      return `${statusLabel(String(payload.from ?? "created"))} → ${statusLabel(String(payload.to ?? "unknown"))}`;
    case "warning":
      return `${String(payload.code ?? "warning")}: ${String(payload.message ?? "Review this run warning.")}`;
    case "request_started":
      return `Request ${Number(payload.sequence ?? 0) + 1} started (${String(payload.phase ?? "measurement")}).`;
    case "first_output":
      return "First output observed.";
    case "output_chunk":
      return `Output event observed · ${Number(payload.characters ?? 0).toLocaleString()} characters of metadata.`;
    case "engine_usage":
      return "Engine usage counts observed.";
    case "request_completed":
      return `Request completed · ${formatNumber(payload.duration_ms, "ms")} latency.`;
    case "request_failed":
      return `Request failed · ${String(payload.category ?? "reason unavailable")}.`;
    case "request_cancelled":
      return "Request cancelled.";
    case "run_metric":
      return `Metric recorded · ${String(payload.metric_name ?? "metric")} (${String(payload.source ?? "source unavailable")}).`;
    case "system_sample":
      return payload.available === true
        ? "Resource telemetry sample recorded."
        : "Resource telemetry unavailable from the local host sampler.";
    default:
      return `${event.event_type.replaceAll("_", " ")} event recorded.`;
  }
}

function formatNumber(value: unknown, unit = ""): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`
    : "Unavailable";
}

function eventTime(event: RunEventRecord): string {
  return `${(event.monotonic_offset_ms / 1000).toFixed(3)}s from run timeline origin`;
}

function outcomeClass(status: string): string {
  return `status-pill status-pill--${status}`;
}

function RunStatus({ status }: { status: string }) {
  return <span className={outcomeClass(status)}>{statusLabel(status)}</span>;
}

export function RunsScreen({
  selectedRunId,
  onSelectRun,
}: {
  selectedRunId: string | null;
  onSelectRun: (id: string | null) => void;
}) {
  const [runs, setRuns] = useState<BenchmarkRun[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail>(EMPTY_DETAIL);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [elapsedNow, setElapsedNow] = useState(Date.now());
  const eventsRef = useRef<RunEventRecord[]>([]);
  const samplesRef = useRef<RequestSample[]>([]);
  const incompleteSamplesRef = useRef<RequestSample[]>([]);
  const sampleSummaryRef = useRef(createRunSampleSummary());
  const sampleCursorRef = useRef(-1);
  const incompleteBySequenceRef = useRef(new Map<number, RequestSample>());
  const interruptedSamplesRef = useRef<RequestSample[]>([]);

  const refreshHistory = useCallback(async () => {
    try {
      setRuns(await listRuns(100));
      setHistoryError(null);
    } catch (error) {
      setHistoryError(errorMessage(error));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
    const timer = window.setInterval(() => void refreshHistory(), 3000);
    return () => window.clearInterval(timer);
  }, [refreshHistory]);

  useEffect(() => {
    const timer = window.setInterval(() => setElapsedNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let active = true;
    let polling = false;
    let sampleHistoryReady = false;
    let eventSource: EventSource | null = null;
    let systemSample: RunEventRecord | null = null;
    let pollTimer = 0;
    let refreshTimer = 0;
    let eventRenderTimer = 0;
    eventsRef.current = [];
    samplesRef.current = [];
    incompleteSamplesRef.current = [];
    sampleSummaryRef.current = createRunSampleSummary();
    sampleCursorRef.current = -1;
    incompleteBySequenceRef.current = new Map();
    interruptedSamplesRef.current = [];
    setConfirmCancel(false);
    setDetail({ ...EMPTY_DETAIL, loading: Boolean(selectedRunId) });
    if (!selectedRunId) return;

    const runId = selectedRunId;
    const rebuildIncompleteSamples = () => {
      incompleteSamplesRef.current = mergeRequestSamples(
        [],
        [...interruptedSamplesRef.current, ...incompleteBySequenceRef.current.values()],
        runId,
      ).slice(-500);
    };
    const ingestSamples = (samples: RequestSample[]) => {
      const safeSamples: RequestSample[] = [];
      const terminalIncompletes: RequestSample[] = [];
      for (const rawSample of samples) {
        if (rawSample.run_id !== runId) continue;
        const sample = withoutRequestBodies(rawSample);
        safeSamples.push(sample);
        const previous = incompleteBySequenceRef.current.get(sample.sequence_number);
        if (previous) {
          if (sample.outcome === "in_progress") {
            if (Date.parse(sample.updated_at) >= Date.parse(previous.updated_at)) {
              incompleteBySequenceRef.current.set(sample.sequence_number, sample);
            }
          } else {
            sampleSummaryRef.current = completeRequestSampleSummary(
              sampleSummaryRef.current,
              previous,
              sample,
            );
            incompleteBySequenceRef.current.delete(sample.sequence_number);
            if (sample.outcome === "interrupted" || sample.outcome === "disconnected") {
              terminalIncompletes.push(sample);
            }
          }
        } else {
          sampleSummaryRef.current = addRequestSampleSummary(sampleSummaryRef.current, sample);
          if (sample.outcome === "in_progress") {
            incompleteBySequenceRef.current.set(sample.sequence_number, sample);
          } else if (sample.outcome === "interrupted" || sample.outcome === "disconnected") {
            terminalIncompletes.push(sample);
          }
        }
      }
      samplesRef.current = mergeRequestSamples(samplesRef.current, safeSamples, runId).slice(-500);
      interruptedSamplesRef.current = mergeRequestSamples(
        interruptedSamplesRef.current,
        terminalIncompletes,
        runId,
      ).slice(-500);
      rebuildIncompleteSamples();
    };

    const loadMetadata = async (run: BenchmarkRun) => {
      try {
        const [plans, engines, machines, workloads] = await Promise.all([
          listBenchmarkPlans(),
          listEngineProfiles(),
          listMachineProfiles(),
          listWorkloads(),
        ]);
        if (!active) return;
        const plan = plans.find((item) => item.id === run.plan_id) ?? null;
        const currentEngine = plan
          ? (engines.find((item) => item.id === plan.engine_profile_id) ?? null)
          : null;
        const hasCapturedSnapshot = Boolean(run.profile_snapshot?.captured_at);
        const engine = hasCapturedSnapshot ? (run.profile_snapshot?.engine ?? null) : currentEngine;
        const machineId = hasCapturedSnapshot
          ? (run.profile_snapshot?.machine_profile_id ?? null)
          : (plan?.machine_profile_id ?? currentEngine?.machine_profile_id ?? null);
        const machine = hasCapturedSnapshot
          ? (run.profile_snapshot?.machine ?? null)
          : machineId
            ? (machines.find((item) => item.id === machineId) ?? null)
            : null;
        setDetail((current) => ({
          ...current,
          metadata: plan
            ? {
                plan,
                engine,
                engineSource: hasCapturedSnapshot ? "captured" : "current",
                machine,
                machineSource: hasCapturedSnapshot ? "captured" : "current",
                workload: workloads.find((item) => item.id === plan.workload_id) ?? null,
              }
            : null,
          metadataError: plan ? null : "The saved plan for this run is no longer available.",
        }));
      } catch (error) {
        if (active) setDetail((current) => ({ ...current, metadataError: errorMessage(error) }));
      }
    };

    const refreshLiveState = async () => {
      if (polling || !active) return;
      polling = true;
      try {
        const [run, newSamples, incompleteSamples, metrics] = await Promise.all([
          getRun(runId),
          sampleHistoryReady ? listRunSamples(runId, sampleCursorRef.current, 1000) : null,
          sampleHistoryReady ? listRunSamples(runId, -1, 1000, true) : null,
          listRunMetrics(runId),
        ]);
        if (!active) return;

        if (newSamples && incompleteSamples) {
          ingestSamples(newSamples.samples);
          if (newSamples.next_sequence_number !== null) {
            sampleCursorRef.current = Math.max(
              sampleCursorRef.current,
              newSamples.next_sequence_number,
            );
          }
          const oldIncomplete = [...incompleteBySequenceRef.current.values()];
          const stillIncomplete = new Set(
            incompleteSamples.samples.map((sample) => sample.sequence_number),
          );
          const resolved = oldIncomplete.filter(
            (sample) => !stillIncomplete.has(sample.sequence_number),
          );
          const resolvedPages = await Promise.all(
            resolved.map(async (sample) => {
              const page = await listRunSamples(runId, sample.sequence_number - 1, 1000);
              return (
                page.samples.find((item) => item.sequence_number === sample.sequence_number) ?? null
              );
            }),
          );
          if (!active) return;
          ingestSamples(incompleteSamples.samples);
          ingestSamples(resolvedPages.filter((item): item is RequestSample => item !== null));
        }
        if (isTerminalRunStatus(run.status)) {
          eventSource?.close();
          if (pollTimer) {
            window.clearInterval(pollTimer);
            pollTimer = 0;
          }
        }
        setDetail((current) => ({
          ...current,
          run,
          samples: samplesRef.current,
          incompleteSamples: incompleteSamplesRef.current,
          sampleSummary: sampleSummaryRef.current,
          metrics: metrics.metrics,
          error: null,
          connection: isTerminalRunStatus(run.status) ? "complete" : current.connection,
        }));
        void refreshHistory();
      } catch (error) {
        if (active) setDetail((current) => ({ ...current, error: errorMessage(error) }));
      } finally {
        polling = false;
      }
    };

    const appendEvent = (raw: MessageEvent<string>) => {
      try {
        const event = JSON.parse(raw.data) as RunEventRecord;
        if (event.run_id !== runId || !Number.isSafeInteger(event.sequence_number)) return;
        const lastSequence = eventsRef.current.at(-1)?.sequence_number ?? -1;
        if (event.sequence_number <= lastSequence) return;
        eventsRef.current.push(event);
        if (eventsRef.current.length > 100) eventsRef.current.shift();
        if (event.event_type === "system_sample") systemSample = event;
        if (!eventRenderTimer) {
          eventRenderTimer = window.setTimeout(() => {
            eventRenderTimer = 0;
            if (!active) return;
            setDetail((current) => ({
              ...current,
              events: [...eventsRef.current],
              systemSample: systemSample ?? current.systemSample,
            }));
          }, 100);
        }
        if (
          ["request_completed", "request_failed", "request_cancelled"].includes(event.event_type)
        ) {
          if (!refreshTimer)
            refreshTimer = window.setTimeout(() => {
              refreshTimer = 0;
              void refreshLiveState();
            }, 250);
        }
        if (
          event.event_type === "run_status" &&
          isTerminalRunStatus(String(event.payload.to ?? ""))
        ) {
          if (!refreshTimer)
            refreshTimer = window.setTimeout(() => {
              refreshTimer = 0;
              void refreshLiveState();
            }, 0);
        }
      } catch {
        setDetail((current) => ({
          ...current,
          error: "A live run event could not be read safely.",
        }));
      }
    };

    const load = async () => {
      try {
        const run = await getRun(runId);
        if (!active) return;
        setDetail((current) => ({
          ...current,
          run,
          loading: false,
          catchingUp: true,
          connection: isTerminalRunStatus(run.status) ? "complete" : "connecting",
          error: null,
        }));
        void loadMetadata(run);

        if (!isTerminalRunStatus(run.status)) {
          eventSource = new EventSource(`/api/v1/runs/${runId}/events/stream?after=-1`);
          eventSource.onopen = () => {
            if (active) setDetail((current) => ({ ...current, connection: "connected" }));
          };
          eventSource.onerror = () => {
            if (active) setDetail((current) => ({ ...current, connection: "reconnecting" }));
          };
          for (const eventType of EVENT_TYPES) eventSource.addEventListener(eventType, appendEvent);
          eventSource.addEventListener("heartbeat", () => {
            if (active) setDetail((current) => ({ ...current, connection: "connected" }));
          });
          pollTimer = window.setInterval(() => void refreshLiveState(), 1000);
        }

        const catchUpPersistedHistory = async () => {
          let eventAfter = -1;
          let sampleAfter = -1;
          let moreEvents = isTerminalRunStatus(run.status);
          let moreSamples = true;
          const metrics = await listRunMetrics(runId);
          if (!active) return;
          setDetail((current) => ({ ...current, metrics: metrics.metrics }));
          while (active && (moreEvents || moreSamples)) {
            const eventPromise: Promise<RunEventPage | null> = moreEvents
              ? listRunEvents(runId, eventAfter, 1000)
              : Promise.resolve(null);
            const samplePromise: Promise<RunSamplesResponse | null> = moreSamples
              ? listRunSamples(runId, sampleAfter, 1000)
              : Promise.resolve(null);
            const [eventPage, samplePage] = await Promise.all([eventPromise, samplePromise]);
            if (!active) return;
            if (eventPage) {
              eventsRef.current = mergeRunEvents(eventsRef.current, eventPage.events, runId).slice(
                -100,
              );
              const latestSystemSample = [...eventPage.events]
                .reverse()
                .find((event) => event.event_type === "system_sample");
              if (latestSystemSample) systemSample = latestSystemSample;
              if (eventPage.next_sequence_number !== null)
                eventAfter = eventPage.next_sequence_number;
              moreEvents =
                eventPage.next_sequence_number !== null && eventPage.events.length === 1000;
            }
            if (samplePage) {
              ingestSamples(samplePage.samples);
              if (samplePage.next_sequence_number !== null)
                sampleAfter = samplePage.next_sequence_number;
              moreSamples =
                samplePage.next_sequence_number !== null && samplePage.samples.length === 1000;
            }
            setDetail((current) => ({
              ...current,
              events: eventsRef.current,
              systemSample,
              samples: samplesRef.current,
              incompleteSamples: incompleteSamplesRef.current,
              sampleSummary: sampleSummaryRef.current,
              catchingUp: moreEvents || moreSamples,
            }));
          }
          sampleHistoryReady = true;
          sampleCursorRef.current = Math.max(sampleCursorRef.current, sampleAfter);
        };

        void catchUpPersistedHistory().catch((error: unknown) => {
          if (active)
            setDetail((current) => ({
              ...current,
              catchingUp: false,
              error: errorMessage(error),
            }));
        });
      } catch (error) {
        if (!active) return;
        setDetail((current) => ({
          ...current,
          loading: false,
          connection: "complete",
          error: errorMessage(error),
        }));
      }
    };

    void load();
    return () => {
      active = false;
      eventSource?.close();
      if (pollTimer) window.clearInterval(pollTimer);
      if (refreshTimer) window.clearTimeout(refreshTimer);
      if (eventRenderTimer) window.clearTimeout(eventRenderTimer);
    };
  }, [selectedRunId, refreshHistory]);

  const counts = detail.sampleSummary;
  const liveLatency = summarizedMeanLatency(counts);
  const liveThroughput = summarizedRequestThroughput(counts);
  const selectedRun = detail.run;
  const selectedIsTerminal = !selectedRun || isTerminalRunStatus(selectedRun.status);
  const lastMonotonicOffset = detail.events.reduce(
    (maximum, event) => Math.max(maximum, event.monotonic_offset_ms),
    0,
  );
  const elapsed = selectedRun
    ? selectedRun.completed_at
      ? lastMonotonicOffset
      : Math.max(
          lastMonotonicOffset,
          elapsedNow - Date.parse(selectedRun.started_at ?? selectedRun.created_at),
        )
    : 0;
  const latencySamples = detail.samples.filter(
    (sample) =>
      sample.phase === "measurement" &&
      sample.latency_ms !== null &&
      Number.isFinite(sample.latency_ms),
  );
  const warnings = detail.events.filter((event) => event.event_type === "warning");
  const normalizedMetrics = detail.metrics.filter(
    (metric) => metric.source !== "engine_native" && metric.aggregation !== "raw",
  );
  const rawEngineMetrics = detail.metrics.filter(
    (metric) => metric.source === "engine_native" && metric.aggregation === "raw",
  );
  const engineMetricDeltas = detail.metrics.filter(
    (metric) => metric.source === "engine_native" && metric.aggregation !== "raw",
  );

  async function confirmCancellation() {
    if (!selectedRun || selectedIsTerminal || selectedRun.status === "cancelling") return;
    setDetail((current) => ({ ...current, cancelPending: true, cancelError: null }));
    try {
      const run = await cancelBenchmarkRun(selectedRun.id);
      setDetail((current) => ({ ...current, run, cancelPending: false }));
      setConfirmCancel(false);
      void refreshHistory();
    } catch (error) {
      setDetail((current) => ({
        ...current,
        cancelPending: false,
        cancelError: errorMessage(error),
      }));
    }
  }

  return (
    <div className="runs-layout">
      <section className="panel run-history-panel" aria-labelledby="run-history-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">PERSISTED RUNS</p>
            <h2 id="run-history-title">Run history</h2>
          </div>
          <button
            className="button button--quiet"
            onClick={() => void refreshHistory()}
            type="button"
          >
            Refresh
          </button>
        </div>
        {historyError && (
          <div className="notice notice--warning" role="alert">
            {historyError}
          </div>
        )}
        {historyLoading ? (
          <p className="empty-inline" role="status">
            Loading saved runs…
          </p>
        ) : historyError && runs.length === 0 ? (
          <div className="runs-empty-state">
            <h3>Run history could not be loaded</h3>
            <p>
              Check that the local API is available, then retry. No conclusion about saved runs can
              be made yet.
            </p>
            <button
              className="button button--primary"
              onClick={() => void refreshHistory()}
              type="button"
            >
              Retry history
            </button>
          </div>
        ) : runs.length === 0 ? (
          <div className="runs-empty-state">
            <h3>No benchmark runs yet</h3>
            <p>Build and launch a workload to follow a run and keep its measurements here.</p>
            <a className="button button--primary" href="#workloads">
              Configure a workload
            </a>
          </div>
        ) : (
          <>
            <p className="helper-copy">
              Showing the latest {runs.length} saved runs. Failed and interrupted runs stay in this
              history.
            </p>
            <ol className="run-history-list" aria-label="Saved benchmark runs">
              {runs.map((run) => (
                <li key={run.id}>
                  <button
                    aria-current={selectedRunId === run.id ? "true" : undefined}
                    className={`run-history-item${selectedRunId === run.id ? " run-history-item--selected" : ""}`}
                    onClick={() => onSelectRun(run.id)}
                    type="button"
                  >
                    <span className="run-history-item__top">
                      <RunStatus status={run.status} />
                      <time dateTime={run.created_at}>{formatDate(run.created_at)}</time>
                    </span>
                    <span className="run-history-item__id">Run {run.id.slice(0, 8)}</span>
                    <span className="run-history-item__meta">
                      {run.error_code
                        ? `Error: ${run.error_code}`
                        : run.potentially_contended
                          ? "Potentially contended"
                          : `Plan ${run.plan_id.slice(0, 8)}`}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </>
        )}
      </section>

      <section className="panel run-detail-panel" aria-labelledby="run-detail-title">
        {!selectedRunId ? (
          <div className="runs-empty-state">
            <p className="eyebrow">RUN DETAIL</p>
            <h2 id="run-detail-title">Select a saved run</h2>
            <p>
              Live progress reconnects from persisted events. Completed, failed, cancelled, and
              interrupted runs open from the history list.
            </p>
          </div>
        ) : detail.loading ? (
          <p className="empty-inline" role="status">
            Restoring run state and measurements…
          </p>
        ) : detail.error && !selectedRun ? (
          <div className="notice notice--warning" role="alert">
            <strong>Run details could not be loaded.</strong>
            <p>{detail.error}</p>
          </div>
        ) : selectedRun ? (
          <>
            <div className="run-detail-heading">
              <div>
                <p className="eyebrow">RUN DETAIL · {selectedRun.id.slice(0, 8)}</p>
                <h2 id="run-detail-title">
                  {detail.metadata?.plan?.name ?? `Run ${selectedRun.id.slice(0, 8)}`}
                </h2>
              </div>
              <div className="run-detail-actions">
                <RunStatus status={selectedRun.status} />
                <button
                  className="button button--quiet"
                  onClick={() => onSelectRun(null)}
                  type="button"
                >
                  Back to history
                </button>
                {!selectedIsTerminal && selectedRun.status !== "cancelling" && (
                  <button
                    className="button button--danger-quiet"
                    disabled={detail.cancelPending}
                    onClick={() => setConfirmCancel(true)}
                    type="button"
                  >
                    Cancel run
                  </button>
                )}
              </div>
            </div>
            <p className="run-connection" role="status">
              <span
                className={`local-dot${detail.connection === "connected" ? "" : " local-dot--muted"}`}
                aria-hidden="true"
              />
              {detail.connection === "connected"
                ? "Live event stream connected"
                : detail.connection === "reconnecting"
                  ? "Connection interrupted · reconnecting from saved events"
                  : detail.connection === "complete"
                    ? "Showing persisted run state"
                    : "Connecting to persisted run events…"}
              {detail.catchingUp
                ? " · Restoring saved event and sample pages; counts may be partial."
                : ""}
            </p>
            {confirmCancel && (
              <div className="run-cancel-confirm" role="alert">
                <p>Stop dispatching requests and ask the engine to cancel active requests?</p>
                <div className="run-detail-actions">
                  <button
                    className="button button--danger"
                    disabled={detail.cancelPending}
                    onClick={() => void confirmCancellation()}
                    type="button"
                  >
                    {detail.cancelPending ? "Requesting cancellation…" : "Confirm cancellation"}
                  </button>
                  <button
                    className="button button--quiet"
                    disabled={detail.cancelPending}
                    onClick={() => setConfirmCancel(false)}
                    type="button"
                  >
                    Keep running
                  </button>
                </div>
              </div>
            )}
            {selectedRun.status === "cancelling" && (
              <p className="notice notice--warning" role="status">
                Cancellation requested. The run will record its terminal cancellation state after
                active requests drain.
              </p>
            )}
            {(detail.cancelError || detail.error) && (
              <p className="notice notice--warning" role="alert">
                {detail.cancelError ?? detail.error}
              </p>
            )}
            {selectedRun.error_code && (
              <div className="run-error" role="alert">
                <strong>{selectedRun.error_code}</strong>
                <p>
                  {selectedRun.error_message ??
                    "The run stopped unexpectedly. Review its timeline and retry after correcting the issue."}
                </p>
              </div>
            )}

            <div className="run-summary-grid" aria-label="Run progress counts">
              <RunFact
                label="Elapsed"
                value={formatDuration(elapsed)}
                detail={
                  selectedRun.completed_at
                    ? "Latest persisted monotonic run offset"
                    : "Display estimate; metric durations use monotonic offsets"
                }
              />
              <RunFact
                label="Measurement requests"
                value={counts.attempted.toLocaleString()}
                detail={`${counts.incomplete} in progress · ${detail.catchingUp ? "loading saved measurement samples" : "measurement samples"}`}
              />
              <RunFact
                label="Warmup requests"
                value={counts.warmup.toLocaleString()}
                detail="Excluded from normalized measurement summaries"
              />
              <RunFact
                label="Succeeded"
                value={counts.succeeded.toLocaleString()}
                detail="Measurement requests"
              />
              <RunFact
                label="Failed"
                value={counts.failed.toLocaleString()}
                detail="Failed, disconnected, or interrupted"
              />
              <RunFact
                label="Timed out"
                value={counts.timedOut.toLocaleString()}
                detail="Distinct from other failures"
              />
              <RunFact
                label="Cancelled"
                value={counts.cancelled.toLocaleString()}
                detail="Request outcomes recorded"
              />
              <RunFact
                label="Mean latency"
                value={formatNumber(liveLatency.value, "ms")}
                detail={`${liveLatency.sampleCount} client-measured observations · arithmetic mean`}
              />
              <RunFact
                label="Observed throughput"
                value={formatNumber(liveThroughput.value, "requests/s")}
                detail={
                  liveThroughput.reason
                    ? `${liveThroughput.sampleCount} terminal samples · ${liveThroughput.reason}`
                    : `${liveThroughput.sampleCount} terminal samples · client-derived observed window`
                }
              />
              <RunFact
                label="Latency samples"
                value={counts.latencySampleCount.toLocaleString()}
                detail="Measured requests · ms"
              />
              <RunFact
                label="Normalized metrics"
                value={normalizedMetrics.length.toLocaleString()}
                detail="Persisted with source and sample count"
              />
            </div>

            <LatencyChart
              samples={latencySamples}
              totalSampleCount={counts.latencySampleCount}
              catchingUp={detail.catchingUp}
            />

            <section className="run-subsection" aria-labelledby="run-provenance-title">
              <div className="section-title-row">
                <h3 id="run-provenance-title">Plan and machine</h3>
              </div>
              {detail.metadataError ? (
                <p className="empty-inline">{detail.metadataError}</p>
              ) : !detail.metadata ? (
                <p className="empty-inline">Loading run provenance…</p>
              ) : (
                <>
                  <dl className="run-provenance-grid">
                    <div>
                      <dt>Plan</dt>
                      <dd>{detail.metadata.plan?.name ?? "Unavailable"}</dd>
                    </div>
                    <div>
                      <dt>Configuration fingerprint · SHA-256</dt>
                      <dd>
                        <Fingerprint plan={detail.metadata.plan} />
                      </dd>
                    </div>
                    <div>
                      <dt>Plan ID</dt>
                      <dd>{detail.metadata.plan?.id ?? selectedRun.plan_id}</dd>
                    </div>
                    <div>
                      <dt>Workload</dt>
                      <dd>{detail.metadata.workload?.name ?? "Unavailable"}</dd>
                    </div>
                    <div>
                      <dt>Model artifact ID</dt>
                      <dd>{detail.metadata.plan?.model_artifact_id ?? "Unavailable"}</dd>
                    </div>
                    <div>
                      <dt>
                        Machine profile{" "}
                        {detail.metadata.machineSource === "captured"
                          ? "at run start"
                          : "currently saved"}
                      </dt>
                      <dd>
                        {detail.metadata.machine?.label ??
                          (detail.metadata.plan?.machine_profile_id ||
                          selectedRun.profile_snapshot?.machine_profile_id
                            ? "Saved profile unavailable"
                            : "No machine profile bound to plan")}
                      </dd>
                    </div>
                    <div>
                      <dt>Retention</dt>
                      <dd>
                        {detail.metadata.plan?.retain_prompt_output
                          ? "Bodies retained by plan opt-in; omitted from this view"
                          : "Prompt and output bodies not retained"}
                      </dd>
                    </div>
                  </dl>
                  <div className="run-engine-provenance">
                    <h4>
                      Engine and capabilities{" "}
                      {detail.metadata.engineSource === "captured"
                        ? "at run start"
                        : "currently saved"}
                    </h4>
                    {detail.metadata.engineSource === "current" && (
                      <p className="source-note">
                        This run record predates profile snapshots. Historical engine details were
                        not captured, so the current saved profile is shown when available.
                      </p>
                    )}
                    <p>
                      {detail.metadata.engine?.label ?? "Engine profile unavailable"} ·{" "}
                      {detail.metadata.engine?.engine_version ?? "Version not reported"} ·{" "}
                      {detail.metadata.engine?.adapter_id ?? "Adapter unknown"} adapter{" "}
                      {detail.metadata.engine?.adapter_version ?? "version not recorded"}
                    </p>
                    {detail.metadata.engine?.capabilities &&
                    Object.keys(detail.metadata.engine.capabilities).length > 0 ? (
                      <div className="table-scroll">
                        <table>
                          <caption>
                            {detail.metadata.engineSource === "captured"
                              ? "Engine capabilities captured when the run was queued"
                              : "Current saved engine capability snapshot"}
                          </caption>
                          <thead>
                            <tr>
                              <th scope="col">Capability</th>
                              <th scope="col">State</th>
                              <th scope="col">Details</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(detail.metadata.engine.capabilities).map(
                              ([name, value]) => (
                                <tr key={name}>
                                  <th scope="row">{name}</th>
                                  <td>{value.state}</td>
                                  <td>{value.details ?? value.source ?? "Not reported"}</td>
                                </tr>
                              ),
                            )}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="empty-inline">
                        Engine capabilities were not saved for this profile.
                      </p>
                    )}
                    <p className="run-machine-detail">{machineSummary(detail.metadata.machine)}</p>
                  </div>
                  <details className="run-config-details">
                    <summary>Saved configuration snapshot</summary>
                    <pre>
                      {detail.metadata.plan
                        ? JSON.stringify(detail.metadata.plan.configuration_snapshot, null, 2)
                        : "Unavailable"}
                    </pre>
                  </details>
                </>
              )}
            </section>

            <section className="run-subsection" aria-labelledby="run-metrics-title">
              <div className="section-title-row">
                <div>
                  <h3 id="run-metrics-title">Measurements</h3>
                  <p className="helper-copy">
                    Each value keeps its unit, source, sample count, formula, and quality flags.
                    Unavailable is different from zero.
                  </p>
                </div>
              </div>
              <MetricTable title="Normalized and client metrics" metrics={normalizedMetrics} />
              <MetricTable title="Raw engine-native observations" metrics={rawEngineMetrics} />
              <MetricTable title="Engine-native deltas" metrics={engineMetricDeltas} />
              {detail.metrics.length === 0 && (
                <p className="empty-inline">
                  {detail.catchingUp
                    ? "Restoring persisted metric and request-sample history."
                    : "No persisted metric records yet. Summary metrics are written as the run finalizes; request latency observations above come from stored request samples."}
                </p>
              )}
              <ResourceTelemetry
                event={detail.systemSample ?? undefined}
                metrics={detail.metrics.filter((metric) => metric.source === "system_observed")}
                catchingUp={detail.catchingUp}
              />
            </section>

            <section className="run-subsection" aria-labelledby="run-samples-title">
              <div className="section-title-row">
                <div>
                  <h3 id="run-samples-title">Incomplete requests</h3>
                  <p className="helper-copy">
                    Warmup requests are preserved separately and excluded from measurement counts.
                  </p>
                </div>
              </div>
              <IncompleteSamples
                samples={detail.incompleteSamples}
                catchingUp={detail.catchingUp}
              />
              <p className="retention-note">
                Request/output bodies are never rendered in run history. Only timing, outcome,
                token-count provenance, and redacted metadata are shown.
              </p>
            </section>

            <section className="run-subsection" aria-labelledby="run-warnings-title">
              <div className="section-title-row">
                <h3 id="run-warnings-title">Warnings in latest events · {warnings.length}</h3>
              </div>
              {warnings.length === 0 ? (
                <p className="empty-inline">No run warnings were recorded.</p>
              ) : (
                <ul className="run-warning-list">
                  {warnings.map((event) => (
                    <li key={event.sequence_number}>
                      <strong>{String(event.payload.code ?? "Warning")}</strong>
                      <span>
                        {String(
                          event.payload.message ?? "Review this warning in the event timeline.",
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="run-subsection" aria-labelledby="run-timeline-title">
              <div className="section-title-row">
                <div>
                  <h3 id="run-timeline-title">Event timeline</h3>
                  <p className="helper-copy">
                    Persisted event IDs are replayed in sequence after reconnect.
                  </p>
                </div>
              </div>
              {detail.events.length === 0 ? (
                <p className="empty-inline">No run events have been recorded yet.</p>
              ) : (
                <ol className="run-event-timeline">
                  {detail.events.slice(-100).map((event) => (
                    <li key={event.sequence_number}>
                      <time dateTime={event.occurred_at}>{eventTime(event)}</time>
                      <span className="run-event-type">{statusLabel(event.event_type)}</span>
                      <span>{eventSummary(event)}</span>
                    </li>
                  ))}
                </ol>
              )}
              {detail.events.length > 100 && (
                <p className="source-note">
                  Showing the latest 100 of {detail.events.length} persisted events.
                </p>
              )}
            </section>
          </>
        ) : null}
      </section>
    </div>
  );
}

function RunFact({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="run-fact">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function Fingerprint({ plan }: { plan: BenchmarkPlan | null }) {
  const [digest, setDigest] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    if (!plan) return;
    void configurationFingerprint(plan).then((value) => {
      if (active) setDigest(value);
    });
    return () => {
      active = false;
    };
  }, [plan]);
  return (
    <span className="hash-value">
      {digest ? digest : plan ? "SHA-256 not available in this browser" : "Unavailable"}
    </span>
  );
}

function LatencyChart({
  samples,
  totalSampleCount,
  catchingUp,
}: {
  samples: RequestSample[];
  totalSampleCount: number;
  catchingUp: boolean;
}) {
  const width = 680;
  const height = 220;
  const left = 54;
  const right = 18;
  const top = 20;
  const bottom = 42;
  const displayedSamples = samples.slice(-500);
  const observed = displayedSamples.map((sample) => sample.latency_ms ?? 0);
  const maximum = observed.reduce((current, value) => Math.max(current, value), 1);
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  return (
    <section className="run-subsection" aria-labelledby="run-latency-chart-title">
      <div className="section-title-row">
        <div>
          <h3 id="run-latency-chart-title">Request latency observations</h3>
          <p className="helper-copy">
            Latest observed measurement request points · milliseconds · {samples.length} shown of{" "}
            {totalSampleCount} total samples. No missing values are interpolated.
          </p>
        </div>
      </div>
      {samples.length === 0 ? (
        <p className="chart-no-data" role="status">
          {catchingUp
            ? "Restoring persisted request samples; latency availability will update as pages load."
            : totalSampleCount > 0
              ? `The latest saved requests have no latency observations. ${totalSampleCount} latency samples were recorded earlier in the run.`
              : "No measured request latency samples are available yet."}
        </p>
      ) : (
        <>
          <div className="run-chart-scroll">
            <svg
              aria-label={`Request latency chart with ${displayedSamples.length} of ${totalSampleCount} observed samples shown, in milliseconds`}
              className="run-latency-chart"
              role="img"
              viewBox={`0 0 ${width} ${height}`}
            >
              {[0, 0.5, 1].map((fraction) => {
                const y = top + chartHeight * (1 - fraction);
                return (
                  <g key={fraction}>
                    <line x1={left} x2={width - right} y1={y} y2={y} />
                    <text x={left - 8} y={y + 4} textAnchor="end">
                      {Math.round(maximum * fraction)}
                    </text>
                  </g>
                );
              })}
              <line className="run-chart-axis" x1={left} x2={left} y1={top} y2={height - bottom} />
              <line
                className="run-chart-axis"
                x1={left}
                x2={width - right}
                y1={height - bottom}
                y2={height - bottom}
              />
              {displayedSamples.map((sample, index) => {
                const x =
                  displayedSamples.length === 1
                    ? left + chartWidth / 2
                    : left + (index / (displayedSamples.length - 1)) * chartWidth;
                const y = top + chartHeight * (1 - (sample.latency_ms ?? 0) / maximum);
                return (
                  <circle
                    key={sample.sequence_number}
                    cx={x}
                    cy={y}
                    r="4"
                    aria-label={`Request ${sample.sequence_number + 1}: ${formatNumber(sample.latency_ms, "ms")}`}
                  />
                );
              })}
              <text
                className="run-chart-axis-label"
                transform={`translate(14 ${top + chartHeight / 2}) rotate(-90)`}
                textAnchor="middle"
              >
                Latency (ms)
              </text>
              <text
                className="run-chart-axis-label"
                x={left + chartWidth / 2}
                y={height - 8}
                textAnchor="middle"
              >
                Measured request sample index
              </text>
            </svg>
          </div>
          <div className="table-scroll">
            <table>
              <caption>Accessible table of the latency points shown in the chart</caption>
              <thead>
                <tr>
                  <th scope="col">Sample</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Latency</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {displayedSamples.map((sample) => (
                  <tr key={sample.sequence_number}>
                    <th scope="row">{sample.sequence_number + 1}</th>
                    <td>{sample.outcome}</td>
                    <td>{formatNumber(sample.latency_ms, "ms")}</td>
                    <td>Client measured</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalSampleCount > displayedSamples.length && (
            <p className="source-note">
              Showing the latest {displayedSamples.length} of {totalSampleCount} measured latency
              samples.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function MetricTable({ title, metrics }: { title: string; metrics: MetricSample[] }) {
  return (
    <section className="metric-group" aria-label={title}>
      <h4>{title}</h4>
      {metrics.length === 0 ? (
        <p className="empty-inline">No records in this category.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                <th scope="col">Value</th>
                <th scope="col">Unit</th>
                <th scope="col">Source</th>
                <th scope="col">Samples</th>
                <th scope="col">Definition and quality</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((metric, index) => (
                <tr key={`${metric.metric_name}-${metric.aggregation}-${index}`}>
                  <th scope="row">
                    {metric.metric_name}
                    <small className="table-subtext">{metric.aggregation}</small>
                  </th>
                  <td>{metric.value === null ? "Unavailable" : formatNumber(metric.value)}</td>
                  <td>{metric.unit}</td>
                  <td>{metric.source}</td>
                  <td>{metric.sample_count.toLocaleString()}</td>
                  <td>
                    <span>{metric.definition}</span>
                    <small className="table-subtext">
                      Formula: {metric.formula} · {metric.method_version}
                      {metric.quality_flags.length ? ` · ${metric.quality_flags.join(", ")}` : ""}
                    </small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ResourceTelemetry({
  event,
  metrics,
  catchingUp,
}: {
  event: RunEventRecord | undefined;
  metrics: MetricSample[];
  catchingUp: boolean;
}) {
  if (metrics.length > 0)
    return (
      <p className="source-note">
        System telemetry is available from {metrics.map((metric) => metric.source).join(", ")}{" "}
        sources; inspect values and units in the metrics table.
      </p>
    );
  if (event?.payload.available === true)
    return (
      <p className="source-note">
        A system telemetry sample was reported; normalized resource metric values are not available.
      </p>
    );
  if (event)
    return (
      <p className="source-note">
        Resource telemetry unavailable:{" "}
        {String(event.payload.reason ?? "the host sampler did not report readings")}. This is
        unavailable data, not zero usage.
      </p>
    );
  return (
    <p className="source-note">
      {catchingUp
        ? "Restoring telemetry availability from persisted events."
        : "No resource telemetry was reported for this run."}
    </p>
  );
}

function IncompleteSamples({
  samples,
  catchingUp,
}: {
  samples: RequestSample[];
  catchingUp: boolean;
}) {
  if (samples.length === 0)
    return (
      <p className="empty-inline">
        {catchingUp
          ? "Restoring incomplete request samples."
          : "No incomplete request samples remain."}
      </p>
    );
  return (
    <div className="table-scroll">
      <table>
        <caption>Incomplete or interrupted measurement request samples</caption>
        <thead>
          <tr>
            <th scope="col">Request</th>
            <th scope="col">Phase</th>
            <th scope="col">Outcome</th>
            <th scope="col">Error class</th>
            <th scope="col">Partial output</th>
          </tr>
        </thead>
        <tbody>
          {samples.map((sample) => (
            <tr key={sample.sequence_number}>
              <th scope="row">{sample.sequence_number + 1}</th>
              <td>{sample.phase}</td>
              <td>{sample.outcome}</td>
              <td>{sample.error_class ?? "Not reported"}</td>
              <td>{sample.partial_output_observed ? "Observed; body omitted" : "None recorded"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {samples.length === 500 && (
        <p className="source-note">Showing the latest 500 incomplete or interrupted samples.</p>
      )}
    </div>
  );
}

function machineSummary(
  machine: Pick<MachineProfile, "id" | "label" | "status" | "hardware"> | null,
): string {
  if (!machine) return "Machine profile details were not attached to this plan.";
  const facts = [
    machine.hardware?.os_name,
    machine.hardware?.architecture,
    machine.hardware?.cpu_model,
  ].filter(Boolean);
  const memory = machine.hardware?.memory_total_bytes;
  if (memory) facts.push(`${(memory / 1024 ** 3).toFixed(1)} GiB RAM`);
  return facts.length
    ? `Saved machine profile: ${facts.join(" · ")}`
    : `Saved machine profile: ${machine.label} · hardware details unavailable`;
}
