import type { BenchmarkPlan, MetricSample } from "./api";
import { quantileR7 } from "./chart-data";
import { findRunMetric, loadLevels } from "./run-measurements";

/**
 * Learning-layer content and pure helpers: the metric glossary, experiment guides, deep-link
 * parsing for `#learning?metric=…&run=…`, and evidence-based diagnosis rules.
 *
 * Diagnosis rules only describe what recorded values are *consistent with*. They never claim a
 * root cause, never fire without the metrics they need, and never treat a missing value as zero.
 */

// ---------------------------------------------------------------------------------------------
// Metric glossary
// ---------------------------------------------------------------------------------------------

export type GlossarySource = "client_measured" | "derived" | "engine_native" | "system_observed";

export type GlossaryCategory = "latency" | "throughput" | "reliability" | "engine" | "system";

export const GLOSSARY_CATEGORIES: ReadonlyArray<{ id: GlossaryCategory; label: string }> = [
  { id: "latency", label: "Latency" },
  { id: "throughput", label: "Throughput" },
  { id: "reliability", label: "Outcomes and errors" },
  { id: "engine", label: "Engine-reported (Ollama)" },
  { id: "system", label: "Machine resources" },
];

export const SOURCE_LABELS: Record<GlossarySource, { label: string; description: string }> = {
  client_measured: {
    label: "Client measured",
    description:
      "Timed by the LocalForge benchmark worker with a monotonic clock, from the caller's side of the connection.",
  },
  derived: {
    label: "Derived",
    description:
      "Calculated by LocalForge from stored request samples or engine-reported values using the recorded formula.",
  },
  engine_native: {
    label: "Engine native",
    description:
      "Reported by the inference engine itself. Definitions can differ between engines, so these are kept separate from client timing.",
  },
  system_observed: {
    label: "System observed",
    description:
      "Best-effort operating-system readings taken by the benchmark worker about once per second.",
  },
};

export type GlossaryEntry = {
  /** Persisted `metric_name`. */
  name: string;
  label: string;
  category: GlossaryCategory;
  unit: string;
  source: GlossarySource;
  /** How the persisted records summarize this metric. */
  aggregations: string;
  /** Plain-language explanation. */
  summary: string;
  /** Formula exactly as LocalForge computes it. */
  formula: string;
  /** What usually makes it go up or down. */
  movedBy: string[];
  caveats: string[];
  related: string[];
  /** Extra search terms (abbreviations, synonyms). */
  aliases: string[];
};

const PERCENTILE_CAVEAT =
  "Percentiles need enough samples: p50 needs 1, p90 needs 10, p95 needs 20 and p99 needs 100 measurement requests. Below that the percentile is unavailable (shown as not available, never as zero) and flagged insufficient_samples_for_stable_tail.";
const DISTRIBUTION = "mean, p50, p90, p95, p99 (R-7 linear interpolation)";
const WARMUP_CAVEAT =
  "Warmup requests are stored but excluded from the measurement summaries; concurrency sweeps record a separate summary per load level.";
const WINDOW_CAVEAT =
  "Unavailable when any measurement request lacks a monotonic dispatch or end offset, because the measurement window cannot be established.";

