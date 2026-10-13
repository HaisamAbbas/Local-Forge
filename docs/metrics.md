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
one unlabeled series.

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
