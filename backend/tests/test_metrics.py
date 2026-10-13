"""Metric formulas and quality safeguards use deterministic request fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from localforge.adapters.contracts import AdapterMetric
from localforge.core.models import (
    MetricSource,
    RequestOutcome,
    RequestSample,
    TokenCountSource,
)
from localforge.metrics import (
    PERCENTILE_MINIMUMS,
    aggregate_run_metrics,
    evaluate_native_metric_snapshots,
    native_metric_snapshot_samples,
)

RUN_ID = uuid4()
BASE_TIME = datetime(2026, 10, 12, tzinfo=UTC)


def _request(
    sequence: int,
    *,
    outcome: RequestOutcome = RequestOutcome.SUCCEEDED,
    phase: str = "measurement",
    concurrency_level: int | None = None,
    effective_concurrency: int | None = None,
    dispatch: float | None = 0,
    first_output: float | None = None,
    completed: float | None = 100,
    latency: float | None = 100,
    ttft: float | None = None,
    queue_time: float | None = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    input_source: TokenCountSource = TokenCountSource.UNAVAILABLE,
    output_source: TokenCountSource = TokenCountSource.UNAVAILABLE,
    inter_events: list[float] | None = None,
    inter_tokens: list[float] | None = None,
    wall_started: datetime | None = None,
    wall_completed: datetime | None = None,
    partial: bool = False,
) -> RequestSample:
    return RequestSample(
        run_id=RUN_ID,
        sequence_number=sequence,
        request_id=uuid4(),
        phase=phase,  # type: ignore[arg-type]
        concurrency_level=concurrency_level,
        effective_concurrency=effective_concurrency,
        outcome=outcome,
        started_at=wall_started or BASE_TIME,
        completed_at=wall_completed or BASE_TIME + timedelta(milliseconds=latency or 0),
        dispatch_offset_ms=dispatch,
        first_output_offset_ms=first_output,
        completed_offset_ms=completed,
        queue_time_ms=queue_time,
        latency_ms=latency,
        ttft_ms=ttft,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        prompt_token_count_source=input_source,
        completion_token_count_source=output_source,
        inter_event_latency_ms=inter_events or [],
        inter_token_latency_ms=inter_tokens or [],
        error_class=None if outcome is RequestOutcome.SUCCEEDED else outcome.value,
        partial_output_observed=partial,
    )


def _metric(metrics, name: str, aggregation: str | None = None):
    return next(
        row
        for row in metrics
        if row.metric_name == name and (aggregation is None or row.aggregation == aggregation)
    )


def test_aggregation_uses_documented_formulas_and_excludes_warmup() -> None:
    samples = [
        _request(
            0,
            dispatch=0,
            first_output=10,
            completed=100,
            latency=100,
            ttft=10,
            queue_time=1,
            input_tokens=20,
            output_tokens=4,
            input_source=TokenCountSource.ENGINE_EXACT,
            output_source=TokenCountSource.ENGINE_EXACT,
            inter_events=[5, 15],
        ),
        _request(
            1,
            dispatch=1_000,
            first_output=1_020,
            completed=1_100,
            latency=100,
            ttft=20,
            queue_time=2,
            input_tokens=10,
            output_tokens=2,
            input_source=TokenCountSource.TOKENIZER_EXACT,
            output_source=TokenCountSource.ENGINE_ESTIMATED,
            inter_events=[8],
        ),
        _request(
            2,
            dispatch=2_000,
            completed=2_050,
            latency=50,
            queue_time=3,
            input_tokens=0,
            output_tokens=0,
            input_source=TokenCountSource.ENGINE_EXACT,
            output_source=TokenCountSource.ENGINE_EXACT,
        ),
        _request(
            3,
            outcome=RequestOutcome.TIMED_OUT,
            dispatch=3_000,
            completed=3_300,
            latency=300,
            queue_time=4,
            input_tokens=5,
            input_source=TokenCountSource.ESTIMATED,
            inter_events=[4],
            partial=True,
        ),
        _request(
            4,
            outcome=RequestOutcome.CANCELLED,
            dispatch=4_000,
            completed=4_050,
            latency=50,
            queue_time=5,
            input_tokens=0,
            input_source=TokenCountSource.ENGINE_EXACT,
            partial=True,
        ),
        _request(
            5,
            phase="warmup",
            dispatch=0,
            completed=500_000,
            latency=500_000,
            input_tokens=999_999,
            output_tokens=999_999,
            input_source=TokenCountSource.ENGINE_EXACT,
            output_source=TokenCountSource.ENGINE_EXACT,
        ),
    ]

    metrics = aggregate_run_metrics(RUN_ID, iter(samples))

    assert samples[3].partial_output_observed and samples[4].partial_output_observed
    assert _metric(metrics, "request_latency_ms").value == pytest.approx(120)
    assert _metric(metrics, "request_latency_ms", "p50").value == pytest.approx(100)
    assert _metric(metrics, "ttft_ms").value == pytest.approx(15)
    assert _metric(metrics, "ttft_ms").sample_count == 2
    assert "ttft_only_when_observed" in _metric(metrics, "ttft_ms").quality_flags
    assert _metric(metrics, "queue_time_ms").value == pytest.approx(3)
    assert _metric(metrics, "inter_event_latency_ms").value == pytest.approx(8)
    assert _metric(metrics, "inter_token_latency_ms").value is None
    assert (
        "unavailable_token_timestamps_not_provided"
        in _metric(metrics, "inter_token_latency_ms").quality_flags
    )
    assert _metric(metrics, "tpot_ms_per_token").value == pytest.approx(30)
    assert _metric(metrics, "tpot_ms_per_token").sample_count == 1
    assert _metric(
        metrics, "input_throughput_tokens_per_second", "run_rate"
    ).value == pytest.approx(35 / 4.05)
    assert (
        "includes_estimated_token_counts"
        in _metric(metrics, "input_throughput_tokens_per_second", "run_rate").quality_flags
    )
    assert _metric(
        metrics, "output_throughput_tokens_per_second", "run_rate"
    ).value == pytest.approx(6 / 4.05)
    assert _metric(metrics, "output_throughput_tokens_per_second", "run_rate").sample_count == 3
    assert _metric(
        metrics, "generation_throughput_tokens_per_second", "run_rate"
    ).value == pytest.approx(6 / 0.17)
    assert _metric(metrics, "requests_attempted").value == 5
    assert _metric(metrics, "requests_succeeded").value == 3
    assert _metric(metrics, "requests_timed_out").value == 1
    assert _metric(metrics, "requests_cancelled").value == 1
    assert _metric(metrics, "error_rate_percent", "run_rate").value == pytest.approx(40)
    assert "R-7" in _metric(metrics, "request_latency_ms", "p50").formula


def test_percentiles_require_minimum_counts_and_use_r7_interpolation() -> None:
    samples = [
        _request(index, dispatch=index * 100, completed=index * 100 + value, latency=value)
        for index, value in enumerate(range(1, 21))
    ]

    metrics = aggregate_run_metrics(RUN_ID, samples)

    assert PERCENTILE_MINIMUMS == {"p50": 1, "p90": 10, "p95": 20, "p99": 100}
    assert _metric(metrics, "request_latency_ms", "p50").value == pytest.approx(10.5)
    assert _metric(metrics, "request_latency_ms", "p90").value == pytest.approx(18.1)
    assert _metric(metrics, "request_latency_ms", "p95").value == pytest.approx(19.05)
    p99 = _metric(metrics, "request_latency_ms", "p99")
    assert p99.value is None
    assert p99.sample_count == 20
    assert "insufficient_samples_for_stable_tail" in p99.quality_flags


def test_sweep_metrics_are_grouped_by_concurrency_level() -> None:
    samples = [
        _request(0, concurrency_level=1, effective_concurrency=1, dispatch=0, completed=100),
        _request(1, concurrency_level=1, effective_concurrency=1, dispatch=1_000, completed=1_100),
        _request(2, concurrency_level=2, effective_concurrency=2, dispatch=2_000, completed=2_100),
        _request(3, concurrency_level=2, effective_concurrency=2, dispatch=2_100, completed=2_200),
    ]

    metrics = aggregate_run_metrics(RUN_ID, samples)
    throughput = [
        metric
        for metric in metrics
        if metric.metric_name == "request_throughput_requests_per_second"
    ]
    by_level = {metric.labels["concurrency_level"]: metric for metric in throughput}

    assert set(by_level) == {"1", "2"}
    assert by_level["1"].value == pytest.approx(2 / 1.1)
    assert by_level["2"].value == pytest.approx(10)
    assert by_level["1"].labels["effective_concurrency"] == "1"
    assert by_level["2"].labels["effective_concurrency"] == "2"


def test_missing_timestamps_and_interrupted_requests_stay_visible() -> None:
    metrics = aggregate_run_metrics(
        RUN_ID,
        [
            _request(
                0,
                outcome=RequestOutcome.INTERRUPTED,
                dispatch=None,
                completed=None,
                latency=None,
                first_output=None,
            )
        ],
    )

    assert _metric(metrics, "request_latency_ms").value is None
    assert "incomplete_request_samples" in _metric(metrics, "request_latency_ms").quality_flags
    assert _metric(metrics, "ttft_ms").value is None
    assert _metric(metrics, "output_throughput_tokens_per_second", "run_rate").value is None
    assert _metric(metrics, "requests_attempted").value == 1
    assert _metric(metrics, "requests_interrupted").value == 1
    assert _metric(metrics, "error_rate_percent", "run_rate").value == 100


def test_error_breakdown_keeps_failure_and_disconnect_categories_separate() -> None:
    outcomes = [
        RequestOutcome.FAILED,
        RequestOutcome.DISCONNECTED,
        RequestOutcome.TIMED_OUT,
        RequestOutcome.CANCELLED,
        RequestOutcome.INTERRUPTED,
    ]
    samples = [
        _request(
            index,
            outcome=outcome,
            dispatch=index * 100,
            completed=index * 100 + 25,
            latency=25,
        )
        for index, outcome in enumerate(outcomes)
    ]

    metrics = aggregate_run_metrics(RUN_ID, samples)

    assert _metric(metrics, "requests_attempted").value == 5
    assert _metric(metrics, "requests_failed").value == 1
    assert _metric(metrics, "requests_disconnected").value == 1
    assert _metric(metrics, "requests_timed_out").value == 1
    assert _metric(metrics, "requests_cancelled").value == 1
    assert _metric(metrics, "requests_interrupted").value == 1
    assert _metric(metrics, "error_rate_percent", "run_rate").value == 100


def test_token_throughput_is_unavailable_when_request_usage_is_incomplete() -> None:
    samples = [
        _request(
            0,
            dispatch=0,
            first_output=10,
            completed=100,
            latency=100,
            input_tokens=4,
            output_tokens=2,
            input_source=TokenCountSource.ENGINE_EXACT,
            output_source=TokenCountSource.ENGINE_EXACT,
        ),
        _request(
            1,
            dispatch=1_000,
            first_output=1_010,
            completed=1_100,
            latency=100,
        ),
    ]

    metrics = aggregate_run_metrics(RUN_ID, samples)

    for name in (
        "input_throughput_tokens_per_second",
        "output_throughput_tokens_per_second",
    ):
        metric = _metric(metrics, name, "run_rate")
        assert metric.value is None
        assert metric.sample_count == 1
        assert "incomplete_token_count_coverage" in metric.quality_flags


def test_run_throughput_is_unavailable_when_an_interrupted_request_has_no_end_offset() -> None:
    samples = [
        _request(
            0,
            dispatch=0,
            first_output=10,
            completed=100,
            latency=100,
            input_tokens=4,
            output_tokens=2,
            input_source=TokenCountSource.ENGINE_EXACT,
            output_source=TokenCountSource.ENGINE_EXACT,
        ),
        _request(
            1,
            outcome=RequestOutcome.INTERRUPTED,
            dispatch=1_000,
            first_output=None,
            completed=None,
            latency=None,
        ),
    ]

    metrics = aggregate_run_metrics(RUN_ID, samples)

    for name in (
        "request_throughput_requests_per_second",
        "input_throughput_tokens_per_second",
        "output_throughput_tokens_per_second",
    ):
        metric = _metric(metrics, name, "run_rate")
        assert metric.value is None
        assert "incomplete_measurement_window" in metric.quality_flags


def test_monotonic_measurements_ignore_wall_clock_adjustments() -> None:
    ordinary = [
        _request(0, dispatch=0, completed=100, latency=100),
        _request(1, dispatch=1_000, completed=1_200, latency=200),
    ]
    clock_adjusted = [
        _request(
            0,
            dispatch=0,
            completed=100,
            latency=100,
            wall_started=BASE_TIME,
            wall_completed=BASE_TIME - timedelta(days=3),
        ),
        _request(
            1,
            dispatch=1_000,
            completed=1_200,
            latency=200,
            wall_started=BASE_TIME - timedelta(days=3),
            wall_completed=BASE_TIME - timedelta(days=3, seconds=1),
        ),
    ]

    regular_metrics = aggregate_run_metrics(RUN_ID, ordinary)
    adjusted_metrics = aggregate_run_metrics(RUN_ID, clock_adjusted)
    regular_values = [(row.metric_name, row.aggregation, row.value) for row in regular_metrics]
    adjusted_values = [(row.metric_name, row.aggregation, row.value) for row in adjusted_metrics]

    assert adjusted_values == regular_values
    assert _metric(adjusted_metrics, "request_latency_ms").value == pytest.approx(150)


def test_native_metric_reset_unit_mismatch_missing_and_invalid_counter() -> None:
    before = [
        AdapterMetric(
            metric_name="engine.requests",
            value=100,
            unit="requests",
            source=MetricSource.ENGINE_NATIVE,
            kind="counter",
        ),
        AdapterMetric(
            metric_name="engine.memory",
            value=4,
            unit="GiB",
            source=MetricSource.ENGINE_NATIVE,
        ),
        AdapterMetric(
            metric_name="engine.bad_counter",
            value=4,
            unit="requests",
            source=MetricSource.ENGINE_NATIVE,
            kind="counter",
        ),
    ]
    after = [
        AdapterMetric(
            metric_name="engine.requests",
            value=90,
            unit="requests",
            source=MetricSource.ENGINE_NATIVE,
            kind="counter",
        ),
        AdapterMetric(
            metric_name="engine.memory",
            value=5,
            unit="MiB",
            source=MetricSource.ENGINE_NATIVE,
        ),
        AdapterMetric(
            metric_name="engine.bad_counter",
            value=-1,
            unit="requests",
            source=MetricSource.ENGINE_NATIVE,
            kind="counter",
        ),
        AdapterMetric(
            metric_name="engine.new_counter",
            value=2,
            unit="requests",
            source=MetricSource.ENGINE_NATIVE,
            kind="counter",
        ),
    ]

    evaluation = evaluate_native_metric_snapshots(
        RUN_ID,
        before,
        after,
        expected_names={"engine.requests", "engine.memory", "engine.bad_counter", "engine.absent"},
        adapter_id="test.adapter",
        adapter_version="1.2",
        engine_version="engine-x",
    )
    rows = {row.metric_name: row for row in evaluation.samples}

    assert rows["engine.requests"].value is None
    assert "counter_reset" in rows["engine.requests"].quality_flags
    assert "unit_mismatch" in rows["engine.memory"].quality_flags
    assert "invalid_negative_counter" in rows["engine.bad_counter"].quality_flags
    assert "missing_before_snapshot" in rows["engine.new_counter"].quality_flags
    assert "missing_both_snapshot" in rows["engine.absent"].quality_flags
    assert rows["engine.requests"].adapter_id == "test.adapter"
    assert rows["engine.requests"].engine_version == "engine-x"
    assert {code for code, _ in evaluation.warnings} >= {
        "native_counter_reset",
        "native_metric_unit_mismatch",
        "native_counter_invalid",
        "native_metric_missing",
    }


def test_native_raw_snapshot_preserves_provenance_and_flags_bad_source() -> None:
    metric = AdapterMetric(
        metric_name="engine.requests",
        value=9,
        unit="requests",
        source=MetricSource.CLIENT_MEASURED,
        kind="counter",
        labels={"pool": "main"},
    )

    snapshot = native_metric_snapshot_samples(
        RUN_ID,
        [metric],
        expected_names={"engine.absent"},
        stage="before",
        adapter_id="test.adapter",
        adapter_version="1.2",
        engine_version="engine-x",
    )

    stored = next(row for row in snapshot.samples if row.metric_name == "engine.requests")
    missing = next(row for row in snapshot.samples if row.metric_name == "engine.absent")
    assert stored.value == 9
    assert stored.source is MetricSource.CLIENT_MEASURED
    assert stored.labels == {"pool": "main", "snapshot": "before"}
    assert stored.adapter_id == "test.adapter"
    assert stored.engine_version == "engine-x"
    assert "source_mismatch" in stored.quality_flags
    assert missing.value is None
    assert "missing_before_snapshot" in missing.quality_flags
    assert any(code == "native_metric_source_mismatch" for code, _ in snapshot.warnings)


def test_native_counter_delta_rejects_duplicate_names_and_source_mismatch() -> None:
    def native(name: str, value: float, *, source: MetricSource = MetricSource.ENGINE_NATIVE):
        return AdapterMetric(
            metric_name=name,
            value=value,
            unit="requests",
            source=source,
            kind="counter",
        )

    evaluation = evaluate_native_metric_snapshots(
        RUN_ID,
        [
            native("engine.duplicate", 4),
            native("engine.duplicate", 5),
            native("engine.wrong_source", 1, source=MetricSource.CLIENT_MEASURED),
        ],
        [
            native("engine.duplicate", 10),
            native("engine.wrong_source", 2, source=MetricSource.CLIENT_MEASURED),
        ],
        expected_names=(),
        adapter_id="test.adapter",
        adapter_version="1.2",
        engine_version="engine-x",
    )
    rows = {row.metric_name: row for row in evaluation.samples}

    assert rows["engine.duplicate"].value is None
    assert "duplicate_metric_name" in rows["engine.duplicate"].quality_flags
    assert rows["engine.wrong_source"].value is None
    assert "source_mismatch" in rows["engine.wrong_source"].quality_flags


def test_native_counter_delta_tracks_labeled_prometheus_series_independently() -> None:
    def native(value: float, position: str) -> AdapterMetric:
        return AdapterMetric(
            metric_name="engine.accepted_tokens_total",
            value=value,
            unit="tokens",
            source=MetricSource.ENGINE_NATIVE,
            kind="counter",
            labels={"position": position},
        )

    before = [native(4, "0"), native(9, "1")]
    after = [native(7, "0"), native(15, "1")]
    raw = native_metric_snapshot_samples(
        RUN_ID,
        before,
        expected_names={"engine.accepted_tokens_total"},
        stage="before",
        adapter_id="test.adapter",
        adapter_version="1.2",
        engine_version="engine-x",
    )
    evaluation = evaluate_native_metric_snapshots(
        RUN_ID,
        before,
        after,
        expected_names={"engine.accepted_tokens_total"},
        adapter_id="test.adapter",
        adapter_version="1.2",
        engine_version="engine-x",
    )

    assert not any(code == "native_metric_duplicate" for code, _ in raw.warnings)
    assert {row.labels["position"] for row in raw.samples} == {"0", "1"}
    assert not evaluation.warnings
    assert {(row.labels["position"], row.value) for row in evaluation.samples} == {
        ("0", 3),
        ("1", 6),
    }