const entries: GlossaryEntry[] = [
  // Latency -----------------------------------------------------------------------------------
  {
    name: "request_latency_ms",
    label: "Request latency",
    category: "latency",
    unit: "ms",
    source: "client_measured",
    aggregations: DISTRIBUTION,
    summary:
      "How long one request took from the moment LocalForge sent it until the response finished (or ended in an error). This is the end-to-end wait a caller experiences.",
    formula:
      "terminal response offset - dispatch offset, per measurement request (monotonic clock); summaries are the mean and R-7 percentiles of those values",
    movedBy: [
      "Output length: each extra generated token adds roughly one TPOT",
      "Prompt length, which raises time to first token",
      "Concurrency and request rate, when requests share the engine",
      "Model size, quantization and whether the model runs on GPU or CPU",
      "Network distance for remote endpoints",
    ],
    caveats: [PERCENTILE_CAVEAT, WARMUP_CAVEAT],
    related: ["ttft_ms", "tpot_ms_per_token", "queue_time_ms", "ollama.total_duration"],
    aliases: ["e2e", "end-to-end", "total latency", "response time"],
  },
  {
    name: "ttft_ms",
    label: "Time to first token (TTFT)",
    category: "latency",
    unit: "ms",
    source: "client_measured",
    aggregations: DISTRIBUTION,
    summary:
      "How long a caller waits before any output appears. It covers sending the request, any waiting inside the engine, loading the model if it was not resident, and processing the whole prompt (prefill) before the first token is produced.",
    formula:
      "first non-empty streamed output event offset - dispatch offset, per measurement request",
    movedBy: [
      "Prompt length: longer prompts need more prefill work",
      "Cold model loads on the first request after start-up or eviction",
      "Other requests occupying the engine (concurrency)",
      "Prompt caching in the engine, which can shorten repeated prefixes",
    ],
    caveats: [
      "Only measurable when the response streams. Requests whose first output event was not observed have no TTFT and are flagged ttft_only_when_observed.",
      "It is measured at the first output event, which may contain more than one token.",
      PERCENTILE_CAVEAT,
    ],
    related: ["request_latency_ms", "ollama.prompt_eval_duration", "ollama.load_duration"],
    aliases: ["ttft", "first token", "prefill latency", "time to first byte"],
  },
  {
    name: "tpot_ms_per_token",
    label: "Time per output token (TPOT)",
    category: "latency",
    unit: "ms/token",
    source: "derived",
    aggregations: DISTRIBUTION,
    summary:
      "The average time between tokens after the first one appeared, per request. It describes generation (decode) speed as a caller sees it; lower is faster.",
    formula:
      "(completion offset - first-output offset) / (output tokens - 1), for successful requests with an exact output-token count of at least 2",
    movedBy: [
      "Model size and quantization (decode is usually limited by memory bandwidth)",
      "GPU versus CPU execution and how many layers are offloaded",
      "Concurrency: batching can raise per-request TPOT while raising total throughput",
      "Very long contexts, which make each decode step slightly more expensive",
    ],
    caveats: [
      "Estimated token counts do not qualify, so TPOT is unavailable when the engine does not report exact counts (flag unavailable_exact_token_timing_or_counts).",
      "It is an average over the request, not a per-token timestamp measurement.",
      PERCENTILE_CAVEAT,
    ],
    related: [
      "generation_throughput_tokens_per_second",
      "inter_token_latency_ms",
      "ollama.eval_rate_tokens_per_second",
    ],
    aliases: ["tpot", "decode latency", "per token", "time per token"],
  },
  {
    name: "inter_token_latency_ms",
    label: "Inter-token latency (ITL)",
    category: "latency",
    unit: "ms/token",
    source: "client_measured",
    aggregations: DISTRIBUTION,
    summary:
      "The gap between consecutive tokens, measured from individual token timestamps. It shows stutters and pauses that an average such as TPOT hides.",
    formula: "difference between consecutive token timestamps from a token-aware source",
    movedBy: [
      "The same factors as TPOT",
      "Scheduling pauses when the engine interleaves several requests",
    ],
    caveats: [
      "Ollama and other engines without per-token timestamps cannot provide it, so it is unavailable (flag unavailable_token_timestamps_not_provided). LocalForge never substitutes output-event counts for token counts.",
      "Use inter-event latency for a chunk-level view on those engines.",
      PERCENTILE_CAVEAT,
    ],
    related: ["inter_event_latency_ms", "tpot_ms_per_token"],
    aliases: ["itl", "token gap", "jitter"],
  },
  {
    name: "inter_event_latency_ms",
    label: "Gap between output events",
    category: "latency",
    unit: "ms",
    source: "client_measured",
    aggregations: DISTRIBUTION,
    summary:
      "The time between consecutive streamed output chunks. One chunk can carry one or several tokens, so this is chunk timing rather than per-token timing.",
    formula:
      "difference between consecutive non-empty streamed output event offsets, pooled over measurement requests",
    movedBy: [
      "Decode speed",
      "How the engine and transport group tokens into chunks",
      "Scheduling pauses under concurrency",
    ],
    caveats: [
      "Not comparable with inter-token latency or TPOT unless each event is known to carry exactly one token.",
      PERCENTILE_CAVEAT,
    ],
    related: ["inter_token_latency_ms", "tpot_ms_per_token"],
    aliases: ["chunk", "event gap", "stream"],
  },
  {
    name: "queue_time_ms",
    label: "Queue time",
    category: "latency",
    unit: "ms",
    source: "client_measured",
    aggregations: DISTRIBUTION,
    summary:
      "How long a request waited inside LocalForge between its scheduled arrival time and being sent. It is the local benchmark scheduler's wait, not the engine's internal queue.",
    formula: "max(0, dispatch time - scheduled arrival time), per measurement request",
    movedBy: [
      "Open-loop target request rates above what the engine completes",
      "All client slots being busy with slow requests",
    ],
    caveats: [
      "Closed-loop workloads dispatch immediately, so queue time stays near zero by design; waiting inside the engine shows up in TTFT instead.",
      "It does not include time a request spends queued inside the engine.",
      PERCENTILE_CAVEAT,
    ],
    related: ["ttft_ms", "request_throughput_requests_per_second"],
    aliases: ["waiting", "scheduler", "backlog", "queue"],
  },
  // Throughput ---------------------------------------------------------------------------------
  {
    name: "output_throughput_tokens_per_second",
    label: "Output throughput",
    category: "throughput",
    unit: "tokens/s",
    source: "derived",
    aggregations: "run_rate (one value per run or load level)",
    summary:
      "All generated tokens divided by the wall-clock measurement window. It is the system-wide rate, so it rises when the engine serves several requests at once.",
    formula: "sum of successful output tokens / monotonic measurement-window seconds",
    movedBy: [
      "Concurrency and batching",
      "Decode speed of the model on this hardware",
      "Idle gaps in the window, such as long prefill phases",
    ],
    caveats: [
      "Null with incomplete_token_count_coverage when any successful request lacks an output-token count.",
      "Estimated counts are labelled (includes_estimated_token_counts) and the token_count_source label shows exact, estimated or mixed provenance.",
      WINDOW_CAVEAT,
    ],
    related: [
      "generation_throughput_tokens_per_second",
      "request_throughput_requests_per_second",
      "ollama.eval_rate_tokens_per_second",
    ],
    aliases: ["tokens per second", "tok/s", "aggregate throughput"],
  },
  {
    name: "generation_throughput_tokens_per_second",
    label: "Generation throughput",
    category: "throughput",
    unit: "tokens/s",
    source: "derived",
    aggregations: "run_rate",
    summary:
      "Tokens produced per second while requests were actually generating, ignoring the wait for the first token. It is the per-stream decode speed a reader feels.",
    formula: "eligible output tokens / sum(completion offset - first-output offset)",
    movedBy: [
      "Model size, quantization and GPU offload",
      "Concurrency, which shares decode capacity between streams",
    ],
    caveats: [
      "Only requests with exact output-token counts and an observed first output are eligible.",
      "Distinct from output throughput, which divides by the whole measurement window.",
    ],
    related: ["tpot_ms_per_token", "output_throughput_tokens_per_second"],
    aliases: ["decode throughput", "decode speed", "stream speed"],
  },
  {
    name: "input_throughput_tokens_per_second",
    label: "Input throughput",
    category: "throughput",
    unit: "tokens/s",
    source: "derived",
    aggregations: "run_rate",
    summary:
      "How many prompt tokens the run pushed through per second of measurement window. It describes the workload rate, not how fast the engine processes prompts.",
    formula: "sum of known input tokens / monotonic measurement-window seconds",
    movedBy: ["Prompt length", "Request rate and concurrency"],
    caveats: [
      "This is not engine prefill throughput: the window also contains generation time.",
      "Null with incomplete_token_count_coverage when any attempted request lacks an input-token count.",
      WINDOW_CAVEAT,
    ],
    related: [
      "prompt_processing_throughput_tokens_per_second",
      "ollama.prompt_eval_rate_tokens_per_second",
    ],
    aliases: ["prompt tokens per second"],
  },
  {
    name: "prompt_processing_throughput_tokens_per_second",
    label: "Prompt processing throughput",
    category: "throughput",
    unit: "tokens/s",
    source: "derived",
    aggregations: "run_rate",
    summary:
      "How fast the engine reads the prompt (prefill), measured from a dedicated prefill interval. Prefill is usually much faster per token than generation because the whole prompt is processed in parallel.",
    formula: "input tokens / isolated prefill interval",
    movedBy: ["Compute throughput of the device", "Batch size and prompt length"],
    caveats: [
      "Currently always unavailable (flag unavailable_prefill_interval): no adapter exposes a prefill interval with a cross-engine definition. An engine-timed prefill rate is never substituted.",
      "For Ollama, see the engine-timed prompt evaluation rate instead.",
    ],
    related: ["ollama.prompt_eval_rate_tokens_per_second", "ttft_ms"],
    aliases: ["prefill", "prompt eval"],
  },
  {
    name: "request_throughput_requests_per_second",
    label: "Request throughput",
    category: "throughput",
    unit: "requests/s",
    source: "derived",
    aggregations: "run_rate",
    summary: "How many requests finished successfully per second of measurement window.",
    formula:
      "successful measurement requests / (last terminal completion offset - first dispatch offset)",
    movedBy: ["Concurrency and batching", "Output length", "Error rate"],
    caveats: [WINDOW_CAVEAT, "Failed requests do not count toward it."],
    related: ["output_throughput_tokens_per_second", "error_rate_percent"],
    aliases: ["rps", "requests per second", "qps"],
  },
  // Reliability ---------------------------------------------------------------------------------
  {
    name: "error_rate_percent",
    label: "Error rate",
    category: "reliability",
    unit: "%",
    source: "derived",
    aggregations: "run_rate",
    summary: "The share of measurement requests that did not finish successfully, for any reason.",
    formula: "100 * (failed + disconnected + timed out + cancelled + interrupted) / attempted",
    movedBy: [
      "Request timeouts that are too short for the output length",
      "Overload at high concurrency or request rate",
      "Prompts longer than the model's context window",
      "Engine crashes or restarts",
    ],
    caveats: [
      "Unavailable (unavailable_no_measurement_requests) when no measurement request was attempted.",
      "Look at the separate outcome counts to see which kind of failure happened.",
    ],
    related: ["requests_failed", "requests_timed_out", "requests_disconnected"],
    aliases: ["errors", "failures", "failure rate"],
  },
  ...(
    [
      [
        "requests_attempted",
        "Requests attempted",
        "Measurement requests that were dispatched.",
        "count of measurement request records",
      ],
      [
        "requests_succeeded",
        "Requests succeeded",
        "Measurement requests that completed successfully.",
        "count(outcome = succeeded)",
      ],
      [
        "requests_failed",
        "Requests failed",
        "Measurement requests the adapter reported as failed, for example an HTTP error from the engine.",
        "count(outcome = failed)",
      ],
      [
        "requests_timed_out",
        "Requests timed out",
        "Measurement requests that exceeded the plan's request timeout.",
        "count(outcome = timed_out)",
      ],
      [
        "requests_disconnected",
        "Requests disconnected",
        "Measurement requests whose response stream closed before completion.",
        "count(outcome = disconnected)",
      ],
      [
        "requests_cancelled",
        "Requests cancelled",
        "Measurement requests cancelled by the user or the adapter.",
        "count(outcome = cancelled)",
      ],
      [
        "requests_interrupted",
        "Requests interrupted",
        "Measurement requests left incomplete because the worker or application stopped.",
        "count(outcome = interrupted)",
      ],
    ] as const
  ).map(([name, label, summary, formula]): GlossaryEntry => ({
    name,
    label,
    category: "reliability",
    unit: "requests",
    source: "derived",
    aggregations: "run_total",
    summary,
    formula,
    movedBy:
      name === "requests_attempted" || name === "requests_succeeded"
        ? ["The workload's request count or duration", "Errors, for succeeded"]
        : [
            "Timeouts, overload, context-window limits and engine stability",
            "Cancelling a run part-way",
          ],
    caveats: [
      "Counts cover the measurement phase only; warmup requests are excluded.",
      "Outcome categories are counted separately and add up to the attempted total.",
    ],
    related: ["error_rate_percent", "requests_attempted"],
    aliases: ["outcome", "count", "totals"],
  })),
  // Engine native (Ollama) ---------------------------------------------------------------------
  {
    name: "ollama.total_duration",
    label: "Ollama total duration",
    category: "engine",
    unit: "ns (shown as ms)",
    source: "engine_native",
    aggregations: "raw per request, then mean and R-7 percentiles over measurement requests",
    summary:
      "Ollama's own timing of the whole request on the server: loading, prompt evaluation and generation. It excludes network and client streaming overhead.",
    formula: "reported by Ollama in the final response (total_duration, nanoseconds)",
    movedBy: ["Everything that moves load, prompt evaluation and eval duration"],
    caveats: [
      "Engine-timed, so it is not directly comparable with client-measured request latency; the gap between them approximates transport and client overhead.",
      PERCENTILE_CAVEAT,
    ],
    related: ["request_latency_ms", "ollama.load_duration", "ollama.eval_duration"],
    aliases: ["server time", "total_duration"],
  },
  {
    name: "ollama.load_duration",
    label: "Ollama model load duration",
    category: "engine",
    unit: "ns (shown as ms)",
    source: "engine_native",
    aggregations: "raw per request, then mean and R-7 percentiles over measurement requests",
    summary:
      "Time Ollama spent loading the model before it could answer. It is large on a cold start and small once the model is resident in memory.",
    formula: "reported by Ollama in the final response (load_duration, nanoseconds)",
    movedBy: [
      "Whether the model was already loaded (cold versus warm)",
      "Ollama's keep-alive setting and memory pressure, which can evict the model",
      "Disk speed and model file size",
    ],
    caveats: [
      "A cold load inflates TTFT and latency for the first request; use warmup requests to keep it out of the measurement summaries.",
      PERCENTILE_CAVEAT,
    ],
    related: ["ttft_ms", "ollama.total_duration"],
    aliases: ["cold start", "model load", "load_duration"],
  },
  {
    name: "ollama.prompt_eval_duration",
    label: "Ollama prompt evaluation duration",
    category: "engine",
    unit: "ns (shown as ms)",
    source: "engine_native",
    aggregations: "raw per request, then mean and R-7 percentiles over measurement requests",
    summary:
      "Time Ollama spent processing the prompt (prefill) before generating. It is the engine-side counterpart of most of the time to first token.",
    formula: "reported by Ollama in the final response (prompt_eval_duration, nanoseconds)",
    movedBy: ["Prompt length", "Prompt caching of a repeated prefix", "Device compute speed"],
    caveats: [
      "When Ollama reuses a cached prompt prefix, it can report fewer evaluated prompt tokens and a shorter duration.",
      PERCENTILE_CAVEAT,
    ],
    related: ["ttft_ms", "ollama.prompt_eval_count", "ollama.prompt_eval_rate_tokens_per_second"],
    aliases: ["prefill", "prompt_eval_duration"],
  },
  {
    name: "ollama.eval_duration",
    label: "Ollama eval (generation) duration",
    category: "engine",
    unit: "ns (shown as ms)",
    source: "engine_native",
    aggregations: "raw per request, then mean and R-7 percentiles over measurement requests",
    summary: "Time Ollama spent generating output tokens (decode).",
    formula: "reported by Ollama in the final response (eval_duration, nanoseconds)",
    movedBy: ["Output length", "Model size, quantization and GPU offload"],
    caveats: [PERCENTILE_CAVEAT],
    related: ["ollama.eval_count", "ollama.eval_rate_tokens_per_second", "tpot_ms_per_token"],
    aliases: ["decode", "eval_duration", "generation time"],
  },
  {
    name: "ollama.prompt_eval_count",
    label: "Ollama prompt tokens evaluated",
    category: "engine",
    unit: "tokens",
    source: "engine_native",
    aggregations: "raw per request, then mean and R-7 percentiles over measurement requests",
    summary: "How many prompt tokens Ollama says it evaluated for the request.",
    formula: "reported by Ollama in the final response (prompt_eval_count)",
    movedBy: ["Prompt length and chat template", "Prompt-prefix caching"],
    caveats: [
      "Can be lower than the prompt's full length when a cached prefix was reused.",
      "Zero is a valid observation; a missing count is stored as unavailable.",
    ],
    related: ["ollama.prompt_eval_duration", "ollama.prompt_eval_rate_tokens_per_second"],
    aliases: ["input tokens", "prompt_eval_count"],
  },
  {
    name: "ollama.eval_count",
    label: "Ollama tokens generated",
    category: "engine",
    unit: "tokens",
    source: "engine_native",
    aggregations: "raw per request, then mean and R-7 percentiles over measurement requests",
    summary: "How many output tokens Ollama says it generated for the request.",
    formula: "reported by Ollama in the final response (eval_count)",
    movedBy: ["Maximum output tokens", "Stop sequences and the model's natural answer length"],
    caveats: ["Zero is a valid observation; a missing count is stored as unavailable."],
    related: ["ollama.eval_duration", "ollama.eval_rate_tokens_per_second"],
    aliases: ["output tokens", "eval_count"],
  },
  {
    name: "ollama.prompt_eval_rate_tokens_per_second",
    label: "Prompt evaluation rate (engine-timed)",
    category: "engine",
    unit: "tokens/s",
    source: "derived",
    aggregations: "run_rate",
    summary:
      "Ollama's prefill speed: prompt tokens per second of Ollama-reported prompt evaluation time.",
    formula:
      "sum(ollama.prompt_eval_count) / (sum(ollama.prompt_eval_duration ns) / 1e9) over measurement requests that reported both",
    movedBy: ["Device compute speed", "Model size and quantization", "Prompt caching"],
    caveats: [
      "Engine-timed: excludes client, network and streaming overhead, so it is reported separately from client-timed throughput.",
      "Null with incomplete_token_count_coverage when a request reported a duration but no count, and with unavailable_zero_engine_duration when the summed duration is zero.",
      "Older runs may fall back to engine-exact usage counts (flag token_counts_from_engine_usage_record).",
    ],
    related: ["ollama.prompt_eval_duration", "prompt_processing_throughput_tokens_per_second"],
    aliases: ["prefill rate", "prompt tokens per second"],
  },
  {
    name: "ollama.eval_rate_tokens_per_second",
    label: "Generation rate (engine-timed)",
    category: "engine",
    unit: "tokens/s",
    source: "derived",
    aggregations: "run_rate",
    summary:
      "Ollama's decode speed: generated tokens per second of Ollama-reported eval time. It is usually a little higher than client-measured generation throughput.",
    formula:
      "sum(ollama.eval_count) / (sum(ollama.eval_duration ns) / 1e9) over measurement requests that reported both",
    movedBy: ["Model size and quantization", "GPU offload", "Concurrency"],
    caveats: [
      "Engine-timed: excludes client, network and streaming overhead, so it is reported separately from client-timed throughput.",
      "Null with incomplete_token_count_coverage or unavailable_zero_engine_duration as for the prompt evaluation rate.",
    ],
    related: [
      "ollama.eval_duration",
      "generation_throughput_tokens_per_second",
      "tpot_ms_per_token",
    ],
    aliases: ["decode rate", "eval rate", "tokens per second"],
  },
  // System -----------------------------------------------------------------------------------
  {
    name: "host_cpu_percent",
    label: "Host CPU utilization",
    category: "system",
    unit: "%",
    source: "system_observed",
    aggregations: "sample (about one per second while the worker runs)",
    summary:
      "How busy all CPU cores of the machine running LocalForge were, as a percentage of total capacity.",
    formula: "psutil.cpu_percent() over the interval since the previous host sample",
    movedBy: [
      "An engine running on this machine's CPU",
      "Other applications",
      "LocalForge's own worker at high request rates",
    ],
    caveats: [
      "Describes the LocalForge host only (scope=local_host): not a remote engine host, and not GPU utilization.",
      "Sampling failures are stored as unavailable, never as zero.",
    ],
    related: ["worker_process_cpu_percent", "host_memory_used_percent"],
    aliases: ["cpu", "processor", "utilization"],
  },
  {
    name: "host_memory_used_percent",
    label: "Host memory used",
    category: "system",
    unit: "%",
    source: "system_observed",
    aggregations: "sample (point-in-time readings)",
    summary: "The share of the machine's RAM in use at each sample.",
    formula: "psutil.virtual_memory().percent",
    movedBy: [
      "Loading a model into system RAM (CPU or partially offloaded runs)",
      "Long contexts, which enlarge the KV cache",
      "Other applications",
    ],
    caveats: [
      "Point-in-time readings: short spikes between samples are not captured.",
      "Does not include GPU memory (VRAM).",
    ],
    related: ["host_memory_available_bytes", "worker_process_rss_bytes"],
    aliases: ["ram", "memory"],
  },
  {
    name: "host_memory_available_bytes",
    label: "Host memory available",
    category: "system",
    unit: "bytes",
    source: "system_observed",
    aggregations: "sample (point-in-time readings)",
    summary:
      "How much RAM the operating system could still hand out without swapping, at each sample.",
    formula: "psutil.virtual_memory().available",
    movedBy: ["The same factors as host memory used"],
    caveats: ["Point-in-time readings.", "Does not include GPU memory (VRAM)."],
    related: ["host_memory_used_percent"],
    aliases: ["free memory", "ram"],
  },
  {
    name: "worker_process_cpu_percent",
    label: "Benchmark worker CPU",
    category: "system",
    unit: "% of one logical CPU",
    source: "system_observed",
    aggregations: "sample (about one per second while the worker runs)",
    summary:
      "How much CPU LocalForge's own isolated benchmark process used. A high value means the load generator itself was busy.",
    formula: "psutil.Process.cpu_percent() over the interval since the previous sample",
    movedBy: ["Request rate and concurrency", "Streaming many small output events"],
    caveats: [
      "Expressed per logical CPU, so it can exceed 100% when the worker uses several cores.",
      "Measures LocalForge's worker (scope=benchmark_worker), not the engine process.",
    ],
    related: ["host_cpu_percent"],
    aliases: ["client cpu", "load generator"],
  },
  {
    name: "worker_process_rss_bytes",
    label: "Benchmark worker memory",
    category: "system",
    unit: "bytes",
    source: "system_observed",
    aggregations: "sample (point-in-time readings)",
    summary: "Resident memory (RSS) of LocalForge's isolated benchmark process.",
    formula: "psutil.Process.memory_info().rss",
    movedBy: ["Retaining prompt and output text", "Very large datasets"],
    caveats: ["Measures LocalForge's worker only, not the engine process."],
    related: ["host_memory_used_percent"],
    aliases: ["rss", "client memory"],
  },
];

