"""Versioned benchmark aggregation and native-metric quality checks."""

import math
import sqlite3
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import Literal
from uuid import UUID

from localforge.adapters.contracts import AdapterMetric
from localforge.core.models import (
    MetricSample,
    MetricSource,
    RequestOutcome,
    RequestSample,
    TokenCountSource,
    utc_now,
)

METRIC_METHOD_VERSION = "localforge.metrics.v1"
PERCENTILE_MINIMUMS = {"p50": 1, "p90": 10, "p95": 20, "p99": 100}
PercentileName = Literal["p50", "p90", "p95", "p99"]


@dataclass(frozen=True)
class NativeMetricEvaluation:
    samples: list[MetricSample]
    warnings: list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class _RequestMetricSample:
    """The request fields needed for aggregation, excluding retained bodies and metadata."""

    concurrency_level: int | None
    effective_concurrency: int | None
    outcome: RequestOutcome
    dispatch_offset_ms: float | None
    completed_offset_ms: float | None
    latency_ms: float | None
    ttft_ms: float | None
    queue_time_ms: float | None
    inter_event_latency_ms: Sequence[float]
    inter_token_latency_ms: Sequence[float]
    prompt_tokens: int | None
    prompt_token_count_source: TokenCountSource
    completion_tokens: int | None
    completion_token_count_source: TokenCountSource
    first_output_offset_ms: float | None

    @classmethod
    def from_request(cls, sample: RequestSample) -> "_RequestMetricSample":
        return cls(
            concurrency_level=sample.concurrency_level,
            effective_concurrency=sample.effective_concurrency,
            outcome=sample.outcome,
            dispatch_offset_ms=sample.dispatch_offset_ms,
            completed_offset_ms=sample.completed_offset_ms,
            latency_ms=sample.latency_ms,
            ttft_ms=sample.ttft_ms,
            queue_time_ms=sample.queue_time_ms,
            inter_event_latency_ms=sample.inter_event_latency_ms,
            inter_token_latency_ms=sample.inter_token_latency_ms,
            prompt_tokens=sample.prompt_tokens,
            prompt_token_count_source=sample.prompt_token_count_source,
            completion_tokens=sample.completion_tokens,
            completion_token_count_source=sample.completion_token_count_source,
            first_output_offset_ms=sample.first_output_offset_ms,
        )


