export type ApiHealth = {
  status: "ok";
  api_version: "v1";
  app_version: string;
  database: { status: "ready"; schema_version: number };
};

export type CapabilityState = "supported" | "unsupported" | "unknown" | "user_supplied";
export type CapabilityValue = {
  state: CapabilityState;
  details: string | null;
  source: string | null;
};
export type EngineMode =
  "attached_endpoint" | "managed_process" | "library_pipeline" | "composed_serving_stack";

export type MachineProfile = {
  id: string;
  label: string;
  status: "detected" | "partially_detected" | "manual_unverified";
  host_name: string | null;
  hardware: {
    os_name: string | null;
    os_version: string | null;
    architecture: string | null;
    cpu_model: string | null;
    cpu_logical_cores: number | null;
    memory_total_bytes: number | null;
    accelerators: Array<{
      name: string;
      vendor: string | null;
      memory_bytes: number | null;
      runtime: string | null;
      driver_version: string | null;
    }> | null;
  } | null;
  provenance: Record<
    string,
    { source: string; confidence: number | null; observed_at: string | null }
  >;
  created_at: string;
  updated_at: string;
};

export type AdapterManifest = {
  adapter_id: string;
  adapter_version: string;
  display_name: string;
  description: string;
  category: string;
  integration_modes: EngineMode[];
  operating_systems: string[];
  implementation_status: "implemented" | "planned" | "unsupported";
  version_tested: string[];
  maintenance_mode: boolean;
  recommended_for_new_managed_install: boolean;
  status_details: string | null;
  declared_capabilities: string[];
  supported_transports: string[];
  native_metrics: string[];
  config_schema: Record<string, unknown>;
  known_limitations: string[];
  documentation_url: string | null;
};

export type EngineProfile = {
  id: string;
  label: string;
  adapter_id: string;
  engine_display_name: string | null;
  adapter_version: string | null;
  integration_mode: EngineMode;
  status: "enabled" | "disabled" | "needs_attention";
  managed_process_running: boolean;
  endpoint_url: string | null;
  is_remote: boolean;
  remote_access_enabled: boolean;
  engine_version: string | null;
  last_probed_at: string | null;
  capability_snapshot_id: string | null;
  capabilities: Record<string, CapabilityValue> | null;
  machine_profile_id: string | null;
  selected_model_ref: string | null;
  user_notes: string | null;
  engine_extensions?: Record<string, Record<string, unknown>>;
  configured_fields: string[];
  credential_configured: boolean;
  created_at: string;
  updated_at: string;
  schema_version: number;
};

export type AdapterModel = {
  model_ref: string;
  display_name: string;
  revision: string | null;
  metadata: Record<string, unknown>;
};

export type Diagnostic = {
  code: string;
  message: string;
  severity: "info" | "warning" | "error";
  retryable: boolean;
};

export type ProbeResult = {
  adapter_id: string;
  adapter_version: string;
  status: "succeeded" | "failed";
  reachable: CapabilityValue;
  health: CapabilityValue;
  engine_version: string | null;
  models: AdapterModel[];
  checks: Record<string, CapabilityValue>;
  capabilities: { capabilities: Record<string, CapabilityValue> };
  diagnostics: Diagnostic[];
  probed_at: string;
};

export type ProbeResponse = { profile: EngineProfile; result: ProbeResult };

export type EngineModel = {
  id: string;
  engine_profile_id: string;
  model_ref: string | null;
  display_name: string;
  revision: string | null;
  format: string | null;
  size_bytes: number | null;
  tokenizer_identity: string | null;
  max_context_tokens: number | null;
  engine_extensions?: Record<string, Record<string, unknown>>;
  status: string;
};

export type PlaygroundStreamEvent =
  | { type: "chunk"; text: string }
  | {
      type: "usage";
      input_tokens: number | null;
      output_tokens: number | null;
      exact: boolean;
    }
  | { type: "complete"; elapsed_ms: number }
  | { type: "error"; code: string; message: string };

