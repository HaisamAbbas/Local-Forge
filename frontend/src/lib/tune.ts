import type { TuneJob, TuneObjective, TuneTrial } from "./api";

export const OBJECTIVE_LABEL: Record<TuneObjective, string> = {
  throughput: "Generation speed",
  latency: "Time to finish a request",
  ttft: "Time to the first token",
};

const SETTING_LABEL: Record<string, string> = {
  threads: "CPU threads",
  gpu_layers: "GPU layers",
  batch_size: "Batch size",
};

export function settingsText(settings: Record<string, unknown>): string {
  const entries = Object.entries(settings);
  if (entries.length === 0) return "Current settings";
  return entries
    .map(([key, value]) => `${SETTING_LABEL[key] ?? key} ${value === 999 ? "all" : String(value)}`)
    .join(", ");
}

export function scoreText(job: Pick<TuneJob, "objective">, trial: TuneTrial): string {
  if (trial.score === null) return trial.error ?? "No result";
  const value = trial.score.toLocaleString(undefined, { maximumFractionDigits: 1 });
  return job.objective === "throughput" ? `${value} tokens/s` : `${value} ms`;
}

export const jobIsActive = (job: TuneJob) => job.status === "running";

/** The keys of the recommended settings that differ from what the profile already has. */
export function changedFromProfile(
  recommended: Record<string, unknown>,
  current: Record<string, unknown> | undefined,
): string[] {
  return Object.keys(recommended).filter((key) => recommended[key] !== (current ?? {})[key]);
}
