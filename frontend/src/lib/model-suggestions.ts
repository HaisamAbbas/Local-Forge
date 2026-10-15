import type { ModelFit, ModelPull, ModelSuggestion, ModelUseCase } from "./api";

export const USE_CASE_TABS: ReadonlyArray<{ id: ModelUseCase; label: string }> = [
  { id: "chat", label: "Chat" },
  { id: "coding", label: "Coding" },
  { id: "document_qa", label: "Document Q&A" },
  { id: "small_fast", label: "Small and fast" },
  { id: "reasoning", label: "Reasoning" },
];

export const FIT_LABEL: Record<ModelFit, string> = {
  fits: "Fits",
  tight: "Tight fit",
  too_large: "Too large",
};

export const STRENGTH_LABEL: Record<string, string> = {
  chat: "Chat",
  coding: "Coding",
  reasoning: "Reasoning",
  multilingual: "Multilingual",
  small_fast: "Small and fast",
};

/** How many suggestions show before "Show all". */
export const INITIAL_SUGGESTIONS = 6;

export function formatGb(value: number): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: value < 10 ? 1 : 0 })} GB`;
}

export function formatParams(item: ModelSuggestion): string {
  const total = `${item.params_b.toLocaleString(undefined, { maximumFractionDigits: 1 })}B`;
  return item.active_params_b ? `${total} (${item.active_params_b}B active)` : total;
}

export function formatContext(tokens: number): string {
  return tokens >= 1024 ? `${Math.round(tokens / 1024)}k context` : `${tokens} context`;
}

export function speedText(item: ModelSuggestion): string {
  const rate = item.speed.tokens_per_second;
  if (rate === null) return "Speed unknown";
  const shown = rate >= 3 ? Math.round(rate).toString() : rate.toFixed(1);
  return `~${shown} tok/s`;
}

/** Where the model is already downloaded through Ollama, for a Benchmark link. */
export function benchmarkProfileId(item: ModelSuggestion): string | null {
  const location = item.downloaded.find(
    (entry) => entry.source === "ollama" && entry.engine_profile_id,
  );
  return location?.engine_profile_id ?? null;
}

export function benchmarkHref(profileId: string): string {
  return `#workloads?engine=${encodeURIComponent(profileId)}`;
}

function formatBytes(value: number): string {
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)} GB`;
  if (value >= 1e6) return `${Math.round(value / 1e6)} MB`;
  return `${Math.round(value / 1e3)} KB`;
}

export function pullProgressText(pull: ModelPull): string {
  const parts: string[] = [];
  if (pull.percent !== null) parts.push(`${Math.floor(pull.percent)}%`);
  if (pull.bytes_completed !== null && pull.bytes_total !== null)
    parts.push(`${formatBytes(pull.bytes_completed)} of ${formatBytes(pull.bytes_total)}`);
  if (pull.phase) parts.push(pull.phase);
  return parts.join(" · ") || "Starting…";
}

export function pullIsActive(pull: ModelPull): boolean {
  return pull.status === "running" || pull.probe_status === "running";
}
