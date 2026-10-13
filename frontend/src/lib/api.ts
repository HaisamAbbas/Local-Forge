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
  status: string;
};

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

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    headers.set("X-LOCALFORGE-SESSION", await readSessionToken());
  }
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
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
export const listEngineModels = (id: string) =>
  apiRequest<EngineModel[]>(`/api/v1/engines/${id}/models`);

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
