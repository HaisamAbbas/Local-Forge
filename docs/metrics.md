# Benchmark metric definitions

LOCALFORGE stores request samples before it calculates run summaries. Each persisted metric
includes its unit, source, aggregation, sample count, definition, formula, method version, and any
quality flags. The current derived method identifier is `localforge.metrics.v1`. Request durations
and run windows use monotonic-clock offsets; wall-clock timestamps are retained for display and
correlation only.

## Client measurements

| Metric | Unit | Definition and formula |
|---|---|---|
| Request latency | ms | Per-request dispatch to terminal response duration, measured with a monotonic clock. Aggregates use eligible measurement-phase request samples. |
| TTFT | ms | Dispatch to the first non-empty streamed output event. It is unavailable when that event was not observed. |
| Inter-event latency | ms | Time between non-empty streamed output events. This is chunk/event timing, not per-token ITL. |
| Inter-token latency (ITL) | ms/token | Time between token timestamps from a token-aware source. Chunk count is never used as a substitute. It is unavailable when token timestamps are absent. |
| TPOT | ms/token | For a successful request with an exact output-token count of at least two: `(completion offset - first-output offset) / (output tokens - 1)`. Estimated token counts do not qualify. |
| Input throughput | tokens/s | Sum of known input tokens divided by the monotonic measurement window. The source label shows exact, estimated, or mixed token-count provenance. This is not engine prefill throughput. |
| Output throughput | tokens/s | Successful output tokens divided by the monotonic measurement window. Estimated counts are labeled. |
| Generation throughput | tokens/s | Eligible successful output tokens divided by the sum of their post-first-output durations. This is distinct from aggregate output throughput. |
| Request throughput | requests/s | Successful measurement requests divided by `(last terminal completion offset - first dispatch offset)`. |
| Queue time | ms | Scheduled arrival to local dispatch delay. Closed-loop workloads dispatch immediately and have no target arrival queue. |
| Outcome counts | requests | Separate attempted, succeeded, failed, disconnected, timed-out, cancelled, and interrupted request counts. |
| Error rate | % | `100 * (failed + disconnected + timed out + cancelled + interrupted) / attempted`. |

Warmup requests are persisted as raw samples but excluded from the measurement summaries. Zero token
counts are valid observations. Character counts are never converted to token counts. Request samples
keep partial-output state and error class even when prompt/output text retention is disabled.
When retention is enabled, complete output text is stored with the terminal sample; abrupt worker loss
preserves partial-output metadata but may not preserve the unfinished output text.
Metrics from a concurrency sweep are emitted separately per requested level, with both
`concurrency_level` and `effective_concurrency` labels, so measurements from different load levels
are not blended into one summary.

## Percentiles and quality flags

Percentiles use the R-7 method (linear interpolation at `(n - 1) * p`) and are only assigned a
numeric value when the sample count reaches the current threshold:

| Percentile | Minimum eligible observations |
|---|---:|
| p50 | 1 |
| p90 | 10 |
| p95 | 20 |
| p99 | 100 |

Below the threshold, the metric value is `null`, while its observed sample count and
`insufficient_samples_for_stable_tail` flag remain available. Missing TTFT, token timestamps,
exact token counts, or a valid measurement window likewise produce unavailable values with a reason
flag instead of an inferred number.
Input and output token throughput are null when any corresponding expected request count is missing;
`incomplete_token_count_coverage` identifies these partial usage records.
Run-level throughput is also null when a measurement request lacks a monotonic dispatch or end offset,
because its full measurement window cannot be established.

## Engine-native metrics

Native observations are stored separately from client measurements with the adapter ID/version,
engine version, source, labels, unit, gauge/counter kind, timestamp, and before/after snapshot label.
Expected but absent metrics are stored as unavailable samples. Counter deltas are calculated only
when both values are non-negative and the counter did not decrease. Resets, negative values,
duplicate names, non-native source labels, missing snapshots, kind changes, and unit changes are
flagged and do not produce a numeric delta.
Native definitions can differ between engines, so native and client metrics must not be merged into
one unlabeled series. Snapshot placeholders are expected only from adapters that declare the
`native_metrics` capability (an aggregate metrics endpoint); adapters that only report per-request
values, such as Ollama, do not produce before/after/delta placeholder rows.

### Per-request engine-native observations