export type BenchmarkRun = {
  id: string;
  plan_id: string;
  status:
    | "queued"
    | "preflight"
    | "preparing"
    | "warming"
    | "running"
    | "cancelling"
    | "completed"
    | "failed"
    | "cancelled"
    | "interrupted"
    | string;
  worker_id: string | null;
  potentially_contended: boolean;
  profile_snapshot?: RunProfileSnapshot;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type RunProfileSnapshot = {
  captured_at: string;
  machine_profile_id: string | null;
  machine: Pick<MachineProfile, "id" | "label" | "status" | "hardware"> | null;
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
};

export type RunEventRecord = {
  schema_version: 1;
  run_id: string;
  sequence_number: number;
  event_type: string;
  request_id: string | null;
  monotonic_offset_ms: number;
  occurred_at: string;
  payload: Record<string, unknown>;
};

export type RequestSample = {
  id: string;
  run_id: string;
  sequence_number: number;
  request_id: string | null;
  phase: "warmup" | "measurement";
  concurrency_level: number | null;
  effective_concurrency: number | null;
  outcome:
    | "in_progress"
    | "succeeded"
    | "failed"
    | "timed_out"
    | "cancelled"
    | "disconnected"
    | "interrupted";
  started_at: string;
  completed_at: string | null;
  dispatch_offset_ms: number | null;
  first_output_offset_ms: number | null;
  completed_offset_ms: number | null;
  queue_time_ms: number | null;
  latency_ms: number | null;
  ttft_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  prompt_token_count_source: string;
  completion_token_count_source: string;
  error_class: string | null;
  output_event_count: number;
  output_characters: number;
  partial_output_observed: boolean;
  content_retained: boolean;
  prompt_text?: string | null;
  output_text?: string | null;
  created_at: string;
  updated_at: string;
};

export type MetricSample = {
  id: string;
  run_id: string;
  metric_name: string;
  value: number | null;
  unit: string;
  source: "client_measured" | "engine_native" | "system_observed" | "derived" | "user_supplied";
  aggregation: string;
  sample_count: number;
  definition: string;
  formula: string;
  method_version: string;
  quality_flags: string[];
  metric_kind: "gauge" | "counter";
  adapter_id: string | null;
  adapter_version: string | null;
  engine_version: string | null;
  observed_at: string;
  labels: Record<string, string>;
};

export type RunEventPage = {
  events: RunEventRecord[];
  next_sequence_number: number | null;
  terminal: boolean;
};

export type RunSamplesResponse = {
  samples: RequestSample[];
  next_sequence_number: number | null;
};

export type RunMetricsResponse = { metrics: MetricSample[] };

export type Dataset = {
  id: string;
  name: string;
  source: string;
  status: string;
  item_count: number | null;
  content_hash: string | null;
  snapshot_hash: string | null;
  content_retained: boolean;
  format: string | null;
  prompt_field: string | null;
  expected_answer_field: string | null;
  row_limit: number | null;
  shuffle_seed: number | null;
  prompt_sizing_method: string | null;
  tokenizer_identity: string | null;
  count_method: string | null;
  duplicate_count: number;
  redacted_summary: string | null;
};

export type DatasetImportResponse = {
  dataset: Dataset;
  preview: Array<{
    row_number: number;
    prompt_preview: string;
    prompt_size: number | null;
    expected_answer_present: boolean;
  }>;
  parsed_rows: number;
  selected_rows: number;
  duplicate_rows: number;
  rows_excluded_by_limit: number;
  count_method: string;
};

export type Workload = {
  id: string;
  name: string;
  kind: "chat" | "completion";
  status: "draft" | "ready" | "invalid";
  input_mode: "dataset" | "synthetic";
  dataset_id: string | null;
  preset: string | null;
  request_count: number | null;
  duration_seconds: number | null;
  concurrency: number;
  concurrency_levels: number[];
  traffic_mode: "closed_loop" | "open_loop";
  arrival_rate_per_second: number | null;
  warmup_count: number;
  target_input_tokens: number | null;
  target_output_tokens: number | null;
  max_output_tokens: number;
  prompt_sizing_method: "characters" | "words" | "tokenizer";
  random_seed: number;
  sampling_config: Record<string, string | number | boolean | null>;
};

export type WorkloadInput = Omit<Workload, "id" | "status" | "concurrency_levels"> & {
  concurrency_levels: number[];
};

export type BenchmarkPlan = {
  id: string;
  name: string;
  status: "draft" | "ready" | "blocked";
  machine_profile_id: string | null;
  engine_profile_id: string;
  model_artifact_id: string | null;
  workload_id: string;
  warmup_requests: number;
  measured_requests: number | null;
  measurement_duration_seconds: number | null;
  concurrency: number;
  timeout_seconds: number;
  retain_prompt_output: boolean;
  dataset_snapshot_hash: string | null;
  configuration_snapshot: Record<string, unknown>;
};

export type PreflightIssue = { code: string; message: string };
export type BenchmarkPreflight = {
  valid: boolean;
  target: Record<string, unknown>;
  workload: Record<string, unknown> | null;
  supported_options: string[];
  unknown_options: string[];
  unsupported_options: string[];
  missing_prerequisites: string[];
  estimated_duration_seconds: number | null;
  estimated_request_volume: number | null;
  resource_contention: string[];
  remote_data_egress: {
    code: string;
    host: string;
    message: string;
    request_will_be_sent: boolean;
  } | null;
  blockers: PreflightIssue[];
  warnings: PreflightIssue[];
};

export type BenchmarkPlanResponse = { plan: BenchmarkPlan; preflight: BenchmarkPreflight };

export type ExperimentTrial = {
  order: number;
  variant_name: string;
  variant_value: unknown;
  repetition: number;
  plan_id: string;
  run_id: string | null;
  status: "pending" | "launching" | "running" | "succeeded" | "failed";
  launch_started_at: string | null;
  error: string | null;
};

export type ExperimentGroup = {
  id: string;
  name: string;
  status: "draft" | "running" | "completed" | "failed";
  run_ids: string[];
  description: string | null;
  baseline_plan_id: string | null;
  independent_variable: string | null;
  baseline_value: unknown;
  variants: Array<{ name: string; value: unknown }>;
  repetitions: number;
  randomize_order: boolean;
  order_seed: number;
  estimated_duration_seconds: number | null;
  trials: ExperimentTrial[];
};

export type ExperimentCreateResponse = {
  group: ExperimentGroup;
  estimated_run_count: number;
  estimated_duration_seconds: number | null;
};

export type ExperimentSummary = {
  experiment_group_id: string;
  name: string;
  status: string;
  independent_variable: string | null;
  hypothesis: string | null;
  repetitions: number;
  randomize_order: boolean;
  order_seed: number;
  comparison: RunComparison | null;
  variants: Array<{
    name: string;
    value: unknown;
    trial_count: number;
    succeeded_count: number;
    failed_count: number;
    metrics: Array<{
      metric_name: string;
      labels: Record<string, string>;
      unit: string;
      source: string;
      aggregation: string;
      definition: string;
      formula: string;
      method_version: string;
      metric_kind: "gauge" | "counter";
      median: number | null;
      spread_min: number | null;
      spread_max: number | null;
      repeat_count: number;
      sample_count: number;
    }>;
  }>;
  failed_trials: ExperimentTrial[];
};

export type RunComparison = {
  run_ids: string[];
  baseline_run_id: string;
  declared_variable: { path: string; values: unknown[] } | null;
  grade: "A" | "B" | "C" | "not_comparable";
  label: string;
  rationale: string;
  controlled_comparison_badge: boolean;
  causal_interpretation_allowed: boolean;
  dimensions: Array<{
    key: string;
    label: string;
    state: string;
    values: unknown[];
    changes?: unknown[];
  }>;
  workload_changes: unknown[];
  excluded_metrics: Array<{ metric_name: string; reason: string }>;
  shared_metrics: Array<{
    metric_name: string;
    unit: string;
    source: string;
    aggregation: string;
    definition: string;
    formula: string;
    method_version: string;
    metric_kind: "gauge" | "counter";
    labels: Record<string, string>;
    baseline_run_id: string;
    runs: Record<string, { value: number | null; sample_count: number; record_count: number }>;
    observed_deltas: Record<string, number | null>;
  }>;
  run_statuses: Record<string, string>;
};

export type ReportMetadata = {
  id: string;
  name: string;
  format: "json" | "csv" | "markdown";
  status: "ready" | "failed";
  run_ids: string[];
  generated_at: string;
  export_ref: string | null;
  filename: string | null;
  content_included: boolean;
  error_message: string | null;
  redacted_summary: string | null;
};

export type ExperimentCreateInput = {
  name: string;
  baseline_plan_id: string;
  independent_variable: string;
  variants: Array<{ name: string; value: string | number }>;
  repetitions: number;
  randomize_order: boolean;
  order_seed: number;
  hypothesis: string | null;
};

export type ReproduciblePlan = {
  format_version: 1;
  app_version: string;
  database_schema_version: number;
  exported_at: string;
  workload: Workload;
  plan: {
    name: string;
    engine_profile_id: string;
    machine_profile_id: string | null;
    model_artifact_id: string | null;
    warmup_requests: number;
    measured_requests: number | null;
    measurement_duration_seconds: number | null;
    concurrency: number;
    timeout_seconds: number;
    retain_prompt_output: boolean;
    dataset_snapshot_hash: string | null;
  };
};

export type EngineProfileInput = {
  label: string;
  adapter_id: string;
  engine_display_name: string | null;
  integration_mode: EngineMode;
  endpoint_url: string | null;
  health_route?: string | null;
  models_route?: string | null;
  secret_refs?: Record<string, string>;
  engine_extensions?: Record<string, Record<string, unknown>>;
  user_notes: string | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
    readonly host: string | null = null,
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isApiHealth(value: unknown): value is ApiHealth {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<ApiHealth>;
  return (
    candidate.status === "ok" &&
    candidate.api_version === "v1" &&
    typeof candidate.app_version === "string" &&
    typeof candidate.database === "object" &&
    candidate.database !== null &&
    candidate.database.status === "ready" &&
    typeof candidate.database.schema_version === "number"
  );
}

let sessionToken: string | null = null;

async function readSessionToken(): Promise<string> {
  if (sessionToken) return sessionToken;
  const response = await fetch("/api/v1/session", { headers: { Accept: "application/json" } });
  if (!response.ok)
    throw new ApiError("Could not establish a local editing session.", response.status);
  const payload: unknown = await response.json();
  if (
    typeof payload !== "object" ||
    payload === null ||
    typeof (payload as { token?: unknown }).token !== "string"
  ) {
    throw new ApiError("The local API returned an invalid session response.", response.status);
  }
  sessionToken = (payload as { token: string }).token;
  return sessionToken;
}

async function isStaleSessionResponse(response: Response): Promise<boolean> {
  if (response.status !== 403) return false;
  try {
    const payload = (await response.clone().json()) as { detail?: { code?: unknown } };
    return payload.detail?.code === "local_session_required";
  } catch {
    return false;
  }
}

/**
 * Send a mutating request with the local session header. The API issues a new token each time
 * it starts, so a rejected token is refreshed once and the request retried.
 */
async function fetchWithSession(path: string, init: RequestInit): Promise<Response> {
  const send = async () => {
    const headers = new Headers(init.headers);
    headers.set("X-LOCALFORGE-SESSION", await readSessionToken());
    return fetch(path, { ...init, headers });
  };
  const response = await send();
  if (!(await isStaleSessionResponse(response))) return response;
  sessionToken = null;
  return send();
}

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const mutating = ["POST", "PUT", "PATCH", "DELETE"].includes(method);
  let response: Response;
  try {
    response = mutating
      ? await fetchWithSession(path, { ...init, headers })
      : await fetch(path, { ...init, headers });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "The local API could not be reached. Check that it is running, then retry.",
      0,
    );
  }
  if (!response.ok) {
    let message = `The local API returned an error (${response.status}).`;
    let code: string | null = null;
    let host: string | null = null;
    const fieldErrors: Record<string, string> = {};
    try {
      const payload: unknown = await response.json();
      const detail =
        typeof payload === "object" && payload !== null
          ? (payload as { detail?: unknown }).detail
          : null;
      if (Array.isArray(detail)) {
        for (const issue of detail) {
          if (typeof issue !== "object" || issue === null) continue;
          const value = issue as { loc?: unknown; msg?: unknown };
          if (!Array.isArray(value.loc) || typeof value.msg !== "string") continue;
          const field = value.loc.filter((part): part is string => typeof part === "string").at(-1);
          if (field) fieldErrors[field] = value.msg;
        }
        message = "Some workload settings need attention.";
      } else if (typeof detail === "object" && detail !== null) {
        const record = detail as { message?: unknown; code?: unknown; host?: unknown };
        if (typeof record.message === "string") message = record.message;
        if (typeof record.code === "string") code = record.code;
        if (typeof record.host === "string") host = record.host;
        const fields = (detail as { fields?: unknown }).fields;
        if (Array.isArray(fields) && typeof record.message === "string") {
          for (const field of fields) {
            if (typeof field === "string") fieldErrors[field.split(".").at(-1)!] = record.message;
          }
        }
      }
    } catch {
      // Keep the bounded status message if the API response is not JSON.
    }
    throw new ApiError(message, response.status, code, host, fieldErrors);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function fetchApiHealth(signal?: AbortSignal): Promise<ApiHealth> {
  const payload = await apiRequest<unknown>("/health", { signal });
  if (!isApiHealth(payload)) {
    throw new ApiError("The local API returned an invalid health response.", 200);
  }
  return payload;
}

export const listEngineCatalog = () => apiRequest<AdapterManifest[]>("/api/v1/engines/catalog");
export const listEngineProfiles = () => apiRequest<EngineProfile[]>("/api/v1/engines");
export const fetchSystemProfile = () => apiRequest<MachineProfile>("/api/v1/system");
export const listMachineProfiles = () => apiRequest<MachineProfile[]>("/api/v1/machine-profiles");
export const listRuns = (limit = 100) => apiRequest<BenchmarkRun[]>(`/api/v1/runs?limit=${limit}`);
export const getRun = (id: string) => apiRequest<BenchmarkRun>(`/api/v1/runs/${id}`);
export const cancelBenchmarkRun = (id: string) =>
  apiRequest<BenchmarkRun>(`/api/v1/runs/${id}/cancel`, { method: "POST", body: "{}" });
export const listRunEvents = (id: string, after = -1, limit = 1000) =>
  apiRequest<RunEventPage>(`/api/v1/runs/${id}/events?after=${after}&limit=${limit}`);
export const listRunSamples = (id: string, after = -1, limit = 1000, incompleteOnly = false) =>
  apiRequest<RunSamplesResponse>(
    `/api/v1/runs/${id}/samples?after=${after}&limit=${limit}&incomplete_only=${incompleteOnly}`,
  );
export const listRunMetrics = (id: string) =>
  apiRequest<RunMetricsResponse>(`/api/v1/runs/${id}/metrics`);
export const listDatasets = () => apiRequest<Dataset[]>("/api/v1/datasets");
export const listWorkloads = () => apiRequest<Workload[]>("/api/v1/workloads");
export const listBenchmarkPlans = () => apiRequest<BenchmarkPlan[]>("/api/v1/benchmark-plans");
export const importBenchmarkPlan = (bundle: ReproduciblePlan) =>
  apiRequest<BenchmarkPlanResponse>("/api/v1/benchmark-plans/import", {
    method: "POST",
    body: JSON.stringify(bundle),
  });
export const listExperimentGroups = () => apiRequest<ExperimentGroup[]>("/api/v1/experiments");
export const getExperimentGroup = (id: string) =>
  apiRequest<ExperimentGroup>(`/api/v1/experiments/${id}`);
export const getExperimentSummary = (id: string) =>
  apiRequest<ExperimentSummary>(`/api/v1/experiments/${id}/summary`);
export const createExperimentGroup = (input: ExperimentCreateInput) =>
  apiRequest<ExperimentCreateResponse>("/api/v1/experiments", {
    method: "POST",
    body: JSON.stringify(input),
  });
export const startExperimentGroup = (id: string, confirmEstimatedRuntime: boolean) =>
  apiRequest<ExperimentGroup>(`/api/v1/experiments/${id}/start`, {
    method: "POST",
    body: JSON.stringify({ confirm_estimated_runtime: confirmEstimatedRuntime }),
  });
export const launchNextExperimentTrial = (id: string) =>
  apiRequest<{ group: ExperimentGroup; run: BenchmarkRun | null }>(
    `/api/v1/experiments/${id}/next`,
    { method: "POST", body: "{}" },
  );
export const compareRuns = (input: {
  run_ids: string[];
  baseline_run_id?: string | null;
  declared_variable?: string | null;
}) =>
  apiRequest<RunComparison>("/api/v1/comparisons", {
    method: "POST",
    body: JSON.stringify(input),
  });
export const createReport = (input: {
  name: string;
  filename: string | null;
  format: "json" | "csv" | "markdown";
  run_ids: string[];
  baseline_run_id: string | null;
  experiment_group_id: string | null;
  include_retained_content: boolean;
}) =>
  apiRequest<{ report: ReportMetadata }>("/api/v1/reports", {
    method: "POST",
    body: JSON.stringify(input),
  });
export const listEngineModels = (id: string) =>
  apiRequest<EngineModel[]>(`/api/v1/engines/${id}/models`);

export async function streamPlaygroundPrompt(
  input: {
    profile_id: string;
    model_ref: string;
    prompt: string;
    max_tokens: number;
    temperature: number;
    confirm_remote_egress: boolean;
  },
  signal: AbortSignal,
  onEvent: (event: PlaygroundStreamEvent) => void,
): Promise<void> {
  const headers = new Headers({
    Accept: "application/x-ndjson",
    "Content-Type": "application/json",
  });
  let response: Response;
  try {
    response = await fetchWithSession("/api/v1/playground/stream", {
      method: "POST",
      headers,
      body: JSON.stringify(input),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "The local API could not be reached. Check that it is running, then retry.",
      0,
    );
  }

  if (!response.ok) {
    let message = `The local API returned an error (${response.status}).`;
    let code: string | null = null;
    let host: string | null = null;
    try {
      const payload: unknown = await response.json();
      const detail =
        typeof payload === "object" && payload !== null
          ? (payload as { detail?: unknown }).detail
          : null;
      if (typeof detail === "object" && detail !== null) {
        const record = detail as { message?: unknown; code?: unknown; host?: unknown };
        if (typeof record.message === "string") message = record.message;
        if (typeof record.code === "string") code = record.code;
        if (typeof record.host === "string") host = record.host;
      }
    } catch {
      // Retain the bounded status message for non-JSON responses.
    }
    throw new ApiError(message, response.status, code, host);
  }

  if (!response.body) throw new ApiError("The engine returned no response stream.", 502);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let completed = false;
  const consumeLine = (line: string) => {
    if (!line.trim()) return;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      throw new ApiError("The engine returned an invalid stream event.", 502);
    }
    if (
      typeof value !== "object" ||
      value === null ||
      typeof (value as { type?: unknown }).type !== "string"
    )
      throw new ApiError("The engine returned an invalid stream event.", 502);
    const event = value as PlaygroundStreamEvent;
    if (event.type === "error") throw new ApiError(event.message, 502, event.code);
    if (event.type === "complete") completed = true;
    onEvent(event);
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let newline = pending.indexOf("\n");
      while (newline >= 0) {
        consumeLine(pending.slice(0, newline).replace(/\r$/, ""));
        pending = pending.slice(newline + 1);
        newline = pending.indexOf("\n");
      }
      if (done) break;
    }
    if (pending.trim()) consumeLine(pending);
    if (!completed) throw new ApiError("The engine stream ended before completion.", 502);
  } finally {
    reader.releaseLock();
  }
}

export const createEngineProfile = (input: EngineProfileInput) =>
  apiRequest<EngineProfile>("/api/v1/engines", { method: "POST", body: JSON.stringify(input) });

export const updateEngineProfile = (id: string, input: Partial<EngineProfileInput>) => {
  const patch: Partial<Omit<EngineProfileInput, "adapter_id">> = {
    label: input.label,
    engine_display_name: input.engine_display_name,
    integration_mode: input.integration_mode,
    endpoint_url: input.endpoint_url,
    user_notes: input.user_notes,
    ...(input.health_route === undefined ? {} : { health_route: input.health_route }),
    ...(input.models_route === undefined ? {} : { models_route: input.models_route }),
    ...(input.secret_refs === undefined ? {} : { secret_refs: input.secret_refs }),
    ...(input.engine_extensions === undefined
      ? {}
      : { engine_extensions: input.engine_extensions }),
  };
  return apiRequest<EngineProfile>(`/api/v1/engines/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
};

export const probeEngineProfile = (id: string, confirmRemoteEgress: boolean) =>
  apiRequest<ProbeResponse>(`/api/v1/engines/${id}/probe`, {
    method: "POST",
    body: JSON.stringify({ confirm_remote_egress: confirmRemoteEgress }),
  });

export const enableEngineProfile = (id: string) =>
  apiRequest<EngineProfile>(`/api/v1/engines/${id}/enable`, { method: "POST", body: "{}" });
export const startEngineProfile = (id: string) =>
  apiRequest<EngineProfile>(`/api/v1/engines/${id}/start`, { method: "POST", body: "{}" });
export const stopEngineProfile = (id: string) =>
  apiRequest<EngineProfile>(`/api/v1/engines/${id}/stop`, { method: "POST", body: "{}" });

export const deleteEngineProfile = (id: string) =>
  apiRequest<void>(`/api/v1/engines/${id}`, { method: "DELETE" });

export const saveCurrentMachineProfile = () =>
  apiRequest<MachineProfile>("/api/v1/machine-profiles/current", { method: "PUT", body: "{}" });

export const importDataset = (
  file: File,
  options: {
    name: string;
    format: "jsonl" | "csv";
    promptField: string;
    expectedAnswerField: string;
    rowLimit: number;
    shuffleSeed: number;
    promptSizingMethod: "characters" | "words" | "tokenizer";
    tokenizerName: string;
    retainContent: boolean;
  },
) => {
  const body = new FormData();
  body.set("file", file);
  body.set("name", options.name);
  body.set("format", options.format);
  body.set("prompt_field", options.promptField);
  if (options.expectedAnswerField) body.set("expected_answer_field", options.expectedAnswerField);
  body.set("row_limit", String(options.rowLimit));
  body.set("shuffle_seed", String(options.shuffleSeed));
  body.set("prompt_sizing_method", options.promptSizingMethod);
  if (options.tokenizerName) body.set("tokenizer_name", options.tokenizerName);
  body.set("retain_content", String(options.retainContent));
  return apiRequest<DatasetImportResponse>("/api/v1/datasets/import", { method: "POST", body });
};

export const createWorkload = (input: WorkloadInput) =>
  apiRequest<Workload>("/api/v1/workloads", { method: "POST", body: JSON.stringify(input) });

export const replayWorkload = (id: string) =>
  apiRequest<Workload>(`/api/v1/workloads/${id}/replay`, { method: "POST", body: "{}" });

export const preflightBenchmarkPlan = (input: {
  name: string;
  engine_profile_id: string;
  machine_profile_id: string | null;
  model_artifact_id: string;
  workload_id: string;
  retain_prompt_output: boolean;
}) =>
  apiRequest<BenchmarkPreflight>("/api/v1/benchmark-plans/preflight", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const createBenchmarkPlan = (input: {
  name: string;
  engine_profile_id: string;
  machine_profile_id: string | null;
  model_artifact_id: string;
  workload_id: string;
  retain_prompt_output: boolean;
}) =>
  apiRequest<BenchmarkPlanResponse>("/api/v1/benchmark-plans", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const preflightSavedPlan = (id: string) =>
  apiRequest<BenchmarkPreflight>(`/api/v1/benchmark-plans/${id}/preflight`, {
    method: "POST",
    body: "{}",
  });

export const startBenchmarkRun = (planId: string, overrideContention = false) =>
  apiRequest<{ run: BenchmarkRun; conflicting_resources: string[] }>("/api/v1/runs", {
    method: "POST",
    body: JSON.stringify({ plan_id: planId, override_contention: overrideContention }),
  });

// --- Workspace settings and hardware detail additions ---

export type WorkspaceSettings = {
  show_test_fixture_engines: boolean;
  default_retain_prompt_output: boolean;
  default_request_timeout_seconds: number;
  default_warmup_count: number;
};

export type StorageStatus = {
  data_directory: string;
  database_path: string;
  free_bytes: number | null;
  total_bytes: number | null;
  low_space: boolean | null;
  low_space_threshold_bytes: number;
};

export type WorkspaceSettingsResponse = {
  settings: WorkspaceSettings;
  defaults: WorkspaceSettings;
  saved: boolean;
  test_fixture_adapter_ids: string[];
  storage: StorageStatus;
};

/** Optional detection details reported by newer APIs; absent values mean unknown. */
export type AcceleratorDetails = NonNullable<
  NonNullable<MachineProfile["hardware"]>["accelerators"]
>[number] & {
  integrated?: boolean | null;
  memory_kind?: "dedicated" | "shared" | null;
  detected_by?: string | null;
};

export const fetchWorkspaceSettings = () =>
  apiRequest<WorkspaceSettingsResponse>("/api/v1/settings");

/** Save every setting. Validation failures map each rejected field to its API error type. */
export async function saveWorkspaceSettings(
  settings: WorkspaceSettings,
): Promise<WorkspaceSettingsResponse> {
  let response: Response;
  try {
    response = await fetchWithSession("/api/v1/settings", {
      method: "PUT",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(settings),
    });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "The local API could not be reached. Check that it is running, then retry.",
      0,
    );
  }
  if (response.ok) return (await response.json()) as WorkspaceSettingsResponse;
  let message = `The local API returned an error (${response.status}).`;
  let code: string | null = null;
  const fieldErrors: Record<string, string> = {};
  try {
    const payload = (await response.json()) as {
      detail?: { message?: unknown; code?: unknown; fields?: unknown };
    };
    const detail = payload.detail;
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") message = detail.message;
      if (typeof detail.code === "string") code = detail.code;
      if (Array.isArray(detail.fields)) {
        for (const item of detail.fields as unknown[]) {
          if (typeof item !== "object" || item === null) continue;
          const { field, error } = item as { field?: unknown; error?: unknown };
          if (typeof field !== "string") continue;
          fieldErrors[field.split(".").at(-1)!] = typeof error === "string" ? error : "invalid";
        }
      }
    }
  } catch {
    // Keep the bounded status message if the API response is not JSON.
  }
  throw new ApiError(message, response.status, code, null, fieldErrors);
}

export type HardwareDetails = NonNullable<MachineProfile["hardware"]> & {
  cpu_physical_cores?: number | null;
};

// --- Saved reports and model inventory (Reports and Models screens) ---

/** A report file saved by the API. `size_bytes` is null when unknown. */
export type SavedReport = {
  id: string;
  created_at: string;
  title: string;
  format: string;
  run_ids: string[];
  size_bytes: number | null;
  /** False when the saved file is missing from the reports folder. */
  available: boolean;
};

export const listReports = () => apiRequest<SavedReport[]>("/api/v1/reports");

/** Same-origin download route; used as a plain link, as the Experiments report export does. */
export const reportDownloadUrl = (id: string) =>
  `/api/v1/reports/${encodeURIComponent(id)}/download`;

export const deleteReport = (id: string) =>
  apiRequest<void>(`/api/v1/reports/${encodeURIComponent(id)}`, { method: "DELETE" });

/** A model visible to a connected engine. Every nullable field means "unknown", never zero. */
export type ModelRecord = {
  id: string;
  model_ref: string | null;
  display_name: string | null;
  status: string;
  engine_profile_id: string;
  engine_label: string | null;
  adapter_id: string | null;
  format: string | null;
  quantization: string | null;
  size_bytes: number | null;
  max_context_tokens: number | null;
  revision: string | null;
  tokenizer_identity: string | null;
  updated_at: string | null;
};

export const listModels = () => apiRequest<ModelRecord[]>("/api/v1/models");

// --- Run retention and plan cloning (Runs screen: delete, clean up, repeat with one change) ---

export const deleteRun = (id: string) =>
  apiRequest<void>(`/api/v1/runs/${id}`, { method: "DELETE" });

export type RunCleanupInput = {
  /** ISO timestamp: only runs created before it. */
  before: string | null;
  statuses: string[] | null;
  /** Always keep this many of the newest terminal runs. */
  keep_latest: number | null;
  dry_run: boolean;
};
export type RunCleanupResult = { run_ids: string[]; count: number; dry_run: boolean };

export const cleanupRuns = (input: RunCleanupInput) =>
  apiRequest<RunCleanupResult>("/api/v1/runs/cleanup", {
    method: "POST",
    body: JSON.stringify(input),
  });

export type PlanCloneChanges = Partial<{
  model_artifact_id: string | null;
  engine_profile_id: string;
  concurrency: number;
  max_output_tokens: number;
  temperature: number | null;
  warmup_count: number;
  request_count: number;
  request_timeout_seconds: number;
}>;
export type PlanCloneResponse = BenchmarkPlanResponse & { changed_fields: string[] };

export const cloneBenchmarkPlan = (
  planId: string,
  input: { name: string | null; changes: PlanCloneChanges },
) =>
  apiRequest<PlanCloneResponse>(`/api/v1/benchmark-plans/${planId}/clone`, {
    method: "POST",
    body: JSON.stringify(input),
  });

// --- Engine discovery: scan this computer (127.0.0.1) for running engines ---
export type DiscoveryPortStatus = "found" | "no_response" | "unrecognized" | "skipped_self";
export type DiscoveryScannedPort = { port: number; status: DiscoveryPortStatus };
export type DiscoveredEngine = {
  adapter_id: string;
  display_name: string;
  endpoint_url: string;
  engine_version: string | null;
  models: string[];
  evidence: string;
  existing_profile_ids: string[];
};
export type EngineDiscoveryResult = {
  scanned: DiscoveryScannedPort[];
  candidates: DiscoveredEngine[];
  duration_ms: number;
};

/** Only checks loopback ports on this computer; optional ports override the default list. */
export const discoverEngines = (ports?: number[]) =>
  apiRequest<EngineDiscoveryResult>(
    ports?.length
      ? `/api/v1/engines/discover?ports=${ports.map((port) => String(port)).join(",")}`
      : "/api/v1/engines/discover",
  );

// --- Engine recommendations: which inference engines suit this machine (ranked) ---
// Ranked by hardware fit and what is installed or running; not a speed measurement.
export type EngineFit = "best" | "good" | "possible" | "unsuitable";
export type EngineInstallStatus = "running" | "installed" | "not_installed" | "unknown";
export type EngineRecommendation = {
  rank: number;
  adapter_id: string;
  display_name: string;
  fit: EngineFit;
  score: number;
  reasons: string[];
  cautions: string[];
  status: EngineInstallStatus;
  status_detail: string | null;
  endpoint_url: string | null;
  existing_profile_ids: string[];
  install: { summary: string; url: string; command: string | null };
  measured: {
    completed_runs: number;
    best_generation_tokens_per_second: number | null;
    model_ref: string | null;
    run_id: string | null;
  } | null;
};
export type EngineRecommendationsResult = {
  generated_at: string;
  method: string;
  machine: {
    summary: string;
    os: string | null;
    arch: string | null;
    cpu_model: string | null;
    cpu_logical_cores: number | null;
    cpu_physical_cores: number | null;
    memory_total_bytes: number | null;
    accelerators: unknown[];
    cpu_features: { avx2: boolean | null; avx512: boolean | null };
  };
  model_size_guidance: {
    basis: "gpu_memory" | "system_memory";
    memory_budget_bytes: number | null;
    comfortable_params_billion_q4: number | null;
    max_params_billion_q4: number | null;
    /** Without a discrete GPU, speed limits model size; estimated from this machine's runs. */
    speed_basis?: "measured" | "not_measured" | "not_applicable";
    measured_read_gib_per_second?: number | null;
    readable_params_billion_q4?: number | null;
    text: string;
  };
  recommendations: EngineRecommendation[];
};

/** Rescans engines running on this computer (127.0.0.1) and ranks engines for it. */
export const fetchEngineRecommendations = () =>
  apiRequest<EngineRecommendationsResult>("/api/v1/engines/recommendations");

/* ---- Use-case presets, run targets, verdicts, and quality checks ---- */

/** Optional pass/fail thresholds saved on a plan; null means "no target". */
export type RunTargets = {
  ttft_p50_ms_max: number | null;
  ttft_p95_ms_max: number | null;
  latency_p50_ms_max: number | null;
  generation_tokens_per_second_min: number | null;
  request_throughput_min: number | null;
  error_rate_percent_max: number | null;
  quality_pass_rate_min: number | null;
};

/** Opt-in per-output checks; outputs are checked in memory and only outcomes are stored. */
export type QualityChecks = {
  exact_match: boolean;
  valid_json: boolean;
  json_required_keys: string[];
  stopped_properly: boolean;
  max_output_characters: number | null;
  must_contain: string[];
  must_not_contain: string[];
  case_sensitive: boolean;
};

export type UseCasePreset = {
  preset: string;
  title: string;
  description: string;
  input_tokens: number | null;
  output_tokens: number;
  concurrency: number;
  request_count: number;
  focus: "latency" | "throughput" | "quality" | string;
  default_targets: RunTargets;
  uses_builtin_dataset: boolean;
};

export type TargetEvaluation = {
  target: keyof RunTargets | string;
  label: string;
  comparison: "at_most" | "at_least";
  threshold: number;
  unit: string;
  observed: number | null;
  sample_count: number;
  status: "pass" | "fail" | "unknown";
  explanation: string;
};

export type RunVerdict = {
  run_id: string;
  plan_id: string;
  run_status: string;
  overall: "meets_targets" | "misses_targets" | "not_enough_data" | "no_targets";
  headline: string;
  summary: string;
  targets: RunTargets | null;
  target_source: "run_plan_snapshot" | "plan" | "none";
  evaluations: TargetEvaluation[];
  passed: number;
  failed: number;
  unknown: number;
  concurrency_level: string | null;
  method_version: string;
  quality_method_version: string | null;
};

export type PrefillEstimate = {
  available: boolean;
  input_tokens: number;
  prompt_tokens_per_second: number | null;
  expected_first_token_seconds: number | null;
  run_count: number;
  request_count: number;
  source_metric: string | null;
  message: string;
};

export type UseCaseWorkloadInput = {
  name: string;
  preset: string;
  request_count?: number | null;
  duration_seconds?: number | null;
  concurrency?: number;
  warmup_count?: number;
  max_output_tokens?: number;
  random_seed?: number;
  sampling_config?: Record<string, string | number | boolean | null>;
  quality_checks?: QualityChecks | null;
};

export const listUseCasePresets = () =>
  apiRequest<UseCasePreset[]>("/api/v1/workload-presets/use-cases");

/** Creates a use-case workload server-side (Quality smoke also prepares its built-in dataset). */
export const createUseCaseWorkload = (input: UseCaseWorkloadInput) =>
  apiRequest<Workload>("/api/v1/workloads/presets", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const updatePlanTargets = (planId: string, targets: RunTargets | null) =>
  apiRequest<BenchmarkPlan>(`/api/v1/benchmark-plans/${encodeURIComponent(planId)}/targets`, {
    method: "PUT",
    body: JSON.stringify({ targets }),
  });

export const fetchRunVerdict = (runId: string) =>
  apiRequest<RunVerdict>(`/api/v1/runs/${encodeURIComponent(runId)}/verdict`);

export const fetchPrefillEstimate = (params: {
  engineProfileId: string;
  modelArtifactId?: string | null;
  inputTokens: number;
}) => {
  const query = new URLSearchParams({
    engine_profile_id: params.engineProfileId,
    input_tokens: String(params.inputTokens),
  });
  if (params.modelArtifactId) query.set("model_artifact_id", params.modelArtifactId);
  return apiRequest<PrefillEstimate>(`/api/v1/prefill-estimate?${query.toString()}`);
};

// --- Run conditions: engine runtime settings, model weights, power state, prompt cache ---

export type RuntimeSettingField = {
  value: unknown;
  source: string | null;
  status?: "not_reported";
  observed?: "after_run";
};

export type RuntimeSettingsSnapshot = {
  schema_version: number;
  adapter_id: string;
  captured_at?: string;
  status?: "unavailable";
  fields: Record<string, RuntimeSettingField>;
  sources?: string[];
  notes?: string[];
  filled_after_run?: string[];
  process?: { executable: string };
};

export type ModelIdentity = {
  sha256: string | null;
  basis: "ollama_digest" | "file_hash" | "unknown";
  size_bytes: number | null;
  file_name: string | null;
  note?: string | null;
  source?: string;
  quantization_level?: string;
  parameter_size?: string;
};

export type PowerReading = {
  platform: string;
  captured_at: string;
  ac_power: boolean | null;
  battery_percent: number | null;
  battery_present: boolean | null;
  battery_saver: boolean | null;
  power_plan: string | null;
  power_mode: string | null;
  platform_profile: string | null;
  cpu_governor: string | null;
  low_power_mode: boolean | null;
  sources: string[];
};

export type PowerStateSnapshot = {
  start: PowerReading;
  end: PowerReading | null;
  changed_during_run: boolean | null;
  changed_fields: string[];
};

export type PromptCacheCheck = {
  code: "prompt_cache_likely" | null;
  affected_requests: number;
  message?: string;
  median_ttft_ms?: number | null;
  reasons?: string[];
  checked?: boolean;
};

export type RunConditionsSnapshot = {
  runtime_settings?: RuntimeSettingsSnapshot;
  model_identity?: ModelIdentity;
  power_state?: PowerStateSnapshot;
  prompt_cache?: PromptCacheCheck;
};

export type ConditionDifference = { field: string; label: string; values: unknown[] };

/** Extra fields carried by the run-condition dimensions of a RunComparison. */
export type ConditionDimension = RunComparison["dimensions"][number] & {
  differing_fields?: ConditionDifference[];
  unknown_fields?: string[];
  matched_fields?: string[];
  changed_during_run?: number[];
  digests?: Array<string | null>;
  summary?: string | null;
  resolved_by?: string;
  note?: string;
};

export type RunComparisonWithConditions = RunComparison & { confounders?: string[] };

export type RepeatSpreadMetric = ExperimentSummary["variants"][number]["metrics"][number] & {
  cv?: number | null;
  unstable?: boolean | null;
};

/** Creates a draft experiment group that runs one saved plan `count` times (2–10). */
export const repeatBenchmarkPlan = (planId: string, count: number) =>
  apiRequest<ExperimentCreateResponse>(
    `/api/v1/benchmark-plans/${encodeURIComponent(planId)}/repeat`,
    { method: "POST", body: JSON.stringify({ count }) },
  );

// --- Model suggestions and Ollama pulls (Models screen) ---
// Suggestions rank a curated catalog by memory fit and estimated speed; they are not benchmarks.
export type ModelUseCase = "chat" | "coding" | "document_qa" | "small_fast" | "reasoning";
export type ModelFit = "fits" | "tight" | "too_large";
export type ModelSpeedBasis = "measured" | "gpu_bandwidth_rough" | "unknown";
export type ModelDownloadedLocation = {
  source: "ollama" | "gguf";
  model_ref: string;
  engine_profile_id: string | null;
  engine_label: string | null;
  adapter_id: string;
};
export type ModelSuggestion = {
  rank: number;
  model_id: string;
  name: string;
  family: string;
  publisher: string;
  params_b: number;
  active_params_b: number | null;
  q4_size_gb: number;
  download_size_gb: number;
  context_tokens: number;
  license: string;
  strengths: string[];
  notes: string | null;
  fit: ModelFit;
  memory_required_gb: number;
  speed: {
    tokens_per_second: number | null;
    basis: ModelSpeedBasis;
    label: string;
    verdict: string | null;
  };
  score: number;
  reasons: string[];
  cautions: string[];
  downloaded: ModelDownloadedLocation[];
  runnable_with: string[];
  sources: {
    ollama_tag: string | null;
    gguf_repo: string | null;
    gguf_file: string | null;
    gguf_url: string | null;
    openvino_repo: string | null;
    onnx_repo: string | null;
  };
};
export type ModelPullTarget = {
  engine_profile_id: string;
  label: string;
  endpoint_url: string | null;
  enabled: boolean;
};
export type ModelSuggestionsResult = {
  generated_at: string;
  use_case: ModelUseCase;
  use_case_label: string;
  use_cases: ModelUseCase[];
  target_tokens_per_second: number;
  catalog_version: string;
  catalog_date: string;
  method: string;
  machine_summary: string;
  memory_basis: "gpu_memory" | "system_memory";
  memory_budget_bytes: number | null;
  speed_basis: ModelSpeedBasis;
  read_gib_per_second: number | null;
  ollama_reachable: boolean;
  pull_targets: ModelPullTarget[];
  suggestions: ModelSuggestion[];
};
export type ModelPull = {
  id: string;
  engine_profile_id: string;
  engine_label: string;
  ollama_tag: string;
  catalog_model_id: string | null;
  status: "running" | "succeeded" | "failed" | "cancelled";
  phase: string | null;
  bytes_completed: number | null;
  bytes_total: number | null;
  percent: number | null;
  error: string | null;
  probe_status: "not_started" | "running" | "succeeded" | "failed";
  probe_message: string | null;
  started_at: string;
  finished_at: string | null;
};

export const fetchModelSuggestions = (useCase: ModelUseCase) =>
  apiRequest<ModelSuggestionsResult>(
    `/api/v1/models/suggestions?use_case=${encodeURIComponent(useCase)}`,
  );
/** Asks a local, enabled Ollama profile to download a model; only after the user confirmed. */
export const pullModelWithOllama = (engineProfileId: string, ollamaTag: string) =>
  apiRequest<ModelPull>("/api/v1/models/pull", {
    method: "POST",
    body: JSON.stringify({
      engine_profile_id: engineProfileId,
      ollama_tag: ollamaTag,
      confirm: true,
    }),
  });
export const listModelPulls = () => apiRequest<ModelPull[]>("/api/v1/models/pulls");
export const getModelPull = (id: string) =>
  apiRequest<ModelPull>(`/api/v1/models/pulls/${encodeURIComponent(id)}`);
export const cancelModelPull = (id: string) =>
  apiRequest<ModelPull>(`/api/v1/models/pulls/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: "{}",
  });

// --- Hardware fit (Hardware fit screen) ---
// Detected hardware plus a per-model grade; the speeds are estimates, not benchmarks.
export type FitTier = "perfect" | "good" | "marginal" | "too_tight";
export type FitRunMode = "gpu" | "gpu_and_ram" | "cpu" | "none";
export type HardwareFitAccelerator = {
  name: string;
  vendor: string | null;
  memory_gb: number | null;
  kind: "discrete" | "integrated" | "unified" | "unknown";
  runtime: string | null;
  driver_version: string | null;
};
export type HardwareFitMachine = {
  summary: string;
  os: string | null;
  cpu_model: string | null;
  cpu_logical_cores: number | null;
  cpu_physical_cores: number | null;
  memory_total_gb: number | null;
  accelerators: HardwareFitAccelerator[];
  accelerators_detected: boolean;
  compute_path: "discrete_gpu" | "unified_memory" | "cpu_only";
  compute_path_label: string;
  gpu_budget_gb: number | null;
  ram_budget_gb: number | null;
  notes: string[];
};
export type HardwareFitModel = {
  model_id: string;
  name: string;
  family: string;
  publisher: string;
  params_b: number;
  active_params_b: number | null;
  context_tokens: number;
  license: string;
  strengths: string[];
  notes: string | null;
  quantization: string;
  quantization_bits: number;
  lower_quality: boolean;
  size_gb: number;
  memory_required_gb: number;
  run_mode: FitRunMode;
  tier: FitTier;
  tokens_per_second: number | null;
  speed_basis: ModelSpeedBasis;
  speed_label: string;
  score: number;
  explanation: string;
  downloaded: boolean;
  sources: ModelSuggestion["sources"];
};
export type HardwareFitResult = {
  generated_at: string;
  catalog_version: string;
  method: string;
  detected_by: string;
  machine: HardwareFitMachine;
  models: HardwareFitModel[];
};

export const fetchHardwareFit = (refresh = false) =>
  apiRequest<HardwareFitResult>(`/api/v1/hardware-fit${refresh ? "?refresh=true" : ""}`);

// --- Launch-settings tuning for managed llama.cpp (Engines screen) ---
export type TuneObjective = "throughput" | "latency" | "ttft";
export type TuneTrial = {
  settings: Record<string, unknown>;
  changed: string | null;
  score: number | null;
  tokens_per_second: number | null;
  ttft_ms: number | null;
  latency_ms: number | null;
  error: string | null;
};
export type TuneJob = {
  id: string;
  engine_profile_id: string;
  engine_label: string;
  objective: TuneObjective;
  objective_description: string;
  status: "running" | "completed" | "failed" | "cancelled";
  started_at: string;
  finished_at: string | null;
  total_trials: number;
  trials: TuneTrial[];
  min_gain_percent: number;
  baseline_score: number | null;
  best_score: number | null;
  improvement_percent: number | null;
  best_settings: Record<string, unknown>;
  recommended_launch_settings: Record<string, unknown> | null;
  error: string | null;
  stopping: boolean;
};

export const startTune = (engineProfileId: string, objective: TuneObjective) =>
  apiRequest<TuneJob>("/api/v1/tunes", {
    method: "POST",
    body: JSON.stringify({ engine_profile_id: engineProfileId, objective }),
  });
export const listTunes = () => apiRequest<TuneJob[]>("/api/v1/tunes");
export const getTune = (id: string) =>
  apiRequest<TuneJob>(`/api/v1/tunes/${encodeURIComponent(id)}`);
export const cancelTune = (id: string) =>
  apiRequest<TuneJob>(`/api/v1/tunes/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: "{}",
  });
