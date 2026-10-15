import type { QualityChecks, RunTargets } from "./api";

/** Target inputs as typed text; time targets are edited in seconds and saved in milliseconds. */
export type TargetsForm = {
  ttftP50Seconds: string;
  ttftP95Seconds: string;
  latencyP50Seconds: string;
  generationTokensPerSecond: string;
  requestsPerSecond: string;
  errorRatePercent: string;
  qualityPassRatePercent: string;
};

export type QualityForm = {
  exactMatch: boolean;
  validJson: boolean;
  requiredKeys: string;
  stoppedProperly: boolean;
  maxCharacters: string;
  mustContain: string;
  mustNotContain: string;
};

export const EMPTY_TARGETS: TargetsForm = {
  ttftP50Seconds: "",
  ttftP95Seconds: "",
  latencyP50Seconds: "",
  generationTokensPerSecond: "",
  requestsPerSecond: "",
  errorRatePercent: "",
  qualityPassRatePercent: "",
};

export const EMPTY_QUALITY: QualityForm = {
  exactMatch: false,
  validJson: false,
  requiredKeys: "",
  stoppedProperly: false,
  maxCharacters: "",
  mustContain: "",
  mustNotContain: "",
};

/** Presets whose context is large enough that prompt processing dominates the first token. */
export const LARGE_CONTEXT_TOKENS = 2048;

function numberText(value: number | null, scale = 1): string {
  if (value === null) return "";
  const scaled = value / scale;
  return String(Number.isInteger(scaled) ? scaled : Number(scaled.toFixed(3)));
}

export function targetsToForm(targets: RunTargets | null): TargetsForm {
  if (!targets) return EMPTY_TARGETS;
  return {
    ttftP50Seconds: numberText(targets.ttft_p50_ms_max, 1000),
    ttftP95Seconds: numberText(targets.ttft_p95_ms_max, 1000),
    latencyP50Seconds: numberText(targets.latency_p50_ms_max, 1000),
    generationTokensPerSecond: numberText(targets.generation_tokens_per_second_min),
    requestsPerSecond: numberText(targets.request_throughput_min),
    errorRatePercent: numberText(targets.error_rate_percent_max),
    qualityPassRatePercent: numberText(targets.quality_pass_rate_min),
  };
}

type TargetField = {
  key: keyof TargetsForm;
  label: string;
  unit: string;
  help: string;
  percent?: boolean;
};

export const TARGET_FIELDS: TargetField[] = [
  {
    key: "ttftP50Seconds",
    label: "First token, typical (p50) at most",
    unit: "s",
    help: "Half of requests show their first word within this time.",
  },
  {
    key: "ttftP95Seconds",
    label: "First token, slow requests (p95) at most",
    unit: "s",
    help: "19 of 20 requests start within this time. Needs at least 20 requests to judge.",
  },
  {
    key: "latencyP50Seconds",
    label: "Full response, typical (p50) at most",
    unit: "s",
    help: "Time from sending a request to the last word of the answer.",
  },
  {
    key: "generationTokensPerSecond",
    label: "Generation speed at least",
    unit: "tokens/s",
    help: "How fast text streams once it starts. People read about 5–10 tokens/s.",
  },
  {
    key: "requestsPerSecond",
    label: "Completed requests at least",
    unit: "requests/s",
    help: "Total work finished per second across all parallel requests.",
  },
  {
    key: "errorRatePercent",
    label: "Error rate at most",
    unit: "%",
    help: "Requests that failed, timed out, or were cut off.",
    percent: true,
  },
  {
    key: "qualityPassRatePercent",
    label: "Quality pass rate at least",
    unit: "%",
    help: "Share of checked outputs that pass the quality checks below.",
    percent: true,
  },
];

/** Converts the editor to API targets. Returns field errors for invalid entries. */
export function formToTargets(form: TargetsForm): {
  targets: RunTargets | null;
  errors: Partial<Record<keyof TargetsForm, string>>;
} {
  const errors: Partial<Record<keyof TargetsForm, string>> = {};
  const read = (key: keyof TargetsForm, scale = 1, percent = false): number | null => {
    const text = form[key].trim();
    if (!text) return null;
    const value = Number(text);
    if (!Number.isFinite(value) || value < 0 || (!percent && value === 0)) {
      errors[key] = percent ? "Enter a percentage from 0 to 100." : "Enter a positive number.";
      return null;
    }
    if (percent && value > 100) {
      errors[key] = "Enter a percentage from 0 to 100.";
      return null;
    }
    return value * scale;
  };
  const targets: RunTargets = {
    ttft_p50_ms_max: read("ttftP50Seconds", 1000),
    ttft_p95_ms_max: read("ttftP95Seconds", 1000),
    latency_p50_ms_max: read("latencyP50Seconds", 1000),
    generation_tokens_per_second_min: read("generationTokensPerSecond"),
    request_throughput_min: read("requestsPerSecond"),
    error_rate_percent_max: read("errorRatePercent", 1, true),
    quality_pass_rate_min: read("qualityPassRatePercent", 1, true),
  };
  const any = Object.values(targets).some((value) => value !== null);
  return { targets: any ? targets : null, errors };
}

function splitList(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Converts the quality editor to API checks; null when nothing is enabled. */
export function formToQualityChecks(form: QualityForm): QualityChecks | null {
  const requiredKeys = form.validJson ? splitList(form.requiredKeys) : [];
  const maxCharacters = form.maxCharacters.trim() ? Number(form.maxCharacters) : null;
  const checks: QualityChecks = {
    exact_match: form.exactMatch,
    valid_json: form.validJson,
    json_required_keys: requiredKeys,
    stopped_properly: form.stoppedProperly,
    max_output_characters:
      maxCharacters !== null && Number.isInteger(maxCharacters) && maxCharacters > 0
        ? maxCharacters
        : null,
    must_contain: splitList(form.mustContain),
    must_not_contain: splitList(form.mustNotContain),
    case_sensitive: false,
  };
  const enabled =
    checks.exact_match ||
    checks.valid_json ||
    checks.stopped_properly ||
    checks.max_output_characters !== null ||
    checks.must_contain.length > 0 ||
    checks.must_not_contain.length > 0;
  return enabled ? checks : null;
}
