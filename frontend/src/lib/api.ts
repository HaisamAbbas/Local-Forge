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
  status: string;
  worker_id: string | null;
  potentially_contended: boolean;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type EngineProfileInput = {
  label: string;
  adapter_id: string;
  engine_display_name: string | null;
  integration_mode: EngineMode;
  endpoint_url: string | null;
  health_route?: string | null;
  models_route?: string | null;
  user_notes: string | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
    readonly host: string | null = null,
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
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
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
      // Keep the bounded status message if the API response is not JSON.
    }
    throw new ApiError(message, response.status, code, host);
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
export const listRuns = () => apiRequest<BenchmarkRun[]>("/api/v1/runs?limit=10");
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

export const deleteEngineProfile = (id: string) =>
  apiRequest<void>(`/api/v1/engines/${id}`, { method: "DELETE" });

export const saveCurrentMachineProfile = () =>
  apiRequest<MachineProfile>("/api/v1/machine-profiles/current", { method: "PUT", body: "{}" });