def _metric(
    run_id: UUID,
    name: str,
    value: float | None,
    unit: str,
    *,
    source: MetricSource,
    aggregation: str,
    sample_count: int,
    definition: str,
    formula: str,
    quality_flags: Iterable[str] = (),
    metric_kind: Literal["gauge", "counter"] = "gauge",
    observed_at: datetime | None = None,
    labels: dict[str, str] | None = None,
    adapter_id: str | None = None,
    adapter_version: str | None = None,
    engine_version: str | None = None,
) -> MetricSample:
    return MetricSample(
        run_id=run_id,
        metric_name=name,
        value=value,
        unit=unit,
        source=source,
        aggregation=aggregation,
        sample_count=sample_count,
        definition=definition,
        formula=formula,
        method_version=METRIC_METHOD_VERSION,
        quality_flags=list(quality_flags),
        metric_kind=metric_kind,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        engine_version=engine_version,
        observed_at=observed_at or utc_now(),
        labels=labels or {},
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Calculate an R-7/linear-interpolation percentile over sorted observations."""

    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def native_metric_snapshot_samples(
    run_id: UUID,
    metrics: Sequence[AdapterMetric] | None,
    *,
    expected_names: Iterable[str],
    stage: Literal["before", "after"],
    adapter_id: str,
    adapter_version: str | None,
    engine_version: str | None,
) -> NativeMetricEvaluation:
    """Persist raw native observations immediately, preserving missing and invalid values."""

    rows: list[MetricSample] = []
    warnings: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    seen_series: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for metric in metrics or ():
        flags: list[str] = []
        series_key = (metric.metric_name, tuple(sorted(metric.labels.items())))
        if series_key in seen_series:
            flags.append("duplicate_metric_name")
            warnings.append(
                (
                    "native_metric_duplicate",
                    f"{metric.metric_name} with the same labels was reported more than once.",
                )
            )
        seen_names.add(metric.metric_name)
        seen_series.add(series_key)
        if metric.kind == "counter" and metric.value < 0:
            flags.append("invalid_negative_counter")
            warnings.append(
                ("native_counter_invalid", f"{metric.metric_name} has a negative counter value.")
            )
        if metric.source is not MetricSource.ENGINE_NATIVE:
            flags.append("source_mismatch")
            warnings.append(
                (
                    "native_metric_source_mismatch",
                    f"{metric.metric_name} was not marked engine-native.",
                )
            )
        rows.append(
            _metric(
                run_id,
                metric.metric_name,
                metric.value,
                metric.unit,
                source=metric.source,
                aggregation="raw",
                sample_count=1,
                definition="Unmodified adapter-reported native metric observation.",
                formula="raw adapter value",
                quality_flags=flags,
                metric_kind=metric.kind,
                observed_at=metric.observed_at,
                labels=metric.labels | {"snapshot": stage},
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                engine_version=engine_version,
            )
        )
    for name in sorted(set(expected_names) - seen_names):
        warnings.append(("native_metric_missing", f"{name} was missing from the {stage} snapshot."))
        rows.append(
            _metric(
                run_id,
                name,
                None,
                "unknown",
                source=MetricSource.ENGINE_NATIVE,
                aggregation="raw",
                sample_count=0,
                definition="Expected native metric was unavailable in this snapshot.",
                formula="unavailable: adapter did not provide a value",
                quality_flags=["missing_native_metric", f"missing_{stage}_snapshot"],
                observed_at=utc_now(),
                labels={"snapshot": stage},
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                engine_version=engine_version,
            )
        )
    return NativeMetricEvaluation(samples=rows, warnings=warnings)


def _distribution(
    run_id: UUID,
    name: str,
    values: Sequence[float],
    unit: str,
    *,
    definition: str,
    source: MetricSource = MetricSource.CLIENT_MEASURED,
    labels: dict[str, str] | None = None,
    quality_flags: Iterable[str] = (),
) -> list[MetricSample]:
    count = len(values)
    result = [
        _metric(
            run_id,
            name,
            fmean(values) if values else None,
            unit,
            source=source,
            aggregation="mean",
            sample_count=count,
            definition=definition,
            formula="arithmetic mean of eligible raw request observations",
            quality_flags=quality_flags if values else [*quality_flags, "unavailable_no_samples"],
            labels=labels,
        )
    ]
    for aggregation, percentile in (
        ("p50", 0.50),
        ("p90", 0.90),
        ("p95", 0.95),
        ("p99", 0.99),
    ):
        minimum = PERCENTILE_MINIMUMS[aggregation]
        stable = count >= minimum
        flags = list(quality_flags)
        if not stable:
            flags.append("insufficient_samples_for_stable_tail")
        if not values:
            flags.append("unavailable_no_samples")
        result.append(
            _metric(
                run_id,
                name,
                _percentile(values, percentile) if stable else None,
                unit,
                source=source,
                aggregation=aggregation,
                sample_count=count,
                definition=definition,
                formula=(
                    f"R-7 linear-interpolation percentile at {aggregation[1:]} "
                    f"(minimum {minimum} samples)"
                ),
                quality_flags=flags,
                labels=labels,
            )
        )
    return result


def aggregate_run_metrics(
    run_id: UUID,
    raw_samples: Iterable[RequestSample],
) -> list[MetricSample]:
    """Aggregate measurement requests separately for each concurrency level."""

    samples_by_level: dict[int | None, list[_RequestMetricSample]] = {}
    for sample in raw_samples:
        if sample.phase == "measurement":
            samples_by_level.setdefault(sample.concurrency_level, []).append(
                _RequestMetricSample.from_request(sample)
            )
    if not samples_by_level:
        return _aggregate_run_metric_records(run_id, ())

    separate_levels = len(samples_by_level) > 1
    result: list[MetricSample] = []
    for level in sorted(samples_by_level, key=lambda item: (item is None, item or 0)):
        metrics = _aggregate_run_metric_records(run_id, samples_by_level[level])
        if level is None and not separate_levels:
            return metrics
        labels = {"concurrency_level": str(level) if level is not None else "unrecorded"}
        effective_levels = {
            str(sample.effective_concurrency)
            for sample in samples_by_level[level]
            if sample.effective_concurrency is not None
        }
        if effective_levels:
            labels["effective_concurrency"] = (
                next(iter(effective_levels)) if len(effective_levels) == 1 else "mixed"
            )
        result.extend(
            metric.model_copy(update={"labels": metric.labels | labels}) for metric in metrics
        )
    return result


def _aggregate_run_metric_records(
    run_id: UUID,
    raw_samples: Iterable[_RequestMetricSample],
) -> list[MetricSample]:
    """Summarize persisted request records for one measurement group."""

    latencies: list[float] = []
    ttfts: list[float] = []
    queue_times: list[float] = []
    inter_events: list[float] = []
    inter_tokens: list[float] = []
    tpot_values: list[float] = []
    outcomes: Counter[RequestOutcome] = Counter()
    attempted = 0
    incomplete_measurement_window = False
    first_dispatch: float | None = None
    last_completion: float | None = None
    input_token_total = 0
    input_token_samples = 0
    input_token_sources: set[str] = set()
    output_token_total = 0
    output_token_samples = 0
    output_token_sources: set[str] = set()
    generation_token_total = 0
    generation_token_sources: set[str] = set()
    generation_seconds = 0.0
    generation_samples = 0

    exact_sources = {TokenCountSource.ENGINE_EXACT, TokenCountSource.TOKENIZER_EXACT}
    estimated_sources = {TokenCountSource.ENGINE_ESTIMATED, TokenCountSource.ESTIMATED}
    for sample in raw_samples:
        attempted += 1
        outcomes[sample.outcome] += 1
        if sample.dispatch_offset_ms is None or sample.completed_offset_ms is None:
            incomplete_measurement_window = True
        if sample.latency_ms is not None and math.isfinite(sample.latency_ms):
            latencies.append(sample.latency_ms)
        if sample.ttft_ms is not None and math.isfinite(sample.ttft_ms):
            ttfts.append(sample.ttft_ms)
        if sample.queue_time_ms is not None and math.isfinite(sample.queue_time_ms):
            queue_times.append(sample.queue_time_ms)
        inter_events.extend(
            value for value in sample.inter_event_latency_ms if math.isfinite(value)
        )
        inter_tokens.extend(
            value for value in sample.inter_token_latency_ms if math.isfinite(value)
        )
        if sample.dispatch_offset_ms is not None:
            first_dispatch = (
                sample.dispatch_offset_ms
                if first_dispatch is None
                else min(first_dispatch, sample.dispatch_offset_ms)
            )
        if sample.completed_offset_ms is not None:
            last_completion = (
                sample.completed_offset_ms
                if last_completion is None
                else max(last_completion, sample.completed_offset_ms)
            )
        if (
            sample.prompt_tokens is not None
            and sample.prompt_token_count_source in exact_sources | estimated_sources
        ):
            input_token_total += sample.prompt_tokens
            input_token_samples += 1
            input_token_sources.add(sample.prompt_token_count_source.value)
        if (
            sample.outcome is RequestOutcome.SUCCEEDED
            and sample.completion_tokens is not None
            and sample.completion_token_count_source in exact_sources | estimated_sources
        ):
            output_token_total += sample.completion_tokens
            output_token_samples += 1
            output_token_sources.add(sample.completion_token_count_source.value)
            if sample.completed_offset_ms is not None and sample.first_output_offset_ms is not None:
                generation_token_total += sample.completion_tokens
                generation_token_sources.add(sample.completion_token_count_source.value)
                generation_seconds += max(
                    0.0,
                    (sample.completed_offset_ms - sample.first_output_offset_ms) / 1000,
                )
                generation_samples += 1
            if (
                sample.completion_tokens >= 2
                and sample.completed_offset_ms is not None
                and sample.first_output_offset_ms is not None
                and sample.completion_token_count_source in exact_sources
            ):
                tpot_values.append(
                    max(0.0, sample.completed_offset_ms - sample.first_output_offset_ms)
                    / (sample.completion_tokens - 1)
                )

    succeeded = outcomes[RequestOutcome.SUCCEEDED]
    failed = outcomes[RequestOutcome.FAILED]
    disconnected = outcomes[RequestOutcome.DISCONNECTED]
    timed_out = outcomes[RequestOutcome.TIMED_OUT]
    cancelled = outcomes[RequestOutcome.CANCELLED]
    interrupted = outcomes[RequestOutcome.INTERRUPTED]
    window_seconds = (
        (last_completion - first_dispatch) / 1000
        if not incomplete_measurement_window
        and first_dispatch is not None
        and last_completion is not None
        and last_completion > first_dispatch
        else None
    )
    window_quality_flag = (
        "incomplete_measurement_window"
        if incomplete_measurement_window
        else "unavailable_measurement_window"
    )
    metric_rows: list[MetricSample] = []
    metric_rows.extend(
        _distribution(
            run_id,
            "request_latency_ms",
            latencies,
            "ms",
            definition="Client-observed request dispatch to terminal response duration.",
            quality_flags=(["incomplete_request_samples"] if len(latencies) < attempted else []),
        )
    )
    metric_rows.extend(
        _distribution(
            run_id,
            "ttft_ms",
            ttfts,
            "ms",
            definition="Dispatch to first non-empty streamed output event.",
            quality_flags=["ttft_only_when_observed"] if len(ttfts) < attempted else [],
        )
    )
    metric_rows.extend(
        _distribution(
            run_id,
            "queue_time_ms",
            queue_times,
            "ms",
            definition="Scheduled arrival to local request dispatch delay.",
        )
    )
    metric_rows.extend(
        _distribution(
            run_id,
            "inter_event_latency_ms",
            inter_events,
            "ms",
            definition="Elapsed time between non-empty streamed output events; not per-token ITL.",
        )
    )
    metric_rows.extend(
        _distribution(
            run_id,
            "inter_token_latency_ms",
            inter_tokens,
            "ms/token",
            definition="Elapsed time between token timestamps supplied by a token-aware source.",
            quality_flags=([] if inter_tokens else ["unavailable_token_timestamps_not_provided"]),
        )
    )
    metric_rows.extend(
        _distribution(
            run_id,
            "tpot_ms_per_token",
            tpot_values,
            "ms/token",
            source=MetricSource.DERIVED,
            definition="Post-first-output duration divided by the remaining exact output tokens.",
            quality_flags=([] if tpot_values else ["unavailable_exact_token_timing_or_counts"]),
        )
    )

    def rate_metric(
        name: str,
        total: int,
        sample_count: int,
        sources: set[str],
        unit: str,
        definition: str,
        denominator_seconds: float | None,
        formula: str,
        expected_sample_count: int | None = None,
        window_quality_flag: str = "unavailable_measurement_window",
    ) -> MetricSample:
        flags: list[str] = []
        value: float | None = None
        if sample_count == 0:
            flags.append("unavailable_token_counts_missing")
        elif expected_sample_count is not None and sample_count < expected_sample_count:
            flags.append("incomplete_token_count_coverage")
        if denominator_seconds is None or denominator_seconds <= 0:
            flags.append(window_quality_flag)
        elif sample_count > 0 and (
            expected_sample_count is None or sample_count >= expected_sample_count
        ):
            value = total / denominator_seconds
        if sources & estimated_sources:
            flags.append("includes_estimated_token_counts")
        source_label = (
            "unavailable" if not sources else next(iter(sources)) if len(sources) == 1 else "mixed"
        )
        return _metric(
            run_id,
            name,
            value,
            unit,
            source=MetricSource.DERIVED,
            aggregation="run_rate",
            sample_count=sample_count,
            definition=definition,
            formula=formula,
            quality_flags=flags,
            labels={"token_count_source": source_label},
        )

    metric_rows.append(
        rate_metric(
            "input_throughput_tokens_per_second",
            input_token_total,
            input_token_samples,
            input_token_sources,
            "tokens/s",
            "Input tokens per measurement-window second; not engine prefill throughput.",
            window_seconds,
            "sum of input tokens / monotonic measurement-window seconds",
            expected_sample_count=attempted,
            window_quality_flag=window_quality_flag,
        )
    )
    metric_rows.append(
        rate_metric(
            "output_throughput_tokens_per_second",
            output_token_total,
            output_token_samples,
            output_token_sources,
            "tokens/s",
            "Completed output tokens per monotonic measurement-window second.",
            window_seconds,
            "sum of successful output tokens / monotonic measurement-window seconds",
            expected_sample_count=succeeded,
            window_quality_flag=window_quality_flag,
        )
    )
    metric_rows.append(
        rate_metric(
            "generation_throughput_tokens_per_second",
            generation_token_total,
            generation_samples,
            generation_token_sources,
            "tokens/s",
            "Output tokens per summed post-first-output duration for eligible requests.",
            generation_seconds if generation_samples else None,
            "eligible output tokens / sum(completion offset - first-output offset)",
        )
    )

    totals: tuple[tuple[str, float | None, str, str, str, int, list[str]], ...] = (
        (
            "request_throughput_requests_per_second",
            succeeded / window_seconds if window_seconds else None,
            "requests/s",
            "Successfully completed requests per monotonic measurement-window second.",
            "successful requests / (last completion offset - first dispatch offset)",
            succeeded,
            [] if window_seconds else [window_quality_flag],
        ),
        (
            "requests_attempted",
            float(attempted),
            "requests",
            "Measurement-phase requests that were dispatched.",
            "count of measurement request records",
            attempted,
            [],
        ),
        (
            "requests_succeeded",
            float(succeeded),
            "requests",
            "Measurement requests completed successfully.",
            "count(outcome = succeeded)",
            attempted,
            [],
        ),
        (
            "requests_failed",
            float(failed),
            "requests",
            "Measurement requests reported as failed by the adapter.",
            "count(outcome = failed)",
            attempted,
            [],
        ),
        (
            "requests_disconnected",
            float(disconnected),
            "requests",
            "Measurement requests whose response stream disconnected before completion.",
            "count(outcome = disconnected)",
            attempted,
            [],
        ),
        (
            "requests_timed_out",
            float(timed_out),
            "requests",
            "Measurement requests that exceeded their timeout.",
            "count(outcome = timed_out)",
            attempted,
            [],
        ),
        (
            "requests_cancelled",
            float(cancelled),
            "requests",
            "Measurement requests cancelled by the user or adapter.",
            "count(outcome = cancelled)",
            attempted,
            [],
        ),
        (
            "requests_interrupted",
            float(interrupted),
            "requests",
            "Measurement requests left incomplete by worker or application interruption.",
            "count(outcome = interrupted)",
            attempted,
            [],
        ),
        (
            "error_rate_percent",
            100 * (failed + disconnected + timed_out + cancelled + interrupted) / attempted
            if attempted
            else None,
            "%",
            (
                "Failed, disconnected, timed-out, cancelled, and interrupted requests "
                "as a share of attempts."
            ),
            "100 * (failed + disconnected + timed out + cancelled + interrupted) / attempted",
            attempted,
            [] if attempted else ["unavailable_no_measurement_requests"],
        ),
    )
    for name, value, unit, definition, formula, count, quality in totals:
        metric_rows.append(
            _metric(
                run_id,
                name,
                value,
                unit,
                source=MetricSource.DERIVED,
                aggregation="run_total" if unit == "requests" else "run_rate",
                sample_count=count,
                definition=definition,
                formula=formula,
                quality_flags=quality,
            )
        )
    metric_rows.append(
        _metric(
            run_id,
            "prompt_processing_throughput_tokens_per_second",
            None,
            "tokens/s",
            source=MetricSource.DERIVED,
            aggregation="run_rate",
            sample_count=0,
            definition="Input tokens divided by an isolated prefill interval.",
            formula="unavailable: the adapter does not expose a prefill interval",
            quality_flags=["unavailable_prefill_interval"],
        )
    )
    return metric_rows


def persist_derived_run_metrics(
    connection: sqlite3.Connection,
    run_id: UUID,
) -> list[MetricSample]:
    """Persist versioned aggregates once, only after raw request records exist."""

    from localforge.storage.database import transaction
    from localforge.storage.repositories import MetricSampleRepository, RequestSampleRepository

    metric_repository = MetricSampleRepository(connection)
    existing = metric_repository.list_for_run(run_id)
    if any(
        metric.metric_name == "requests_attempted"
        and metric.source is MetricSource.DERIVED
        and metric.aggregation == "run_total"
        for metric in existing
    ):
        return []
    samples = RequestSampleRepository(connection).iter_for_run(run_id, limit=None)
    metrics = aggregate_run_metrics(run_id, samples)
    with transaction(connection):
        for metric in metrics:
            metric_repository.create(metric)
    return metrics


def evaluate_native_metric_snapshots(
    run_id: UUID,
    before: Sequence[AdapterMetric] | None,
    after: Sequence[AdapterMetric] | None,
    *,
    expected_names: Iterable[str],
    adapter_id: str,
    adapter_version: str | None,
    engine_version: str | None,
) -> NativeMetricEvaluation:
    """Compare two already-persisted native snapshots and flag invalid counter deltas."""

    warnings: list[tuple[str, str]] = []
    rows: list[MetricSample] = []
    by_stage = {"before": before or (), "after": after or ()}
    MetricKey = tuple[str, tuple[tuple[str, str], ...]]
    indexed: dict[str, dict[MetricKey, AdapterMetric]] = {}
    duplicate_series: set[tuple[str, MetricKey]] = set()
    for stage, metrics in by_stage.items():
        indexed[stage] = {}
        for metric in metrics:
            key = (metric.metric_name, tuple(sorted(metric.labels.items())))
            if key in indexed[stage]:
                duplicate_series.add((stage, key))
            indexed[stage][key] = metric
    keys = set(indexed["before"]) | set(indexed["after"])
    present_names = {key[0] for key in keys}
    keys.update((name, ()) for name in set(expected_names) - present_names)

    for key in sorted(keys):
        name, label_pairs = key
        labels = dict(label_pairs)
        first = indexed["before"].get(key)
        last = indexed["after"].get(key)
        if ("before", key) in duplicate_series or ("after", key) in duplicate_series:
            warnings.append(
                (
                    "native_metric_duplicate",
                    f"{name} with labels {labels} was reported more than once in a snapshot.",
                )
            )
            observed = last or first
            rows.append(
                _metric(
                    run_id,
                    name,
                    None,
                    observed.unit if observed is not None else "unknown",
                    source=MetricSource.DERIVED,
                    aggregation="delta",
                    sample_count=int(first is not None) + int(last is not None),
                    definition="Native metric change across the measurement phase.",
                    formula="unavailable: a snapshot contains duplicate metric names",
                    quality_flags=["duplicate_metric_name"],
                    metric_kind=observed.kind if observed is not None else "gauge",
                    labels=labels,
                    adapter_id=adapter_id,
                    adapter_version=adapter_version,
                    engine_version=engine_version,
                )
            )
            continue
        if first is None or last is None:
            missing_stage = (
                "both" if first is None and last is None else "before" if first is None else "after"
            )
            warnings.append(
                ("native_metric_missing", f"{name} was missing from the {missing_stage} snapshot.")
            )
            observed = last or first
            rows.append(
                _metric(
                    run_id,
                    name,
                    None,
                    observed.unit if observed is not None else "unknown",
                    source=MetricSource.ENGINE_NATIVE,
                    aggregation="delta",
                    sample_count=0 if observed is None else 1,
                    definition="Native metric change across the measurement phase.",
                    formula="unavailable: a before or after observation is missing",
                    quality_flags=["missing_native_metric", f"missing_{missing_stage}_snapshot"],
                    metric_kind=observed.kind if observed is not None else "gauge",
                    labels=labels,
                    adapter_id=adapter_id,
                    adapter_version=adapter_version,
                    engine_version=engine_version,
                )
            )
            continue
        if (
            first.source is not MetricSource.ENGINE_NATIVE
            or last.source is not MetricSource.ENGINE_NATIVE
        ):
            rows.append(
                _metric(
                    run_id,
                    name,
                    None,
                    first.unit,
                    source=MetricSource.DERIVED,
                    aggregation="delta",
                    sample_count=2,
                    definition="Native metric change across the measurement phase.",
                    formula="unavailable: one or both observations have a non-native source",
                    quality_flags=["source_mismatch"],
                    metric_kind=first.kind,
                    adapter_id=adapter_id,
                    adapter_version=adapter_version,
                    engine_version=engine_version,
                    labels={
                        **labels,
                        "before_source": first.source.value,
                        "after_source": last.source.value,
                    },
                )
            )
            continue
        if first.unit != last.unit:
            warnings.append(
                ("native_metric_unit_mismatch", f"{name} changed units during the run.")
            )
            rows.append(
                _metric(
                    run_id,
                    name,
                    None,
                    first.unit,
                    source=MetricSource.DERIVED,
                    aggregation="delta",
                    sample_count=2,
                    definition="Native metric change across the measurement phase.",
                    formula="unavailable: before and after units do not match",
                    quality_flags=["unit_mismatch"],
                    metric_kind=first.kind,
                    adapter_id=adapter_id,
                    adapter_version=adapter_version,
                    engine_version=engine_version,
                    labels={**labels, "before_unit": first.unit, "after_unit": last.unit},
                )
            )
            continue
        if first.kind != last.kind:
            warnings.append(("native_metric_kind_mismatch", f"{name} changed gauge/counter kind."))
            rows.append(
                _metric(
                    run_id,
                    name,
                    None,
                    first.unit,
                    source=MetricSource.DERIVED,
                    aggregation="delta",
                    sample_count=2,
                    definition="Native metric change across the measurement phase.",
                    formula="unavailable: metric kind changed between snapshots",
                    quality_flags=["metric_kind_mismatch"],
                    metric_kind=first.kind,
                    labels=labels,
                    adapter_id=adapter_id,
                    adapter_version=adapter_version,
                    engine_version=engine_version,
                )
            )
            continue
        if first.kind == "counter":
            if first.value < 0 or last.value < 0:
                flags = ["invalid_negative_counter"]
                value = None
                warnings.append(("native_counter_invalid", f"{name} has a negative counter value."))
            elif last.value < first.value:
                flags = ["counter_reset"]
                value = None
                warnings.append(("native_counter_reset", f"{name} decreased during the run."))
            else:
                flags = []
                value = last.value - first.value
            rows.append(
                _metric(
                    run_id,
                    name,
                    value,
                    first.unit,
                    source=MetricSource.DERIVED,
                    aggregation="delta",
                    sample_count=2,
                    definition="Native counter change during the measurement phase.",
                    formula="after counter - before counter",
                    quality_flags=flags,
                    metric_kind="counter",
                    labels=labels,
                    adapter_id=adapter_id,
                    adapter_version=adapter_version,
                    engine_version=engine_version,
                )
            )
    return NativeMetricEvaluation(samples=rows, warnings=warnings)