/** Every metric LocalForge records, keyed by persisted `metric_name`. */
export const METRIC_GLOSSARY: Readonly<Record<string, GlossaryEntry>> = Object.freeze(
  Object.fromEntries(entries.map((entry) => [entry.name, entry])),
);

export const GLOSSARY_ORDER: readonly string[] = entries.map((entry) => entry.name);

export function glossaryEntry(name: string): GlossaryEntry | null {
  return Object.prototype.hasOwnProperty.call(METRIC_GLOSSARY, name)
    ? (METRIC_GLOSSARY[name] ?? null)
    : null;
}

/** Glossary entries matching a free-text query and optional category, in catalog order. */
export function searchGlossary(
  query: string,
  category: GlossaryCategory | "all" = "all",
): GlossaryEntry[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return entries.filter((entry) => {
    if (category !== "all" && entry.category !== category) return false;
    if (terms.length === 0) return true;
    const haystack = [
      entry.name,
      entry.label,
      entry.summary,
      entry.unit,
      SOURCE_LABELS[entry.source].label,
      ...entry.aliases,
    ]
      .join(" ")
      .toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

// ---------------------------------------------------------------------------------------------
// Deep links
// ---------------------------------------------------------------------------------------------

const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export type LearningLocation = { metric: string | null; run: string | null };

/**
 * Read `#learning?metric=ttft_ms&run=<uuid>`. Unknown metric names and malformed run IDs are
 * ignored, as is any hash that is not on the learning screen.
 */
export function parseLearningHash(hash: string): LearningLocation {
  const [screen, query = ""] = hash.replace(/^#/, "").split("?", 2);
  if (screen !== "learning") return { metric: null, run: null };
  const params = new URLSearchParams(query);
  const metric = params.get("metric");
  const run = params.get("run");
  return {
    metric: metric && glossaryEntry(metric) ? metric : null,
    run: run && RUN_ID_PATTERN.test(run) ? run : null,
  };
}

export function learningHref(location: Partial<LearningLocation> = {}): string {
  const params = new URLSearchParams();
  if (location.metric) params.set("metric", location.metric);
  if (location.run) params.set("run", location.run);
  const query = params.toString();
  return query ? `#learning?${query}` : "#learning";
}

/** Link to a metric's glossary entry, for use from any screen. */
export function glossaryHref(metricName: string): string {
  return learningHref({ metric: metricName });
}

/** Element id of a glossary entry on the learning screen. */
export function glossaryAnchorId(metricName: string): string {
  return `metric-${metricName.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

// ---------------------------------------------------------------------------------------------
// Experiment guides
// ---------------------------------------------------------------------------------------------

export type ExperimentGuide = {
  id: string;
  kind: "preset" | "classic";
  /** Workload preset id (backend WorkloadPreset) when the guide maps to a preset. */
  presetId: string | null;
  title: string;
  shape: string;
  teaches: string;
  hypothesis: string;
  changeOneAtATime: string[];
  watch: string[];
  tip: string;
};

export const EXPERIMENT_GUIDES: readonly ExperimentGuide[] = [
  {
    id: "fixed_prefill",
    kind: "preset",
    presetId: "fixed_prefill",
    title: "Fixed prompt prefill",
    shape: "Completion · about 1,024 input tokens · 32 output tokens · closed loop",
    teaches:
      "How much of a request's cost is reading the prompt. With a long prompt and a tiny answer, time to first token is mostly prefill work.",
    hypothesis:
      "If I double the prompt length, TTFT will grow by about ___ while TPOT stays about the same, because ___.",
    changeOneAtATime: [
      "Target input tokens: 512, then 1,024, then 2,048",
      "Model quantization, with the same model family",
      "Warmup count: 0 versus 2, to see a cold load",
    ],
    watch: [
      "ttft_ms",
      "ollama.prompt_eval_duration",
      "ollama.prompt_eval_rate_tokens_per_second",
      "ollama.load_duration",
    ],
    tip: "Keep the output budget tiny so generation time does not hide the prefill signal.",
  },
  {
    id: "fixed_decode",
    kind: "preset",
    presetId: "fixed_decode",
    title: "Fixed output decode",
    shape: "Completion · about 128 input tokens · output up to the max-output budget · closed loop",
    teaches:
      "Generation (decode) speed. A short prompt and a long answer isolate the per-token cost, which is usually limited by memory bandwidth rather than raw compute.",
    hypothesis:
      "If I switch to a larger model, TPOT will rise by roughly ___× because each token must read ___ more weights.",
    changeOneAtATime: [
      "Maximum output tokens: 128, then 512, then 1,024",
      "Model size or quantization",
      "GPU offload settings in the engine",
    ],
    watch: [
      "tpot_ms_per_token",
      "generation_throughput_tokens_per_second",
      "ollama.eval_rate_tokens_per_second",
      "inter_event_latency_ms",
    ],
    tip: "TPOT needs exact output-token counts; engines that only allow estimates leave it unavailable.",
  },
  {
    id: "mixed_chat",
    kind: "preset",
    presetId: "mixed_chat",
    title: "Short chat interaction",
    shape: "Chat · about 512 input tokens · 128 output tokens · closed loop",
    teaches:
      "What an interactive chat feels like: how long until text appears (TTFT) versus how long until the answer is complete (request latency).",
    hypothesis:
      "For this model, about ___% of the total wait will be before the first token appears.",
    changeOneAtATime: [
      "The model, keeping the workload fixed",
      "Concurrency 1 versus 2, to see the effect of a second user",
      "Streaming on versus off, if the engine supports both",
    ],
    watch: ["ttft_ms", "request_latency_ms", "tpot_ms_per_token", "error_rate_percent"],
    tip: "A good baseline workload: save it and repeat it whenever you change one setting.",
  },
  {
    id: "concurrency_sweep",
    kind: "preset",
    presetId: "concurrency_sweep",
    title: "Concurrency sweep",
    shape: "Chat · about 512 input / 128 output tokens · concurrency levels 1, 2, 4, 8",
    teaches:
      "The throughput-versus-latency trade-off. Engines that batch requests raise total throughput as concurrency grows, at the cost of per-request latency; engines that serialize requests only add waiting.",
    hypothesis:
      "Total output throughput will keep rising until concurrency ___, after which latency grows without more throughput.",
    changeOneAtATime: [
      "The concurrency levels (for example 1, 2, 4, 8, 16)",
      "Engine parallelism settings, such as Ollama's parallel request limit",
      "Output length, which changes how long each slot stays busy",
    ],
    watch: [
      "output_throughput_tokens_per_second",
      "request_throughput_requests_per_second",
      "ttft_ms",
      "request_latency_ms",
      "error_rate_percent",
    ],
    tip: "Each level is summarized separately, so compare levels rather than one blended number.",
  },
  {
    id: "target_request_rate",
    kind: "preset",
    presetId: "target_request_rate",
    title: "Target request rate",
    shape: "Chat · about 512 input / 128 output tokens · open loop at a fixed arrival rate",
    teaches:
      "Queueing. Requests arrive on a schedule whether or not earlier ones finished, so once arrivals outpace completions, waiting time and tail latency climb quickly.",
    hypothesis:
      "Below ___ requests per second latency will stay flat; above it queue time and p95 latency will grow rapidly.",
    changeOneAtATime: [
      "Arrival rate, in small steps (for example 0.5, 1, 2 requests/s)",
      "Output length, which changes how long each request occupies the engine",
    ],
    watch: [
      "queue_time_ms",
      "request_latency_ms",
      "request_throughput_requests_per_second",
      "error_rate_percent",
      "worker_process_cpu_percent",
    ],
    tip: "If queue time grows while the worker CPU is high, the load generator itself may be falling behind.",
  },
  {
    id: "long_context",
    kind: "preset",
    presetId: "long_context",
    title: "Long context",
    shape: "Completion · about 8,192 input tokens · 128 output tokens · closed loop",
    teaches:
      "The cost of long prompts: much longer prefill, a larger KV cache in memory, and slightly slower decode as attention covers more tokens. It can also exceed the model's context window.",
    hypothesis:
      "Compared with a 512-token prompt, TTFT will grow about ___× and TPOT by about ___%.",
    changeOneAtATime: [
      "Target input tokens: 2,048, 4,096, 8,192",
      "The engine's context-window setting",
      "Model quantization, to see the memory effect",
    ],
    watch: [
      "ttft_ms",
      "ollama.prompt_eval_duration",
      "tpot_ms_per_token",
      "host_memory_used_percent",
      "error_rate_percent",
    ],
    tip: "If requests fail, check whether the prompt exceeds the context window configured in the engine.",
  },
  {
    id: "classic_concurrency",
    kind: "classic",
    presetId: "concurrency_sweep",
    title: "Throughput versus latency curve",
    shape: "Concurrency sweep with the same model and prompt set",
    teaches:
      "Where the knee of the curve is: the concurrency beyond which more parallel users only add waiting.",
    hypothesis:
      "Doubling concurrency from 1 to 2 will raise output throughput by ___× and p50 TTFT by ___×.",
    changeOneAtATime: ["Only the concurrency level; keep model, prompts and output budget fixed"],
    watch: [
      "output_throughput_tokens_per_second",
      "ttft_ms",
      "request_latency_ms",
      "queue_time_ms",
    ],
    tip: "Repeat each sweep at least twice; noisy tails need 20 or more requests per level for p95.",
  },
  {
    id: "classic_output_length",
    kind: "classic",
    presetId: "fixed_decode",
    title: "Output length versus TPOT",
    shape: "Fixed output decode with max output tokens 64, 256, 1,024",
    teaches:
      "That per-token speed is roughly constant while total latency grows linearly with output length, so long answers cost time, not speed.",
    hypothesis:
      "TPOT will stay within ___% across output lengths while request latency grows about ___× from 64 to 1,024 tokens.",
    changeOneAtATime: ["Only the maximum output tokens"],
    watch: ["tpot_ms_per_token", "request_latency_ms", "generation_throughput_tokens_per_second"],
    tip: "Models may stop early; compare ollama.eval_count to confirm how many tokens were really generated.",
  },
  {
    id: "classic_prompt_length",
    kind: "classic",
    presetId: "fixed_prefill",
    title: "Prompt length versus TTFT",
    shape: "Fixed prompt prefill with input tokens 256, 1,024, 4,096",
    teaches:
      "How prefill scales with prompt length, and how fast this device reads prompts compared with how fast it writes answers.",
    hypothesis:
      "TTFT will grow about linearly with prompt length, at roughly ___ ms per 1,000 prompt tokens.",
    changeOneAtATime: ["Only the target input tokens"],
    watch: ["ttft_ms", "ollama.prompt_eval_duration", "ollama.prompt_eval_rate_tokens_per_second"],
    tip: "Repeated identical prompts may hit a prompt cache; a dataset with varied prompts avoids that.",
  },
  {
    id: "classic_model_size",
    kind: "classic",
    presetId: "mixed_chat",
    title: "Model size versus speed",
    shape:
      "Short chat interaction run against two models (for example a 1B and an 8B, or Q4 and Q8)",
    teaches:
      "How parameter count and quantization trade speed and memory: bigger or less-quantized models read more bytes per token, so decode slows and memory use grows.",
    hypothesis:
      "The larger model will generate about ___× slower and use about ___ GiB more memory.",
    changeOneAtATime: [
      "Only the model; keep engine, workload and machine the same",
      "Then compare the two runs in Experiments to check the comparability grade",
    ],
    watch: [
      "ollama.eval_rate_tokens_per_second",
      "tpot_ms_per_token",
      "ttft_ms",
      "host_memory_used_percent",
      "ollama.load_duration",
    ],
    tip: "Use warmup requests so the second model's cold load does not distort the comparison.",
  },
];

// ---------------------------------------------------------------------------------------------
// Diagnosis
// ---------------------------------------------------------------------------------------------

export type DiagnosisStatus = "observed" | "not_observed" | "insufficient_data";

export type DiagnosisEvidence = {
  metric: string;
  label: string;
  value: number;
  unit: string;
  /** Sample count behind the value, when known. */
  sampleCount: number | null;
};

export type DiagnosisFinding = {
  id: string;
  title: string;
  status: DiagnosisStatus;
  /** "attention" observations are worth investigating; "info" ones are descriptive. */
  severity: "attention" | "info";
  statement: string;
  evidence: DiagnosisEvidence[];
  /** Glossary entries that explain the evidence. */
  metrics: string[];
  /** Suggested next experiment, phrased as something to test. */
  nextStep: string | null;
};

export type DiagnosisContext = {
  concurrency: number | null;
  trafficMode: "closed_loop" | "open_loop" | null;
};

/** Run context from a benchmark plan and its workload snapshot. */
export function diagnosisContextFromPlan(plan: BenchmarkPlan | null | undefined): DiagnosisContext {
  if (!plan) return { concurrency: null, trafficMode: null };
  const workload = plan.configuration_snapshot?.workload;
  const traffic =
    workload && typeof workload === "object" && "traffic_mode" in workload
      ? (workload as { traffic_mode?: unknown }).traffic_mode
      : null;
  return {
    concurrency: Number.isFinite(plan.concurrency) ? plan.concurrency : null,
    trafficMode: traffic === "open_loop" || traffic === "closed_loop" ? traffic : null,
  };
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

type Found = { metric: MetricSample; value: number };

function runValue(metrics: MetricSample[], name: string, aggregation: string, level: string) {
  const metric = findRunMetric(metrics, name, aggregation, level);
  return metric && finite(metric.value) ? ({ metric, value: metric.value } as Found) : null;
}

function levelSuffix(level: string): string {
  return level ? ` at concurrency ${level}` : "";
}

function evidence(found: Found, label: string, level = ""): DiagnosisEvidence {
  return {
    metric: found.metric.metric_name,
    label: `${label}${levelSuffix(level)}`,
    value: found.value,
    unit: found.metric.unit,
    sampleCount: found.metric.sample_count,
  };
}

function levelsOf(metrics: MetricSample[]): string[] {
  const levels = loadLevels(metrics);
  return levels.length ? levels : [""];
}

function insufficient(
  id: string,
  title: string,
  statement: string,
  metrics: string[],
): DiagnosisFinding {
  return {
    id,
    title,
    status: "insufficient_data",
    severity: "info",
    statement,
    evidence: [],
    metrics,
    nextStep: null,
  };
}

function percent(value: number): string {
  return `${Math.round(value)}%`;
}

function ratioText(value: number): string {
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)}×`;
}

/** Plain number formatting with precision that scales with magnitude. */
export function formatNumber(value: number): string {
  const magnitude = Math.abs(value);
  const digits = magnitude >= 100 ? 0 : magnitude >= 10 ? 1 : 2;
  return value.toLocaleString("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  });
}

/** A value with its unit, scaling nanoseconds to ms and bytes to MiB/GiB. */
export function formatMetricValue(value: number, unit: string): string {
  if (unit === "ns") return `${formatNumber(value / 1_000_000)} ms`;
  if (unit === "bytes") {
    if (Math.abs(value) >= 1024 ** 3) return `${formatNumber(value / 1024 ** 3)} GiB`;
    if (Math.abs(value) >= 1024 ** 2) return `${formatNumber(value / 1024 ** 2)} MiB`;
    return `${formatNumber(value)} bytes`;
  }
  if (unit === "%") return `${formatNumber(value)}%`;
  return `${formatNumber(value)} ${unit}`;
}

function ms(value: number): string {
  return `${formatNumber(value)} ms`;
}

function ttftShareRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "ttft_share";
  const title = "Share of latency before the first token";
  const names = ["ttft_ms", "request_latency_ms"];
  let best: { share: number; ttft: Found; latency: Found; level: string } | null = null;
  for (const level of levelsOf(metrics)) {
    const ttft = runValue(metrics, "ttft_ms", "mean", level);
    const latency = runValue(metrics, "request_latency_ms", "mean", level);
    if (!ttft || !latency || latency.value <= 0) continue;
    const share = ttft.value / latency.value;
    if (!best || share > best.share) best = { share, ttft, latency, level };
  }
  if (!best) {
    return insufficient(
      id,
      title,
      "Not enough data: this needs both a mean TTFT and a mean request latency. TTFT is only available when responses stream.",
      names,
    );
  }
  const where = levelSuffix(best.level);
  const shared = {
    id,
    title,
    evidence: [
      evidence(best.ttft, "Mean TTFT", best.level),
      evidence(best.latency, "Mean request latency", best.level),
    ],
    metrics: [...names, "ollama.prompt_eval_duration", "ollama.load_duration"],
  };
  if (best.share >= 0.5) {
    return {
      ...shared,
      status: "observed",
      severity: "attention",
      statement: `Time to first token is about ${percent(best.share * 100)} of mean request latency${where}. This is consistent with prompt processing (prefill), model loading, or waiting inside the engine dominating each request, rather than token generation.`,
      nextStep:
        "Re-run with a shorter prompt, or with warmup requests, and check whether TTFT falls while TPOT stays the same.",
    };
  }
  return {
    ...shared,
    status: "not_observed",
    severity: "info",
    statement: `Time to first token is about ${percent(best.share * 100)} of mean request latency${where}, so most of each request is spent after output starts. This is consistent with generation (decode) dominating.`,
    nextStep: null,
  };
}

type RawEngineValue = { value: number; unit: string; phase: string | null; observedAt: string };

function rawEngineValues(metrics: MetricSample[], name: string): RawEngineValue[] {
  return metrics
    .filter(
      (metric) =>
        metric.metric_name === name &&
        metric.source === "engine_native" &&
        metric.labels.request_id !== undefined &&
        finite(metric.value),
    )
    .map((metric) => ({
      value: metric.value as number,
      unit: metric.unit,
      phase: metric.labels.phase ?? null,
      observedAt: metric.observed_at,
    }))
    .sort((left, right) => left.observedAt.localeCompare(right.observedAt));
}

function toMs(value: number, unit: string): number {
  if (unit === "ns") return value / 1_000_000;
  if (unit === "s") return value * 1000;
  return value;
}

/** Thresholds for the cold-load rule, in milliseconds. */
export const COLD_LOAD_MIN_MS = 500;
export const COLD_LOAD_RATIO = 5;

function coldLoadRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "cold_load";
  const title = "Model load on early requests";
  const name = "ollama.load_duration";
  const raw = rawEngineValues(metrics, name);
  if (raw.length < 2) {
    return insufficient(
      id,
      title,
      "Not enough data: this needs Ollama-reported load durations from at least two requests. Other engines do not report a per-request model load time.",
      [name],
    );
  }
  const values = raw.map((item) => toMs(item.value, item.unit));
  const first = values[0] as number;
  const firstPhase = raw[0]?.phase ?? null;
  const rest = values.slice(1);
  const restMedian = quantileR7(rest, 0.5) as number;
  const evidenceItems: DiagnosisEvidence[] = [
    {
      metric: name,
      label: `Load duration, first request${firstPhase ? ` (${firstPhase})` : ""}`,
      value: first,
      unit: "ms",
      sampleCount: 1,
    },
    {
      metric: name,
      label: "Load duration, median of later requests",
      value: restMedian,
      unit: "ms",
      sampleCount: rest.length,
    },
  ];
  const base = { id, title, evidence: evidenceItems, metrics: [name, "ttft_ms"] };
  if (first >= COLD_LOAD_MIN_MS && first >= COLD_LOAD_RATIO * Math.max(restMedian, 1)) {
    const phaseNote =
      firstPhase === "warmup"
        ? " It happened during warmup, so it is excluded from the measurement summaries."
        : firstPhase === "measurement"
          ? " It happened in the measurement phase, so it is included in the TTFT and latency summaries; adding warmup requests would keep it out."
          : "";
    return {
      ...base,
      status: "observed",
      severity: firstPhase === "warmup" ? "info" : "attention",
      statement: `The first request reported a model load of ${ms(first)}, while later requests reported a median of ${ms(restMedian)}. This is consistent with a cold model load on the first request.${phaseNote}`,
      nextStep:
        firstPhase === "warmup"
          ? null
          : "Add one or two warmup requests and compare TTFT p50 and p95.",
    };
  }
  if (restMedian >= COLD_LOAD_MIN_MS) {
    return {
      ...base,
      status: "observed",
      severity: "attention",
      statement: `Load duration stayed high across requests (median ${ms(restMedian)} after the first). This is consistent with the model being reloaded between requests, for example if it is evicted from memory or its keep-alive period expires.`,
      nextStep:
        "Check the engine's keep-alive setting and free memory, then re-run and compare load durations.",
    };
  }
  return {
    ...base,
    status: "not_observed",
    severity: "info",
    statement: `Load duration was small on the first and later requests (first ${ms(first)}, later median ${ms(restMedian)}). This is consistent with the model already being resident in memory.`,
    nextStep: null,
  };
}

function systemValues(metrics: MetricSample[], name: string): { values: number[]; unit: string } {
  const matching = metrics.filter(
    (metric) =>
      metric.metric_name === name && metric.source === "system_observed" && finite(metric.value),
  );
  return {
    values: matching.map((metric) => metric.value as number),
    unit: matching[0]?.unit ?? "",
  };
}

function mean(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export const MIN_RESOURCE_SAMPLES = 3;
export const HOST_CPU_BUSY_PERCENT = 85;
export const WORKER_CPU_LOW_PERCENT = 50;
export const WORKER_CPU_BUSY_PERCENT = 80;

function cpuRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "host_cpu";
  const title = "CPU saturation on this host";
  const names = ["host_cpu_percent", "worker_process_cpu_percent"];
  const host = systemValues(metrics, "host_cpu_percent");
  const worker = systemValues(metrics, "worker_process_cpu_percent");
  if (host.values.length < MIN_RESOURCE_SAMPLES || worker.values.length < MIN_RESOURCE_SAMPLES) {
    return insufficient(
      id,
      title,
      `Not enough data: this needs at least ${MIN_RESOURCE_SAMPLES} host CPU and benchmark-worker CPU samples. Very short runs may finish before enough samples are taken.`,
      names,
    );
  }
  const hostMean = mean(host.values);
  const hostPeak = Math.max(...host.values);
  const workerMean = mean(worker.values);
  const evidenceItems: DiagnosisEvidence[] = [
    {
      metric: "host_cpu_percent",
      label: "Host CPU, mean of samples",
      value: hostMean,
      unit: "%",
      sampleCount: host.values.length,
    },
    {
      metric: "host_cpu_percent",
      label: "Host CPU, peak sample",
      value: hostPeak,
      unit: "%",
      sampleCount: host.values.length,
    },
    {
      metric: "worker_process_cpu_percent",
      label: "Benchmark worker CPU, mean",
      value: workerMean,
      unit: worker.unit || "% of one logical CPU",
      sampleCount: worker.values.length,
    },
  ];
  const base = { id, title, evidence: evidenceItems, metrics: names };
  if (hostMean >= HOST_CPU_BUSY_PERCENT && workerMean <= WORKER_CPU_LOW_PERCENT) {
    return {
      ...base,
      status: "observed",
      severity: "attention",
      statement: `Host CPU averaged ${percent(hostMean)} (peak ${percent(hostPeak)}) while LocalForge's benchmark worker averaged ${percent(workerMean)} of one core. This is consistent with the engine, or another process on this machine, being CPU-bound on this host; LocalForge's own client is not the main consumer.`,
      nextStep:
        "If the engine should run on a GPU, check how many model layers are offloaded, then compare TPOT before and after.",
    };
  }
  if (hostMean >= HOST_CPU_BUSY_PERCENT) {
    return {
      ...base,
      status: "observed",
      severity: "attention",
      statement: `Host CPU averaged ${percent(hostMean)} and the benchmark worker averaged ${percent(workerMean)} of one core. This is consistent with the machine being CPU-saturated, with LocalForge's own client contributing part of the load.`,
      nextStep: "Lower the request rate or concurrency and check whether latency improves.",
    };
  }
  return {
    ...base,
    status: "not_observed",
    severity: "info",
    statement: `Host CPU averaged ${percent(hostMean)} (peak ${percent(hostPeak)}), so there is no sign of sustained CPU saturation on this host. GPU utilization is not measured.`,
    nextStep: null,
  };
}

function clientBusyRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "client_busy";
  const title = "Load generator busy";
  const name = "worker_process_cpu_percent";
  const worker = systemValues(metrics, name);
  if (worker.values.length < MIN_RESOURCE_SAMPLES) {
    return insufficient(
      id,
      title,
      `Not enough data: this needs at least ${MIN_RESOURCE_SAMPLES} benchmark-worker CPU samples.`,
      [name],
    );
  }
  const workerMean = mean(worker.values);
  const item: DiagnosisEvidence = {
    metric: name,
    label: "Benchmark worker CPU, mean",
    value: workerMean,
    unit: worker.unit || "% of one logical CPU",
    sampleCount: worker.values.length,
  };
  if (workerMean >= WORKER_CPU_BUSY_PERCENT) {
    return {
      id,
      title,
      status: "observed",
      severity: "attention",
      statement: `LocalForge's benchmark worker averaged ${percent(workerMean)} of one core. This is consistent with the load generator itself being busy, so client-measured timings at this load may include client overhead.`,
      evidence: [item],
      metrics: [name, "queue_time_ms"],
      nextStep: "Repeat at a lower request rate and compare TTFT and queue time.",
    };
  }
  return {
    id,
    title,
    status: "not_observed",
    severity: "info",
    statement: `LocalForge's benchmark worker averaged ${percent(workerMean)} of one core, which does not suggest the client was saturated.`,
    evidence: [item],
    metrics: [name],
    nextStep: null,
  };
}

const OUTCOME_LABELS: ReadonlyArray<[string, string]> = [
  ["requests_failed", "failed"],
  ["requests_timed_out", "timed out"],
  ["requests_disconnected", "disconnected"],
  ["requests_cancelled", "cancelled"],
  ["requests_interrupted", "interrupted"],
];

const OUTCOME_EXPLANATIONS: Record<string, string> = {
  requests_timed_out:
    "Timeouts are consistent with requests taking longer than the plan's timeout, for example from long outputs or an overloaded engine.",
  requests_failed:
    "Adapter-reported failures are consistent with the engine rejecting requests, for example a missing model, a prompt longer than the context window, or server errors; the run's event log has the details.",
  requests_disconnected:
    "Disconnects are consistent with the engine closing streams early, for example after a crash or restart.",
  requests_cancelled: "Cancellations usually come from stopping the run part-way.",
  requests_interrupted:
    "Interruptions are consistent with the LocalForge worker or application stopping during the run.",
};

export const ERROR_RATE_ATTENTION_PERCENT = 5;

function errorRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "errors";
  const title = "Failed and timed-out requests";
  const names = ["error_rate_percent", ...OUTCOME_LABELS.map(([name]) => name)];
  let best: { rate: Found; level: string } | null = null;
  for (const level of levelsOf(metrics)) {
    const rate = runValue(metrics, "error_rate_percent", "run_rate", level);
    if (rate && (!best || rate.value > best.rate.value)) best = { rate, level };
  }
  if (!best) {
    return insufficient(
      id,
      title,
      "Not enough data: the run has no error-rate summary, which needs at least one measurement request.",
      names,
    );
  }
  const { rate, level } = best;
  const attempted = runValue(metrics, "requests_attempted", "run_total", level);
  const counts = OUTCOME_LABELS.map(([name, label]) => ({
    name,
    label,
    found: runValue(metrics, name, "run_total", level),
  })).filter((item) => item.found && item.found.value > 0);
  const evidenceItems = [
    evidence(rate, "Error rate", level),
    ...(attempted ? [evidence(attempted, "Requests attempted", level)] : []),
    ...counts.map((item) => evidence(item.found as Found, `Requests ${item.label}`, level)),
  ];
  const where = levelSuffix(level);
  if (rate.value > 0) {
    const largest = [...counts].sort(
      (left, right) => (right.found as Found).value - (left.found as Found).value,
    )[0];
    const breakdown = counts
      .map((item) => `${formatNumber((item.found as Found).value)} ${item.label}`)
      .join(", ");
    return {
      id,
      title,
      status: "observed",
      severity: rate.value >= ERROR_RATE_ATTENTION_PERCENT ? "attention" : "info",
      statement:
        `${formatNumber(rate.value)}% of measurement requests did not succeed${where}${breakdown ? ` (${breakdown})` : ""}. ${largest ? (OUTCOME_EXPLANATIONS[largest.name] ?? "") : ""}`.trim(),
      evidence: evidenceItems,
      metrics: names,
      nextStep:
        largest?.name === "requests_timed_out"
          ? "Try a longer request timeout or a smaller output budget and check whether the timeouts disappear."
          : "Open the run's event log to see the reported error classes.",
    };
  }
  return {
    id,
    title,
    status: "not_observed",
    severity: "info",
    statement: `Every measurement request succeeded${where}${attempted ? ` (${formatNumber(attempted.value)} attempted)` : ""}.`,
    evidence: evidenceItems,
    metrics: names,
    nextStep: null,
  };
}

export const TAIL_RATIO = 2;

function tailRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "tail_latency";
  const title = "Tail latency variance";
  const names = ["request_latency_ms", "ttft_ms"];
  let best: { ratio: number; p50: Found; p95: Found; level: string } | null = null;
  let largestCount = 0;
  for (const level of levelsOf(metrics)) {
    const p50 = runValue(metrics, "request_latency_ms", "p50", level);
    const p95 = runValue(metrics, "request_latency_ms", "p95", level);
    const anyRecord = findRunMetric(metrics, "request_latency_ms", "p95", level);
    if (anyRecord) largestCount = Math.max(largestCount, anyRecord.sample_count);
    if (!p50 || !p95 || p50.value <= 0) continue;
    const ratio = p95.value / p50.value;
    if (!best || ratio > best.ratio) best = { ratio, p50, p95, level };
  }
  if (!best) {
    return insufficient(
      id,
      title,
      `Not enough data: p95 request latency needs at least 20 measurement requests${largestCount ? ` (this run has ${formatNumber(largestCount)})` : ""}.`,
      names,
    );
  }
  const where = levelSuffix(best.level);
  const base = {
    id,
    title,
    evidence: [
      evidence(best.p50, "Request latency p50", best.level),
      evidence(best.p95, "Request latency p95", best.level),
    ],
    metrics: names,
  };
  if (best.ratio >= TAIL_RATIO) {
    return {
      ...base,
      status: "observed",
      severity: "attention",
      statement: `p95 request latency (${ms(best.p95.value)}) is ${ratioText(best.ratio)} the median (${ms(best.p50.value)})${where}. This is consistent with tail-latency variance: a minority of requests took much longer, for example from waiting behind other requests, a cold start, or longer outputs.`,
      nextStep:
        "Compare TTFT and output-token counts for the slowest requests in the run detail to see which part grew.",
    };
  }
  return {
    ...base,
    status: "not_observed",
    severity: "info",
    statement: `p95 request latency is ${ratioText(best.ratio)} the median${where}, so latency was fairly consistent across requests.`,
    nextStep: null,
  };
}

