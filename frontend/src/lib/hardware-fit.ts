import type { FitRunMode, FitTier, HardwareFitModel } from "./api";

export const TIER_LABEL: Record<FitTier, string> = {
  perfect: "Perfect fit",
  good: "Good fit",
  marginal: "Marginal",
  too_tight: "Too large",
};

export const RUN_MODE_LABEL: Record<FitRunMode, string> = {
  gpu: "GPU memory",
  gpu_and_ram: "GPU + system RAM",
  cpu: "CPU + system RAM",
  none: "Does not fit",
};

export type FitFilter = "runs_well" | "runs" | "downloaded" | "all";
export type FitSort = "best" | "largest" | "fastest" | "name";

export const FIT_FILTERS: { id: FitFilter; label: string }[] = [
  { id: "runs_well", label: "Runs well" },
  { id: "runs", label: "Runs at all" },
  { id: "downloaded", label: "Already downloaded" },
  { id: "all", label: "Everything" },
];

export const FIT_SORTS: { id: FitSort; label: string }[] = [
  { id: "best", label: "Best match" },
  { id: "largest", label: "Largest model" },
  { id: "fastest", label: "Fastest" },
  { id: "name", label: "Name" },
];

export const STRENGTH_FILTERS: { id: string; label: string }[] = [
  { id: "all", label: "Any use" },
  { id: "chat", label: "Chat" },
  { id: "coding", label: "Coding" },
  { id: "reasoning", label: "Reasoning" },
  { id: "multilingual", label: "Multilingual" },
  { id: "small_fast", label: "Small and fast" },
];

export function runsWell(model: HardwareFitModel): boolean {
  return model.tier === "perfect" || model.tier === "good";
}

export type FitControls = {
  filter: FitFilter;
  sort: FitSort;
  strength: string;
  search: string;
};

export function visibleModels(models: HardwareFitModel[], controls: FitControls) {
  const needle = controls.search.trim().toLowerCase();
  const kept = models.filter((model) => {
    if (controls.filter === "runs_well" && !runsWell(model)) return false;
    if (controls.filter === "runs" && model.tier === "too_tight") return false;
    if (controls.filter === "downloaded" && !model.downloaded) return false;
    if (controls.strength !== "all" && !model.strengths.includes(controls.strength)) return false;
    if (needle) {
      const haystack = `${model.name} ${model.family} ${model.publisher}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
  const tierOrder: Record<FitTier, number> = { perfect: 0, good: 1, marginal: 2, too_tight: 3 };
  const byName = (a: HardwareFitModel, b: HardwareFitModel) => a.name.localeCompare(b.name);
  const sorted = [...kept];
  if (controls.sort === "name") sorted.sort(byName);
  else if (controls.sort === "largest") {
    sorted.sort((a, b) =>
      tierOrder[a.tier] === 3 || tierOrder[b.tier] === 3
        ? tierOrder[a.tier] - tierOrder[b.tier] || b.params_b - a.params_b
        : b.params_b - a.params_b || byName(a, b),
    );
  } else if (controls.sort === "fastest") {
    sorted.sort(
      (a, b) =>
        (b.tokens_per_second ?? -1) - (a.tokens_per_second ?? -1) ||
        tierOrder[a.tier] - tierOrder[b.tier] ||
        byName(a, b),
    );
  } else {
    sorted.sort(
      (a, b) => tierOrder[a.tier] - tierOrder[b.tier] || b.score - a.score || byName(a, b),
    );
  }
  return sorted;
}

export function speedLabel(model: HardwareFitModel): string {
  const rate = model.tokens_per_second;
  if (model.run_mode === "none") return "n/a";
  if (rate === null) return "Unknown";
  return `~${rate >= 3 ? Math.round(rate) : rate.toFixed(1)} tok/s`;
}

export function pullCommand(model: HardwareFitModel): string | null {
  return model.sources.ollama_tag ? `ollama pull ${model.sources.ollama_tag}` : null;
}

export function formatGb(value: number): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: value < 10 ? 1 : 0 })} GB`;
}

export function formatParams(model: HardwareFitModel): string {
  const total = `${model.params_b.toLocaleString(undefined, { maximumFractionDigits: 1 })}B`;
  return model.active_params_b ? `${total} (${model.active_params_b}B active)` : total;
}
