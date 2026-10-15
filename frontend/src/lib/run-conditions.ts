import type {
  BenchmarkRun,
  ConditionDimension,
  ModelIdentity,
  PowerReading,
  RepeatSpreadMetric,
  RunComparison,
  RunConditionsSnapshot,
  RuntimeSettingField,
} from "./api";

/** Display order and plain names for engine runtime settings. */
export const RUNTIME_SETTING_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["compute_backends", "Compute backend"],
  ["threads", "CPU threads"],
  ["threads_batch", "Prompt-processing threads"],
  ["gpu_layers", "GPU layers"],
  ["gpu_offload_share", "Share of model on GPU"],
  ["context_size", "Context size"],
  ["parallel_slots", "Parallel slots"],
  ["batch_size", "Batch size"],
  ["ubatch_size", "Micro-batch size"],
  ["flash_attention", "Flash attention"],
  ["kv_cache_type", "KV cache type"],
  ["backend_devices", "Backend devices"],
];

const SOURCE_LABELS: Record<string, string> = {
  process_command_line: "engine command line",
  process_loaded_libraries: "libraries the engine has loaded",
  engine_profile_device: "engine profile device",
  managed_launch_arguments: "LOCALFORGE launch arguments",
  llama_cpp_props: "llama.cpp /props",
  llama_cpp_slots: "llama.cpp /slots",
  koboldcpp_version: "KoboldCpp /api/extra/version",
  koboldcpp_props: "KoboldCpp /props",
  koboldcpp_true_max_context_length: "KoboldCpp context route",
  ollama_api_show: "Ollama /api/show",
  ollama_api_ps: "Ollama /api/ps",
  vllm_v1_models: "vLLM /v1/models",
  sglang_get_server_info: "SGLang /get_server_info",
};

export function runConditions(run: BenchmarkRun | null | undefined): RunConditionsSnapshot {
  return (run?.profile_snapshot ?? {}) as RunConditionsSnapshot;
}

export function formatSettingValue(field: string, value: unknown): string {
  if (value === null || value === undefined) return "Not reported";
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (field === "gpu_offload_share" && typeof value === "number") {
    return `${Math.round(value * 100)}%`;
  }
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "None";
  return String(value);
}

export function formatSettingSource(entry: RuntimeSettingField | undefined): string {
  if (!entry || entry.value === null || entry.value === undefined) {
    return "No source reported it";
  }
  const source = entry.source ? (SOURCE_LABELS[entry.source] ?? entry.source) : "unknown source";
  return entry.observed === "after_run" ? `${source} (read after the run)` : source;
}

export function shortHash(sha256: string | null | undefined): string {
  return sha256 ? `${sha256.slice(0, 12)}…` : "Unverified";
}

export function describeIdentity(identity: ModelIdentity | undefined): string {
  if (!identity || identity.basis === "unknown" || !identity.sha256) {
    return identity?.note ?? "Weights were not verified for this run.";
  }
  return identity.basis === "ollama_digest"
    ? "SHA-256 from Ollama's weights blob name (the hash of the GGUF file)."
    : "SHA-256 of the model file the engine loaded.";
}

export function describePower(reading: PowerReading | null | undefined): string {
  if (!reading) return "Not captured";
  const parts: string[] = [];
  if (reading.ac_power === true) parts.push("Plugged in");
  else if (reading.ac_power === false) {
    parts.push(
      reading.battery_percent !== null ? `On battery (${reading.battery_percent}%)` : "On battery",
    );
  }
  if (reading.battery_saver) parts.push("battery saver on");
  if (reading.low_power_mode) parts.push("low power mode on");
  if (reading.power_mode) parts.push(reading.power_mode);
  if (reading.power_plan) parts.push(`${reading.power_plan} plan`);
  if (reading.platform_profile) parts.push(`${reading.platform_profile} profile`);
  if (reading.cpu_governor) parts.push(`${reading.cpu_governor} governor`);
  return parts.length ? parts.join(" · ") : "Not reported by this system";
}

export const CONDITION_DIMENSION_KEYS = ["runtime_settings", "model_weights", "power_state"];

export function conditionDimensions(comparison: RunComparison): ConditionDimension[] {
  return comparison.dimensions.filter((dimension) =>
    CONDITION_DIMENSION_KEYS.includes(dimension.key),
  ) as ConditionDimension[];
}

export function conditionStateLabel(dimension: ConditionDimension): string {
  const labels: Record<string, string> = {
    match: "Match",
    mismatch: "Mismatch",
    unknown: "Unknown",
    verified_identical: "Verified identical",
    different: "Different",
    unverified: "Unverified",
  };
  return labels[dimension.state] ?? dimension.state.replaceAll("_", " ");
}

export function conditionExplanation(dimension: ConditionDimension): string {
  if (dimension.key === "runtime_settings") {
    if (dimension.state === "mismatch") return `Differs in ${dimension.summary ?? "settings"}.`;
    if (dimension.state === "match") {
      return "Threads, GPU layers, context size, and parallel slots match.";
    }
    const unknown = dimension.unknown_fields ?? [];
    return unknown.length
      ? `Not reported for every run: ${unknown.map((field) => field.replaceAll("_", " ")).join(", ")}.`
      : "Settings were not captured for these runs.";
  }
  if (dimension.key === "model_weights") {
    if (dimension.state === "verified_identical") {
      return "Every run used byte-identical weights (same SHA-256).";
    }
    if (dimension.state === "different") return "The weights files differ (SHA-256 differs).";
    return "At least one run's weights could not be hashed, so identity is unproven.";
  }
  if (dimension.state === "mismatch") {
    return dimension.summary
      ? `Power differed: ${dimension.summary}.`
      : "Power state changed while a run was measuring.";
  }
  if (dimension.state === "match") return "Same power source and mode for every run.";
  return "Power state was not captured for every run.";
}

/** Key repeat metrics: TTFT p50, request latency p50, generation tokens per second. */
export const KEY_SPREAD_METRICS: ReadonlyArray<{
  name: string;
  aggregation: string;
  label: string;
}> = [
  { name: "ttft_ms", aggregation: "p50", label: "TTFT p50" },
  { name: "request_latency_ms", aggregation: "p50", label: "Latency p50" },
  {
    name: "generation_throughput_tokens_per_second",
    aggregation: "run_rate",
    label: "Generation tok/s",
  },
];

export function keySpreadMetrics(metrics: RepeatSpreadMetric[]): Array<{
  label: string;
  metric: RepeatSpreadMetric | undefined;
}> {
  return KEY_SPREAD_METRICS.map((item) => ({
    label: item.label,
    metric: metrics.find(
      (metric) => metric.metric_name === item.name && metric.aggregation === item.aggregation,
    ),
  }));
}

export function formatCv(cv: number | null | undefined): string {
  return cv === null || cv === undefined ? "CV n/a" : `CV ${(cv * 100).toFixed(1)}%`;
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "Unavailable"
    : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