export const QUEUE_ATTENTION_MS = 5;

function queueRule(metrics: MetricSample[], context: DiagnosisContext): DiagnosisFinding {
  const id = "queue_time";
  const title = "Requests waiting before dispatch";
  const name = "queue_time_ms";
  let best: { found: Found; level: string } | null = null;
  for (const level of levelsOf(metrics)) {
    const found = runValue(metrics, name, "mean", level);
    if (found && (!best || found.value > best.found.value)) best = { found, level };
  }
  if (!best) {
    return insufficient(id, title, "Not enough data: the run has no queue-time summary.", [name]);
  }
  const concurrency = best.level ? Number(best.level) : context.concurrency;
  const where =
    concurrency !== null && Number.isFinite(concurrency) && concurrency > 1
      ? ` at concurrency ${concurrency}`
      : "";
  const closedLoopNote =
    context.trafficMode === "closed_loop"
      ? " This is a closed-loop run, which dispatches immediately, so waiting inside the engine appears in TTFT rather than here."
      : "";
  const base = {
    id,
    title,
    evidence: [evidence(best.found, "Mean queue time", best.level)],
    metrics: [name, "ttft_ms", "request_throughput_requests_per_second"],
  };
  if (best.found.value > QUEUE_ATTENTION_MS) {
    return {
      ...base,
      status: "observed",
      severity: "attention",
      statement: `Requests waited on average ${ms(best.found.value)} between their scheduled arrival and dispatch${where}. This is consistent with arrivals outpacing completions: requests waited for a free client slot because the engine was not finishing them as fast as they were scheduled.`,
      nextStep:
        "Lower the arrival rate or concurrency and check whether queue time returns to near zero.",
    };
  }
  return {
    ...base,
    status: "not_observed",
    severity: "info",
    statement: `Mean queue time was ${ms(best.found.value)}${where}, so requests were dispatched close to their scheduled time.${closedLoopNote}`,
    nextStep: null,
  };
}

