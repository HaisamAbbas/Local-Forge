"""Use-case presets, plan targets, run verdicts, prefill estimates, and quality checks."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.fake import FakeEngineAdapter, FakeEngineScenario
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkPlanStatus,
    BenchmarkRun,
    BenchmarkRunStatus,
    EngineIntegrationMode,
    EngineProfile,
    EngineProfileStatus,
    MetricSample,
    MetricSource,
    QualityChecks,
    RequestOutcome,
    RequestSample,
    RunTargets,
    Workload,
    WorkloadInputMode,
    WorkloadKind,
    WorkloadPreset,
    WorkloadStatus,
)
from localforge.quality import (
    QUALITY_METHOD_VERSION,
    QUALITY_SMOKE_ITEMS,
    answers_match,
    ensure_quality_smoke_dataset,
    evaluate_output,
    quality_pass_rate_metric,
)
from localforge.runs import create_queued_run
from localforge.storage.database import initialize_database, open_database
from localforge.storage.repositories import (
    MetricSampleRepository,
    RecordRepository,
    RequestSampleRepository,
)
from localforge.targets import (
    USE_CASE_PRESETS,
    evaluate_verdict,
    format_duration_ms,
)
from localforge.worker.execution import execute_run
from tests.test_workload_api import _authorize, _client, _ready_engine

USE_CASE_IDS = [item.preset.value for item in USE_CASE_PRESETS]


# --- Presets ----------------------------------------------------------------------------------


def test_every_use_case_preset_creates_a_valid_workload_with_its_shape(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        listed = client.get("/api/v1/workload-presets/use-cases")
        assert listed.status_code == 200, listed.text
        catalog = {item["preset"]: item for item in listed.json()}
        assert set(catalog) == set(USE_CASE_IDS)
        for preset in USE_CASE_IDS:
            assert catalog[preset]["description"]
            assert catalog[preset]["default_targets"]
            created = client.post(
                "/api/v1/workloads/presets", json={"name": f"{preset} fixture", "preset": preset}
            )
            assert created.status_code == 201, created.text
            workload = created.json()
            assert workload["status"] == "ready"
            assert workload["kind"] == "chat"
            assert workload["request_count"] == catalog[preset]["request_count"]
            assert workload["concurrency"] == catalog[preset]["concurrency"]
            assert workload["target_output_tokens"] == catalog[preset]["output_tokens"]
            assert workload["target_input_tokens"] == catalog[preset]["input_tokens"]

        coding = catalog["coding_assistant"]
        assert coding["input_tokens"] == 8192 and coding["output_tokens"] == 300
        assert catalog["batch_summarization"]["concurrency"] == 4
        assert catalog["long_context_stress"]["input_tokens"] == 16384
        assert catalog["interactive_chat"]["default_targets"]["ttft_p50_ms_max"] == 1000

        overridden = client.post(
            "/api/v1/workloads/presets",
            json={
                "name": "Small coding",
                "preset": "coding_assistant",
                "request_count": 2,
                "concurrency": 2,
                "max_output_tokens": 64,
            },
        )
        assert overridden.status_code == 201, overridden.text
        assert overridden.json()["request_count"] == 2
        assert overridden.json()["concurrency"] == 2
        assert overridden.json()["target_output_tokens"] == 64


def test_quality_smoke_preset_uses_a_reusable_builtin_dataset_with_expected_answers(
    tmp_path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        first = client.post(
            "/api/v1/workloads/presets", json={"name": "Smoke", "preset": "quality_smoke"}
        )
        second = client.post(
            "/api/v1/workloads/presets", json={"name": "Smoke again", "preset": "quality_smoke"}
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        workload = first.json()
        assert workload["input_mode"] == "dataset"
        assert workload["quality_checks"]["exact_match"] is True
        assert workload["dataset_id"] == second.json()["dataset_id"]
        datasets = client.get("/api/v1/datasets").json()
        assert len(datasets) == 1
        dataset = datasets[0]
        assert dataset["source"] == "built_in_preset"
        assert dataset["item_count"] == len(QUALITY_SMOKE_ITEMS) == 20
        assert dataset["expected_answer_field"] == "expected_answer"
        rows = (tmp_path / dataset["storage_ref"]).read_text(encoding="utf-8").splitlines()
        assert all(json.loads(row)["expected_answer"] for row in rows)


def test_exact_match_checks_require_dataset_input(tmp_path) -> None:
    with pytest.raises(ValidationError, match="exact-match"):
        Workload(
            name="Synthetic exact",
            kind=WorkloadKind.CHAT,
            input_mode=WorkloadInputMode.SYNTHETIC,
            quality_checks=QualityChecks(exact_match=True),
        )
    with pytest.raises(ValidationError, match="valid_json"):
        QualityChecks(json_required_keys=["a"])
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        rejected = client.post(
            "/api/v1/workloads",
            json={
                "name": "Exact without data",
                "kind": "chat",
                "quality_checks": {"exact_match": True},
            },
        )
        assert rejected.status_code == 422


# --- Targets persistence ----------------------------------------------------------------------


def _plan(client, engine_id: str, model_id: str, workload_id: str, **extra: object):
    response = client.post(
        "/api/v1/benchmark-plans",
        json={
            "name": "Targets plan",
            "engine_profile_id": engine_id,
            "model_artifact_id": model_id,
            "workload_id": workload_id,
        }
        | extra,
    )
    assert response.status_code == 201, response.text
    return response.json()["plan"]


def test_plan_targets_default_from_use_case_and_can_be_edited_cleared_and_exported(
    tmp_path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        workload_id = client.post(
            "/api/v1/workloads/presets",
            json={"name": "Coding", "preset": "coding_assistant", "request_count": 2},
        ).json()["id"]

        defaulted = _plan(client, engine_id, model_id, workload_id)
        assert defaulted["targets"]["ttft_p50_ms_max"] == 5000
        assert defaulted["configuration_snapshot"]["targets"] == defaulted["targets"]

        cleared = _plan(client, engine_id, model_id, workload_id, targets=None)
        assert cleared["targets"] is None

        custom = _plan(client, engine_id, model_id, workload_id, targets={"ttft_p50_ms_max": 60000})
        assert custom["targets"]["ttft_p50_ms_max"] == 60000
        assert custom["targets"]["error_rate_percent_max"] is None

        invalid = client.post(
            "/api/v1/benchmark-plans",
            json={
                "name": "Bad targets",
                "engine_profile_id": engine_id,
                "workload_id": workload_id,
                "targets": {"error_rate_percent_max": 150},
            },
        )
        assert invalid.status_code == 422
        negative = client.put(
            f"/api/v1/benchmark-plans/{custom['id']}/targets",
            json={"targets": {"ttft_p50_ms_max": -1}},
        )
        assert negative.status_code == 422

        updated = client.put(
            f"/api/v1/benchmark-plans/{custom['id']}/targets",
            json={"targets": {"quality_pass_rate_min": 90, "request_throughput_min": 0.5}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["targets"]["quality_pass_rate_min"] == 90
        assert updated.json()["targets"]["ttft_p50_ms_max"] is None
        assert updated.json()["configuration_snapshot"]["targets"]["request_throughput_min"] == 0.5

        exported = client.get(f"/api/v1/benchmark-plans/{custom['id']}/export")
        assert exported.json()["plan"]["targets"]["quality_pass_rate_min"] == 90
        imported = client.post("/api/v1/benchmark-plans/import", json=exported.json())
        assert imported.status_code == 201, imported.text
        assert imported.json()["plan"]["targets"]["quality_pass_rate_min"] == 90

        cloned = client.post(f"/api/v1/benchmark-plans/{custom['id']}/clone", json={})
        assert cloned.status_code == 201, cloned.text
        assert cloned.json()["plan"]["targets"]["request_throughput_min"] == 0.5

        removed = client.put(
            f"/api/v1/benchmark-plans/{custom['id']}/targets", json={"targets": None}
        )
        assert removed.json()["targets"] is None
        assert removed.json()["configuration_snapshot"]["targets"] is None
        missing = client.put(f"/api/v1/benchmark-plans/{uuid4()}/targets", json={"targets": None})
        assert missing.status_code == 404


# --- Verdicts ---------------------------------------------------------------------------------


def _verdict_plan(targets: RunTargets | None, input_tokens: int | None = 8192) -> BenchmarkPlan:
    return BenchmarkPlan(
        name="Verdict plan",
        engine_profile_id=uuid4(),
        workload_id=uuid4(),
        targets=targets,
        configuration_snapshot={"workload": {"target_input_tokens": input_tokens}},
    )


def _metric(
    run_id: UUID,
    name: str,
    value: float | None,
    *,
    aggregation: str = "run_rate",
    sample_count: int = 3,
    flags: list[str] | None = None,
    source: MetricSource = MetricSource.DERIVED,
    labels: dict[str, str] | None = None,
) -> MetricSample:
    return MetricSample(
        run_id=run_id,
        metric_name=name,
        value=value,
        unit="x",
        source=source,
        aggregation=aggregation,
        sample_count=sample_count,
        quality_flags=flags or [],
        labels=labels or {},
    )


def _finished_run(plan: BenchmarkPlan) -> BenchmarkRun:
    return BenchmarkRun(plan_id=plan.id, status=BenchmarkRunStatus.COMPLETED)


def test_verdict_passes_fails_and_explains_misses_with_the_runs_own_numbers() -> None:
    plan = _verdict_plan(
        RunTargets(
            ttft_p50_ms_max=5000,
            generation_tokens_per_second_min=10,
            error_rate_percent_max=1,
        )
    )
    run = _finished_run(plan)
    metrics = [
        _metric(
            run.id,
            "ttft_ms",
            58_000,
            aggregation="p50",
            source=MetricSource.CLIENT_MEASURED,
        ),
        _metric(run.id, "generation_throughput_tokens_per_second", 19.6),
        _metric(run.id, "error_rate_percent", 0, sample_count=3),
        _metric(run.id, "requests_attempted", 3, aggregation="run_total"),
        _metric(
            run.id,
            "ollama.prompt_eval_rate_tokens_per_second",
            140,
            source=MetricSource.DERIVED,
        ),
    ]
    verdict = evaluate_verdict(run, plan, metrics, [])
    assert verdict.overall == "misses_targets"
    assert verdict.headline == "Misses 1 target"
    by_target = {item.target: item for item in verdict.evaluations}
    ttft = by_target["ttft_p50_ms_max"]
    assert ttft.status == "fail"
    assert ttft.observed == 58_000
    assert ttft.explanation.startswith("First token took 58 s at p50 against a 5.0 s target.")
    assert "~140 tokens/s" in ttft.explanation
    assert "so an 8,192-token prompt needs about a minute" in ttft.explanation
    assert "smaller context, a GPU engine, or a smaller model" in ttft.explanation
    assert by_target["generation_tokens_per_second_min"].status == "pass"
    assert "20 tokens/s" in by_target["generation_tokens_per_second_min"].explanation
    assert by_target["error_rate_percent_max"].status == "pass"
    assert verdict.summary.startswith("2 of 3 targets met, 1 missed.")


def test_verdict_meets_targets_when_every_target_passes() -> None:
    plan = _verdict_plan(RunTargets(ttft_p50_ms_max=2000, error_rate_percent_max=5))
    run = _finished_run(plan)
    metrics = [
        _metric(run.id, "ttft_ms", 800, aggregation="p50", sample_count=10),
        _metric(run.id, "error_rate_percent", 0, sample_count=10),
    ]
    verdict = evaluate_verdict(run, plan, metrics, [])
    assert verdict.overall == "meets_targets"
    assert verdict.headline == "Meets your targets"
    assert verdict.passed == 2 and verdict.failed == 0 and verdict.unknown == 0


def test_missing_metrics_and_too_few_samples_are_unknown_never_pass_or_zero() -> None:
    plan = _verdict_plan(
        RunTargets(
            ttft_p95_ms_max=10_000,
            generation_tokens_per_second_min=5,
            quality_pass_rate_min=80,
            request_throughput_min=0.1,
        )
    )
    run = _finished_run(plan)
    metrics = [
        # p95 needs 20 samples; the metric is null with a quality flag.
        _metric(
            run.id,
            "ttft_ms",
            None,
            aggregation="p95",
            sample_count=3,
            flags=["insufficient_samples_for_stable_tail"],
        ),
        # Token counts were missing: value is null, never zero.
        _metric(
            run.id,
            "generation_throughput_tokens_per_second",
            None,
            sample_count=0,
            flags=["unavailable_token_counts_missing"],
        ),
        # A 100% pass rate over 2 outputs is too few to judge.
        _metric(run.id, "quality_pass_rate_percent", 100, sample_count=2),
        # request_throughput_requests_per_second is absent altogether.
    ]
    verdict = evaluate_verdict(run, plan, metrics, [])
    assert verdict.overall == "not_enough_data"
    assert verdict.headline == "Not enough data"
    assert {item.status for item in verdict.evaluations} == {"unknown"}
    by_target = {item.target: item for item in verdict.evaluations}
    assert "at least 20 measured requests" in by_target["ttft_p95_ms_max"].explanation
    assert "this run has 3" in by_target["ttft_p95_ms_max"].explanation
    assert "token counts" in by_target["generation_tokens_per_second_min"].explanation
    assert "Only 2 outputs" in by_target["quality_pass_rate_min"].explanation
    assert by_target["request_throughput_min"].observed is None
    assert "did not record" in by_target["request_throughput_min"].explanation


def test_unknown_does_not_hide_a_definite_miss_and_unfinished_runs_wait() -> None:
    plan = _verdict_plan(RunTargets(ttft_p50_ms_max=1000, ttft_p95_ms_max=2000))
    run = _finished_run(plan)
    metrics = [_metric(run.id, "ttft_ms", 1500, aggregation="p50", sample_count=1)]
    verdict = evaluate_verdict(run, plan, metrics, [])
    assert verdict.overall == "misses_targets"
    assert verdict.failed == 1 and verdict.unknown == 1
    assert "Based on only 1 measured requests" in verdict.evaluations[0].explanation
    assert "did not report a prompt-processing rate" in verdict.evaluations[0].explanation

    running = BenchmarkRun(plan_id=plan.id, status=BenchmarkRunStatus.RUNNING)
    pending = evaluate_verdict(running, plan, metrics, [])
    assert pending.overall == "not_enough_data"
    assert all(item.explanation == "The run has not finished yet." for item in pending.evaluations)

    untargeted = evaluate_verdict(run, _verdict_plan(None), metrics, [])
    assert untargeted.overall == "no_targets"
    assert untargeted.evaluations == []


def test_run_snapshot_targets_win_over_later_plan_edits_and_sweeps_use_the_top_level() -> None:
    plan = _verdict_plan(RunTargets(ttft_p50_ms_max=100))
    run = BenchmarkRun(
        plan_id=plan.id,
        status=BenchmarkRunStatus.COMPLETED,
        profile_snapshot={"targets": {"ttft_p50_ms_max": 5000}},
    )
    metrics = [
        _metric(run.id, "ttft_ms", 900, aggregation="p50", labels={"concurrency_level": "1"}),
        _metric(run.id, "ttft_ms", 4000, aggregation="p50", labels={"concurrency_level": "8"}),
    ]
    verdict = evaluate_verdict(run, plan, metrics, [])
    assert verdict.target_source == "run_plan_snapshot"
    assert verdict.concurrency_level == "8"
    assert verdict.evaluations[0].observed == 4000
    assert verdict.overall == "meets_targets"


def test_quality_and_error_misses_quote_counts_and_reasons() -> None:
    plan = _verdict_plan(RunTargets(quality_pass_rate_min=90, error_rate_percent_max=5))
    run = _finished_run(plan)
    metrics = [
        *quality_pass_rate_metric(run.id, ["pass"] * 6 + ["fail"] * 4 + ["unknown"]),
        _metric(run.id, "error_rate_percent", 20, sample_count=10),
        _metric(run.id, "requests_attempted", 10, aggregation="run_total", sample_count=10),
    ]
    now = datetime.now(UTC)
    samples = [
        RequestSample(
            run_id=run.id,
            sequence_number=index,
            outcome=RequestOutcome.SUCCEEDED,
            started_at=now,
            quality_outcome="fail",
            quality_reasons=["exact_match_mismatch"],
        )
        for index in range(4)
    ]
    verdict = evaluate_verdict(run, plan, metrics, samples)
    by_target = {item.target: item for item in verdict.evaluations}
    quality = by_target["quality_pass_rate_min"]
    assert quality.status == "fail"
    assert quality.explanation.startswith("6 of 10 checked outputs passed (60%)")
    assert "answer did not match the expected answer (4)" in quality.explanation
    assert by_target["error_rate_percent_max"].explanation.startswith(
        "2 of 10 requests did not complete (20%)"
    )
    assert verdict.quality_method_version == QUALITY_METHOD_VERSION


def test_duration_formatting() -> None:
    assert format_duration_ms(850) == "850 ms"
    assert format_duration_ms(2500) == "2.5 s"
    assert format_duration_ms(58_000) == "58 s"
    assert format_duration_ms(180_000) == "3.0 min"


# --- Quality checks ---------------------------------------------------------------------------


def _check(checks: QualityChecks, output: str, **values: object):
    arguments: dict[str, object] = {
        "expected_answer": None,
        "request_succeeded": True,
        "completion_tokens": 10,
        "max_tokens": 64,
    } | values
    return evaluate_output(checks, output=output, **arguments)  # type: ignore[arg-type]


def test_exact_match_normalizes_answers_and_compares_json_structurally() -> None:
    assert answers_match("Paris.", "Paris")
    assert answers_match("  `12` ", "12")
    assert answers_match('```json\n{"a": 1, "b": 2}\n```', '{"b": 2, "a": 1}')
    assert not answers_match("The answer is 12", "12")
    assert not answers_match('{"a": 2}', '{"a": 1}')
    checks = QualityChecks(exact_match=True)
    assert _check(checks, "Tokyo", expected_answer="Tokyo").outcome == "pass"
    failed = _check(checks, "Kyoto", expected_answer="Tokyo")
    assert failed.outcome == "fail" and failed.reasons == ("exact_match_mismatch",)
    missing = _check(checks, "Tokyo", expected_answer=None)
    assert missing.outcome == "unknown" and missing.reasons == ("expected_answer_missing",)


def test_json_contains_length_and_finish_checks() -> None:
    json_checks = QualityChecks(valid_json=True, json_required_keys=["city"])
    assert _check(json_checks, '{"city": "Paris"}').outcome == "pass"
    assert _check(json_checks, "not json").reasons == ("invalid_json",)
    assert _check(json_checks, "[1, 2]").reasons == ("json_not_object",)
    assert _check(json_checks, '{"town": "Paris"}').reasons == ("json_missing_required_key",)

    text_checks = QualityChecks(must_contain=["def "], must_not_contain=["TODO"])
    assert _check(text_checks, "def f(): pass").outcome == "pass"
    assert _check(text_checks, "lambda: 1").reasons == ("required_text_missing",)
    assert _check(text_checks, "def f(): todo").reasons == ("forbidden_text_present",)
    sensitive = QualityChecks(must_contain=["Paris"], case_sensitive=True)
    assert _check(sensitive, "paris").outcome == "fail"

    length = QualityChecks(max_output_characters=5)
    assert _check(length, "123456").reasons == ("output_too_long",)

    finish = QualityChecks(stopped_properly=True)
    assert _check(finish, "x", completion_tokens=10, max_tokens=64).outcome == "pass"
    cut = _check(finish, "x", completion_tokens=64, max_tokens=64)
    assert cut.outcome == "fail" and cut.reasons == ("stopped_at_length_limit",)
    unknown = _check(finish, "x", completion_tokens=None)
    assert unknown.outcome == "unknown" and unknown.reasons == ("stop_reason_unknown",)

    failed_request = _check(finish, "", request_succeeded=False)
    assert failed_request.outcome == "unknown"
    assert failed_request.reasons == ("request_not_completed",)


def test_pass_rate_metric_excludes_unchecked_outputs_and_records_method() -> None:
    run_id = uuid4()
    assert quality_pass_rate_metric(run_id, [None, None]) == []
    [metric] = quality_pass_rate_metric(run_id, ["pass", "pass", "fail", "unknown"])
    assert metric.metric_name == "quality_pass_rate_percent"
    assert metric.value == pytest.approx(200 / 3)
    assert metric.sample_count == 3
    assert metric.source is MetricSource.DERIVED
    assert metric.method_version == QUALITY_METHOD_VERSION
    assert "some_outputs_not_evaluated" in metric.quality_flags
    [empty] = quality_pass_rate_metric(run_id, ["unknown"])
    assert empty.value is None and "unavailable_no_evaluated_outputs" in empty.quality_flags


# --- Worker integration (retention off) -------------------------------------------------------


def _worker_run(
    tmp_path, workload: Workload, *, chunks: tuple[str, ...], dataset_hash: str | None = None
) -> tuple[BenchmarkRun, Path]:
    database_path = tmp_path / "quality.sqlite3"
    connection = open_database(database_path)
    try:
        engine = EngineProfile(
            label="Fake target",
            adapter_id="localforge.fake",
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            status=EngineProfileStatus.ENABLED,
            selected_model_ref="fake://deterministic-model",
        )
        plan = BenchmarkPlan(
            name="Quality plan",
            status=BenchmarkPlanStatus.READY,
            engine_profile_id=engine.id,
            workload_id=workload.id,
            measured_requests=workload.request_count,
            timeout_seconds=20,
            retain_prompt_output=False,
            dataset_snapshot_hash=dataset_hash,
            targets=RunTargets(quality_pass_rate_min=50),
        )
        for record_type, record in (
            (EngineProfile, engine),
            (Workload, workload),
            (BenchmarkPlan, plan),
        ):
            RecordRepository(connection, record_type).create(record)
        run, _ = create_queued_run(
            connection,
            BenchmarkRun(plan_id=plan.id, worker_id=str(uuid4())),
            app_instance_id="test-app",
            resource_keys=[("endpoint", str(engine.id))],
            override_contention=False,
        )
    finally:
        connection.close()
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(FakeEngineScenario(chunks=chunks)))
    result = asyncio.run(
        execute_run(
            run_id=run.id,
            database_path=database_path,
            data_directory=tmp_path,
            app_instance_id="test-app",
            worker_id=run.worker_id or "",
            catalog_factory=lambda: catalog,
        )
    )
    assert result == 0
    return run, database_path


def test_worker_checks_outputs_in_memory_and_never_stores_them_without_retention(
    tmp_path,
) -> None:
    database_path = tmp_path / "quality.sqlite3"
    initialize_database(database_path)
    workload = Workload(
        name="JSON quality",
        kind=WorkloadKind.CHAT,
        status=WorkloadStatus.READY,
        input_mode=WorkloadInputMode.SYNTHETIC,
        request_count=3,
        max_output_tokens=16,
        target_input_tokens=4,
        target_output_tokens=16,
        quality_checks=QualityChecks(
            valid_json=True,
            json_required_keys=["marker"],
            must_contain=["zebra"],
            must_not_contain=["forbidden"],
            stopped_properly=True,
        ),
    )
    run, path = _worker_run(tmp_path, workload, chunks=('{"marker":', ' "zebra-secret-output"}'))
    connection = open_database(path)
    try:
        samples = RequestSampleRepository(connection).list_for_run(run.id)
        measured = [sample for sample in samples if sample.phase == "measurement"]
        assert len(measured) == 3
        assert {sample.quality_outcome for sample in measured} == {"pass"}
        assert all(
            sample.output_text is None and not sample.content_retained for sample in measured
        )
        rows = connection.execute("SELECT payload_json FROM request_samples").fetchall()
        assert all("zebra-secret-output" not in row["payload_json"] for row in rows)
        events = connection.execute("SELECT payload_json FROM run_events").fetchall()
        assert all("zebra-secret-output" not in row["payload_json"] for row in events)
        metrics = MetricSampleRepository(connection).list_for_run(run.id)
        quality = [m for m in metrics if m.metric_name == "quality_pass_rate_percent"]
        assert len(quality) == 1
        assert quality[0].value == 100 and quality[0].sample_count == 3
        assert quality[0].method_version == QUALITY_METHOD_VERSION
    finally:
        connection.close()
    for file in tmp_path.glob("quality.sqlite3*"):
        assert b"zebra-secret-output" not in file.read_bytes()


def test_quality_smoke_dataset_runs_exact_match_in_the_worker(tmp_path) -> None:
    database_path = tmp_path / "quality.sqlite3"
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        dataset = ensure_quality_smoke_dataset(connection, tmp_path)
    finally:
        connection.close()
    workload = Workload(
        name="Smoke",
        kind=WorkloadKind.CHAT,
        status=WorkloadStatus.READY,
        input_mode=WorkloadInputMode.DATASET,
        dataset_id=dataset.id,
        preset=WorkloadPreset.QUALITY_SMOKE,
        request_count=3,
        max_output_tokens=16,
        target_output_tokens=16,
        quality_checks=QualityChecks(exact_match=True),
    )
    # The fake engine always answers "12": right for item one (7 + 5), wrong for the next two.
    run, path = _worker_run(
        tmp_path, workload, chunks=("1", "2"), dataset_hash=dataset.snapshot_hash
    )
    connection = open_database(path)
    try:
        measured = [
            sample
            for sample in RequestSampleRepository(connection).list_for_run(run.id)
            if sample.phase == "measurement"
        ]
        assert [sample.quality_outcome for sample in measured] == ["pass", "fail", "fail"]
        assert measured[1].quality_reasons == ["exact_match_mismatch"]
        stored_run = RecordRepository(connection, BenchmarkRun).get(run.id)
        assert stored_run is not None
        plan = RecordRepository(connection, BenchmarkPlan).get(stored_run.plan_id)
        assert plan is not None
        metrics = MetricSampleRepository(connection).list_for_run(run.id)
        verdict = evaluate_verdict(stored_run, plan, metrics, measured)
        [quality] = verdict.evaluations
        # Three checked outputs are fewer than the five a pass rate needs.
        assert quality.status == "unknown"
        assert quality.observed == pytest.approx(100 / 3)
    finally:
        connection.close()


# --- Prefill estimate and verdict route --------------------------------------------------------


def test_prefill_estimate_and_verdict_routes(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        workload_id = client.post(
            "/api/v1/workloads/presets",
            json={"name": "Coding", "preset": "coding_assistant", "request_count": 2},
        ).json()["id"]
        plan = _plan(client, engine_id, model_id, workload_id)
        none_yet = client.get(
            "/api/v1/prefill-estimate",
            params={"engine_profile_id": engine_id, "input_tokens": 8192},
        )
        assert none_yet.status_code == 200
        assert none_yet.json()["available"] is False

        connection = open_database(app.state.database_path)
        try:
            run = BenchmarkRun(
                plan_id=UUID(plan["id"]),
                status=BenchmarkRunStatus.COMPLETED,
                profile_snapshot={"targets": plan["targets"]},
            )
            RecordRepository(connection, BenchmarkRun).create(run)
            repository = MetricSampleRepository(connection)
            for metric in (
                _metric(
                    run.id,
                    "ollama.prompt_eval_rate_tokens_per_second",
                    140,
                    sample_count=2,
                ),
                _metric(run.id, "ttft_ms", 58_500, aggregation="p50", sample_count=2),
                _metric(run.id, "error_rate_percent", 0, sample_count=2),
            ):
                repository.create(metric)
        finally:
            connection.close()

        estimate = client.get(
            "/api/v1/prefill-estimate",
            params={
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "input_tokens": 8192,
            },
        )
        assert estimate.status_code == 200, estimate.text
        body = estimate.json()
        assert body["available"] is True
        assert body["prompt_tokens_per_second"] == pytest.approx(140)
        assert body["expected_first_token_seconds"] == pytest.approx(8192 / 140)
        assert body["message"].startswith(
            "Expected first token ≈ 59 s for this 8,192-token context"
        )

        verdict = client.get(f"/api/v1/runs/{run.id}/verdict")
        assert verdict.status_code == 200, verdict.text
        payload = verdict.json()
        assert payload["overall"] == "misses_targets"
        assert payload["target_source"] == "run_plan_snapshot"
        ttft = next(item for item in payload["evaluations"] if item["target"] == "ttft_p50_ms_max")
        assert "First token took 58 s at p50 against a 5.0 s target" in ttft["explanation"]
        assert client.get(f"/api/v1/runs/{uuid4()}/verdict").status_code == 404