Some engines return values with each response (for example Ollama's durations and token counts).
Each value is stored as a raw `engine_native` record with a `request_id` label and the request
`phase`. At finalization, the measurement-phase observations are summarized per metric name and unit
(and per concurrency level, with the same labels as client metrics) using the same rules as client
distributions: `mean` is the arithmetic mean and `p50`/`p90`/`p95`/`p99` use R-7 with the minimum
counts below. These summaries keep `source = engine_native` because they summarize engine-reported
values; `sample_count` is the number of requests that reported the value. Engines that report no
per-request values gain no summary rows.

Engine-reported token rates are computed by LOCALFORGE from engine values, so they use
`source = derived` and `aggregation = run_rate`, and keep the engine prefix in their names:

| Metric | Unit | Formula |
|---|---|---|
| `ollama.eval_rate_tokens_per_second` | tokens/s | `sum(ollama.eval_count) / (sum(ollama.eval_duration ns) / 1e9)` over measurement requests that reported both |
| `ollama.prompt_eval_rate_tokens_per_second` | tokens/s | `sum(ollama.prompt_eval_count) / (sum(ollama.prompt_eval_duration ns) / 1e9)` over measurement requests that reported both |

The rate is `null` with `incomplete_token_count_coverage` when a request reported the duration but
no count, and `null` with `unavailable_zero_engine_duration` when the summed duration is zero. For
runs recorded before per-request counts were stored, the count falls back to the request sample's
`engine_exact` usage count (which Ollama populates from the same response fields) and the row is
flagged `token_counts_from_engine_usage_record`. These rates are timed by the engine and exclude
client, network, and streaming overhead, so they are reported separately from the client-timed
throughput metrics. In particular, `prompt_processing_throughput_tokens_per_second` stays
unavailable until an adapter exposes a prefill interval with a cross-engine definition; an
engine-timed prefill rate is never substituted for it.

Runs finalized before these summaries existed are backfilled when the API starts: for finalized
runs with stored per-request engine-native records, missing summary rows are recomputed from the
stored request samples and raw records with the same method and appended. Existing rows are never
modified, and repeating the backfill adds nothing.

## Output quality and verdicts

Quality checks are opt-in per workload (`quality_checks`). The worker evaluates each measured
output in memory with method `localforge.quality.v1` and stores only `quality_outcome`
(`pass`, `fail`, `unknown`) and stable reason codes on the request sample; the output text is
stored only when the plan enables content retention. Exact match normalizes code fences,
wrapping quotes, case, whitespace, and trailing punctuation; expected answers that are JSON
objects or arrays compare as parsed JSON. "Stopped properly" fails when the engine-reported
output token count reaches the request token limit and is `unknown` without a token count.
Requests that did not complete are `unknown` (they count toward the error rate instead).

`quality_pass_rate_percent` (source `derived`, aggregation `run_rate`) is
`100 * passed / (passed + failed)`; `sample_count` is the number of checked outputs, unchecked
outputs are excluded and flagged `some_outputs_not_evaluated`. The row exists only for runs
with checks enabled.

Run verdicts (`localforge.verdict.v1`, `GET /api/v1/runs/{run_id}/verdict`) are computed on read
from the persisted summaries: `ttft_ms` p50/p95, `request_latency_ms` p50,
`generation_throughput_tokens_per_second`, `request_throughput_requests_per_second`,
`error_rate_percent`, and `quality_pass_rate_percent`. A target is `unknown` when its metric is
missing, null (for example a p95 with fewer than 20 samples), or a quality pass rate covers
fewer than 5 checked outputs. For a concurrency sweep the highest level is judged.

## Comparing runs

Run comparisons pair only run-level metrics whose name, unit, source, aggregation, definition,
formula, method version, kind, and stable labels match across every selected run. Per-request
records (any `request_id` label), `raw` and `sample` aggregations, and `system_observed` telemetry
samples are never paired across runs; they are listed once per metric name under excluded metrics.
Metrics with no value in any compared run are also listed as excluded rather than shown as empty
rows. The "Shared metric sources and definitions" dimension compares the same run-level set.

The hardware dimension compares a stable identity: OS name and version, architecture, CPU model,
logical cores, total memory, and each accelerator's name and vendor must be known for every run,
otherwise the dimension is unknown. Physical cores and accelerator memory, integrated/shared-memory
flags, runtime, and driver version are compared when every run reports them; when only some runs
report a field it is listed in `unverified_fields` instead. Detection provenance (`detected_by`)
and observation timestamps are not part of the identity.

## Local resource samples

The isolated benchmark worker records best-effort operating-system observations with source
`system_observed`, aggregation `sample`, and method version
`localforge.resource_sampler.v1`. It samples host CPU utilization, host memory utilization and
available bytes, and the LOCALFORGE worker process CPU utilization and resident memory (RSS).
Memory and RSS values are point-in-time readings. CPU percentages cover the interval since the
previous sample; `interval_seconds` records that interval. Worker CPU is expressed as a percentage
of one logical CPU and can exceed 100% when it uses multiple cores.

Metric labels distinguish `scope=local_host` from `scope=benchmark_worker`. Host readings describe
the whole machine running LOCALFORGE. Worker readings describe only LOCALFORGE's isolated
benchmark process. Neither scope measures an attached engine process, a remote host, GPU usage, or
power consumption. Sampling runs at one-second intervals while the worker is active and records a
final sample when available. Sampling failures remain unavailable and are not represented as zero.

Derived records are available at `GET /api/v1/runs/{run_id}/metrics`; request records are available
at `GET /api/v1/runs/{run_id}/samples`. The samples plus each metric's formula and method version
provide the inputs needed to reproduce the run summary. The built-in fake adapter is for exercising
the local integration boundary only; its timings are not model-performance measurements.