export const SCALING_THROUGHPUT_GAIN = 1.25;
export const SCALING_LATENCY_GROWTH = 1.5;

function scalingRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "concurrency_scaling";
  const title = "Throughput gain from concurrency";
  const names = ["output_throughput_tokens_per_second", "request_latency_ms", "ttft_ms"];
  const levels = loadLevels(metrics).filter((level) => level !== "");
  if (levels.length < 2) {
    return insufficient(
      id,
      title,
      "Not enough data: this compares load levels, so it needs a concurrency sweep with at least two levels.",
      names,
    );
  }
  const low = levels[0] as string;
  const high = levels[levels.length - 1] as string;
  const throughputName = runValue(metrics, "output_throughput_tokens_per_second", "run_rate", low)
    ? "output_throughput_tokens_per_second"
    : "request_throughput_requests_per_second";
  const lowThroughput = runValue(metrics, throughputName, "run_rate", low);
  const highThroughput = runValue(metrics, throughputName, "run_rate", high);
  const lowLatency = runValue(metrics, "request_latency_ms", "p50", low);
  const highLatency = runValue(metrics, "request_latency_ms", "p50", high);
  if (
    !lowThroughput ||
    !highThroughput ||
    !lowLatency ||
    !highLatency ||
    lowThroughput.value <= 0 ||
    lowLatency.value <= 0
  ) {
    return insufficient(
      id,
      title,
      `Not enough data: this needs throughput and median latency at concurrency ${low} and ${high}.`,
      names,
    );
  }
  const gain = highThroughput.value / lowThroughput.value;
  const growth = highLatency.value / lowLatency.value;
  const base = {
    id,
    title,
    evidence: [
      evidence(lowThroughput, "Throughput", low),
      evidence(highThroughput, "Throughput", high),
      evidence(lowLatency, "Request latency p50", low),
      evidence(highLatency, "Request latency p50", high),
    ],
    metrics: [throughputName, "request_latency_ms"],
  };
  if (gain < SCALING_THROUGHPUT_GAIN && growth >= SCALING_LATENCY_GROWTH) {
    return {
      ...base,
      status: "observed",
      severity: "attention",
      statement: `From concurrency ${low} to ${high}, throughput changed ${ratioText(gain)} while median latency grew ${ratioText(growth)}. This is consistent with the engine processing requests largely one at a time, or with a saturated resource: extra concurrency added waiting rather than throughput.`,
      nextStep:
        "Check the engine's parallel-request setting, then repeat the sweep to see whether throughput scales.",
    };
  }
  return {
    ...base,
    status: "not_observed",
    severity: "info",
    statement: `From concurrency ${low} to ${high}, throughput changed ${ratioText(gain)} while median latency changed ${ratioText(growth)}. ${gain >= SCALING_THROUGHPUT_GAIN ? "This is consistent with the engine overlapping or batching concurrent requests." : "Latency stayed close, so the higher levels did not add much waiting."}`,
    nextStep: null,
  };
}

export const MEMORY_PRESSURE_PERCENT = 90;

function memoryRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "memory_pressure";
  const title = "Host memory pressure";
  const names = ["host_memory_used_percent", "host_memory_available_bytes"];
  const used = systemValues(metrics, "host_memory_used_percent");
  if (used.values.length === 0) {
    return insufficient(id, title, "Not enough data: no host memory samples were recorded.", names);
  }
  const peak = Math.max(...used.values);
  const available = systemValues(metrics, "host_memory_available_bytes");
  const evidenceItems: DiagnosisEvidence[] = [
    {
      metric: "host_memory_used_percent",
      label: "Host memory used, peak sample",
      value: peak,
      unit: "%",
      sampleCount: used.values.length,
    },
    ...(available.values.length
      ? [
          {
            metric: "host_memory_available_bytes",
            label: "Host memory available, lowest sample",
            value: Math.min(...available.values),
            unit: "bytes",
            sampleCount: available.values.length,
          },
        ]
      : []),
  ];
  if (peak >= MEMORY_PRESSURE_PERCENT) {
    return {
      id,
      title,
      status: "observed",
      severity: "attention",
      statement: `Host memory use peaked at ${percent(peak)}. This is consistent with memory pressure: if the model and its KV cache do not fit, the engine may offload work or the operating system may page memory, both of which slow generation.`,
      evidence: evidenceItems,
      metrics: names,
      nextStep: "Try a smaller quantization or a shorter context and compare TPOT and peak memory.",
    };
  }
  return {
    id,
    title,
    status: "not_observed",
    severity: "info",
    statement: `Host memory use peaked at ${percent(peak)}, below the ${MEMORY_PRESSURE_PERCENT}% level that would suggest memory pressure. GPU memory is not measured.`,
    evidence: evidenceItems,
    metrics: names,
    nextStep: null,
  };
}

function enginePhaseRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "engine_phases";
  const title = "Where engine time went";
  const phaseNames: ReadonlyArray<[string, string]> = [
    ["ollama.load_duration", "model load"],
    ["ollama.prompt_eval_duration", "prompt evaluation"],
    ["ollama.eval_duration", "generation"],
  ];
  const means = phaseNames.map(([name, label]) => {
    const values = rawEngineValues(metrics, name)
      .filter((item) => item.phase !== "warmup")
      .map((item) => toMs(item.value, item.unit));
    return { name, label, count: values.length, mean: values.length ? mean(values) : null };
  });
  const available = means.filter((item) => item.mean !== null);
  if (available.length < 2) {
    return insufficient(
      id,
      title,
      "Not enough data: this needs Ollama-reported load, prompt evaluation and generation durations for measurement requests.",
      phaseNames.map(([name]) => name),
    );
  }
  const total = available.reduce((sum, item) => sum + (item.mean as number), 0);
  if (total <= 0) {
    return insufficient(
      id,
      title,
      "Not enough data: the engine-reported durations sum to zero.",
      phaseNames.map(([name]) => name),
    );
  }
  const largest = [...available].sort(
    (left, right) => (right.mean as number) - (left.mean as number),
  )[0]!;
  const shares = available
    .map((item) => `${item.label} ${percent(((item.mean as number) / total) * 100)}`)
    .join(", ");
  return {
    id,
    title,
    status: "observed",
    severity: "info",
    statement: `Of Ollama's reported time per measurement request, ${shares}. The largest share is ${largest.label}, consistent with that phase dominating server-side time for this workload.`,
    evidence: available.map((item) => ({
      metric: item.name,
      label: `Mean ${item.label} (engine-reported)`,
      value: item.mean as number,
      unit: "ms",
      sampleCount: item.count,
    })),
    metrics: phaseNames.map(([name]) => name),
    nextStep: null,
  };
}

function sampleSizeRule(metrics: MetricSample[]): DiagnosisFinding {
  const id = "sample_size";
  const title = "Sample size";
  const name = "requests_attempted";
  const attempted = levelsOf(metrics)
    .map((level) => ({ level, found: runValue(metrics, name, "run_total", level) }))
    .filter((item): item is { level: string; found: Found } => item.found !== null)
    .sort((left, right) => left.found.value - right.found.value)[0];
  if (!attempted) {
    return insufficient(id, title, "Not enough data: the run has no attempted-request total.", [
      name,
    ]);
  }
  const count = attempted.found.value;
  const base = {
    id,
    title,
    evidence: [evidence(attempted.found, "Requests attempted", attempted.level)],
    metrics: [name, "request_latency_ms"],
  };
  if (count < 20) {
    return {
      ...base,
      status: "observed",
      severity: "info",
      statement: `Only ${formatNumber(count)} measurement requests${levelSuffix(attempted.level)}. p95 needs at least 20 and p99 at least 100, so tail percentiles are unavailable and single slow requests weigh heavily on the mean.`,
      nextStep: "Increase the request count to 20 or more before drawing conclusions about tails.",
    };
  }
  return {
    ...base,
    status: "not_observed",
    severity: "info",
    statement: `${formatNumber(count)} measurement requests${levelSuffix(attempted.level)}${count < 100 ? ": enough for p95, though p99 needs 100" : ": enough for p95 and p99"}.`,
    nextStep: null,
  };
}

const STATUS_ORDER: Record<DiagnosisStatus, number> = {
  observed: 0,
  not_observed: 1,
  insufficient_data: 2,
};

/**
 * Evaluate every diagnosis rule against a run's persisted metric records. Results are ordered
 * observed (attention first), then checked-without-signal, then not-enough-data.
 */
export function diagnoseRun(
  metrics: MetricSample[],
  context: DiagnosisContext = { concurrency: null, trafficMode: null },
): DiagnosisFinding[] {
  const findings = [
    ttftShareRule(metrics),
    coldLoadRule(metrics),
    enginePhaseRule(metrics),
    tailRule(metrics),
    queueRule(metrics, context),
    scalingRule(metrics),
    errorRule(metrics),
    cpuRule(metrics),
    clientBusyRule(metrics),
    memoryRule(metrics),
    sampleSizeRule(metrics),
  ];
  return findings
    .map((finding, index) => ({ finding, index }))
    .sort(
      (left, right) =>
        STATUS_ORDER[left.finding.status] - STATUS_ORDER[right.finding.status] ||
        (left.finding.severity === right.finding.severity
          ? 0
          : left.finding.severity === "attention"
            ? -1
            : 1) ||
        left.index - right.index,
    )
    .map(({ finding }) => finding);
}
