"""Comparability, reproducible report, import, and experiment sweep coverage."""

import csv
import io
import json
import time
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from localforge.api.experiments import _safe_export_filename
from localforge.api.main import AppSettings, create_app
from localforge.comparisons import build_run_comparison
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkPlanStatus,
    BenchmarkRun,
    BenchmarkRunStatus,
    EngineIntegrationMode,
    EngineProfile,
    EngineProfileStatus,
    ExperimentGroup,
    ExperimentGroupStatus,
    ExperimentTrial,
    ExperimentTrialStatus,
    ExperimentVariant,
    HardwareSnapshot,
    MachineProfile,
    MetricSample,
    MetricSource,
    ModelArtifact,
    ReportFormat,
    RequestOutcome,
    RequestSample,
    Workload,
    WorkloadInputMode,
    WorkloadKind,
    WorkloadStatus,
)
from localforge.storage.database import initialize_database, open_database, transaction
from localforge.storage.repositories import (
    MetricSampleRepository,
    RecordRepository,
    RequestSampleRepository,
)
from tests.test_workload_api import _authorize, _client, _ready_engine


def _seed_runs(
    app,
    data_directory,
    workload_settings: list[dict[str, object]],
    *,
    model_refs: list[str] | None = None,
    model_revisions: list[str | None] | None = None,
    tokenizer_identities: list[str] | None = None,
    hardware_names: list[str] | None = None,
    engine_versions: list[str | None] | None = None,
    contention: list[bool] | None = None,
    retain_content: bool = False,
    different_metric_definitions: bool = False,
    different_measurement_sources: bool = False,
) -> tuple[list[UUID], list[UUID], list[UUID]]:
    app.state.database_path = data_directory / "localforge.sqlite3"
    app.state.data_directory = data_directory
    app.state.database_schema_version = initialize_database(app.state.database_path)
    connection = open_database(app.state.database_path)
    run_ids: list[UUID] = []
    plan_ids: list[UUID] = []
    workload_ids: list[UUID] = []
    try:
        with transaction(connection):
            engine = EngineProfile(
                label="Captured fixture engine",
                adapter_id="localforge.fake",
                integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
                status=EngineProfileStatus.ENABLED,
                adapter_version="1.0",
                engine_version="fixture-v1",
            )
            RecordRepository(connection, EngineProfile).create(engine)
            base_model = ModelArtifact(
                display_name="Fixture model",
                engine_profile_id=engine.id,
                model_ref="fixture/model",
                revision="revision-1",
                format="gguf",
                tokenizer_identity="fixture-tokenizer-v1",
                engine_extensions={"fixture": {"quantization_metadata": "Q4_K_M"}},
            )
            RecordRepository(connection, ModelArtifact).create(base_model)
            for index, settings in enumerate(workload_settings):
                hardware_name = (hardware_names or ["Fixture CPU"] * len(workload_settings))[index]
                machine = MachineProfile(
                    label=f"Fixture machine {index}",
                    hardware=HardwareSnapshot(
                        os_name="Fixture OS",
                        os_version="Fixture 1",
                        architecture="x86_64",
                        cpu_model=hardware_name,
                        cpu_logical_cores=8,
                        memory_total_bytes=4096,
                        accelerators=[],
                    ),
                )
                RecordRepository(connection, MachineProfile).create(machine)
                model = base_model
                model_ref = model_refs[index] if model_refs is not None else base_model.model_ref
                model_revision = (
                    model_revisions[index] if model_revisions is not None else base_model.revision
                )
                tokenizer_identity = (
                    tokenizer_identities[index]
                    if tokenizer_identities is not None
                    else base_model.tokenizer_identity
                )
                if (
                    model_ref != base_model.model_ref
                    or model_revision != base_model.revision
                    or tokenizer_identity != base_model.tokenizer_identity
                ):
                    model = ModelArtifact(
                        display_name=f"Fixture model {index}",
                        engine_profile_id=engine.id,
                        model_ref=model_ref,
                        revision=model_revision,
                        format="gguf",
                        tokenizer_identity=tokenizer_identity,
                        engine_extensions=base_model.engine_extensions,
                    )
                    RecordRepository(connection, ModelArtifact).create(model)
                workload = Workload(
                    name=f"Workload {index}",
                    kind=WorkloadKind.COMPLETION,
                    status=WorkloadStatus.READY,
                    input_mode=WorkloadInputMode.SYNTHETIC,
                    request_count=4,
                    concurrency=int(settings.get("concurrency", 1)),
                    max_output_tokens=int(settings.get("max_output_tokens", 16)),
                    sampling_config=settings.get("sampling_config", {"temperature": 0.2}),
                )
                RecordRepository(connection, Workload).create(workload)
                plan = BenchmarkPlan(
                    name=f"Plan {index}",
                    status=BenchmarkPlanStatus.READY,
                    machine_profile_id=machine.id,
                    engine_profile_id=engine.id,
                    model_artifact_id=model.id,
                    workload_id=workload.id,
                    measured_requests=4,
                    concurrency=workload.concurrency,
                    retain_prompt_output=retain_content,
                    configuration_snapshot={"workload": workload.model_dump(mode="json")},
                )
                RecordRepository(connection, BenchmarkPlan).create(plan)
                now = datetime.now(UTC)
                snapshot = {
                    "machine_profile_id": str(machine.id),
                    "machine": machine.model_dump(mode="json"),
                    "model_artifact": model.model_dump(mode="json"),
                    "engine": {
                        "id": str(engine.id),
                        "adapter_id": engine.adapter_id,
                        "adapter_version": engine.adapter_version,
                        "engine_version": (
                            engine_versions[index]
                            if engine_versions is not None
                            else engine.engine_version
                        ),
                    },
                    "api_key": "must-not-export-this-value",
                    "secret_refs": {"authorization": "keyring://fixture/private"},
                }
                run = BenchmarkRun(
                    plan_id=plan.id,
                    status=BenchmarkRunStatus.COMPLETED,
                    profile_snapshot=snapshot,
                    started_at=now,
                    completed_at=now,
                    error_message="Request failed with Bearer fixture-private-value",
                    potentially_contended=(contention[index] if contention is not None else False),
                )
                RecordRepository(connection, BenchmarkRun).create(run)
                MetricSampleRepository(connection).create(
                    MetricSample(
                        run_id=run.id,
                        metric_name="output_tokens_per_second",
                        value=20.0 + index,
                        unit="tokens/s",
                        source=(
                            MetricSource.ENGINE_NATIVE
                            if different_measurement_sources and index == 1
                            else MetricSource.DERIVED
                        ),
                        aggregation="run_mean",
                        sample_count=4,
                        definition=(
                            "Changed definition"
                            if different_metric_definitions and index == 1
                            else "Mean completion rate"
                        ),
                        formula="completion_tokens / elapsed_seconds",
                        labels={
                            "phase": "measurement",
                            "request_id": f"volatile-request-{index}",
                            "api_key": f"metric-private-label-{index}",
                            "api_key_value": f"suffix-api-secret-{index}",
                        },
                    )
                )
                MetricSampleRepository(connection).create(
                    MetricSample(
                        run_id=run.id,
                        metric_name="latency_p50",
                        value=None,
                        unit="ms",
                        source=MetricSource.DERIVED,
                        aggregation="run_percentile",
                        sample_count=0,
                        definition="Median completed request latency",
                        formula="p50(latency_ms)",
                        labels={"phase": "measurement"},
                    )
                )
                if retain_content:
                    RequestSampleRepository(connection).create(
                        RequestSample(
                            run_id=run.id,
                            sequence_number=0,
                            phase="measurement",
                            outcome=RequestOutcome.SUCCEEDED,
                            started_at=now,
                            completed_at=now,
                            content_retained=True,
                            prompt_text="api_key=prompt-private-value",
                            output_text="safe retained output",
                        ),
                        allow_content_retention=True,
                    )
                run_ids.append(run.id)
                plan_ids.append(plan.id)
                workload_ids.append(workload.id)
    finally:
        connection.close()
    return run_ids, plan_ids, workload_ids


def test_comparison_grades_only_one_declared_workload_change_as_controlled(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [
            {"sampling_config": {"temperature": 0.2}},
            {"sampling_config": {"temperature": 0.8}},
        ],
    )
    connection = open_database(app.state.database_path)
    try:
        comparison = build_run_comparison(
            connection,
            run_ids,
            declared_variable="sampling_config.temperature",
        )
    finally:
        connection.close()

    assert comparison["grade"] == "A"
    assert comparison["controlled_comparison_badge"] is True
    assert comparison["declared_variable"]["path"] == "workload.sampling_config.temperature"
    shared = {item["metric_name"]: item for item in comparison["shared_metrics"]}
    assert shared["output_tokens_per_second"]["labels"] == {"phase": "measurement"}
    assert shared["latency_p50"]["runs"][str(run_ids[0])]["value"] is None


@pytest.mark.parametrize(
    ("model_refs", "hardware_names", "workload_settings", "metric_mismatch", "grade"),
    [
        (
            ["fixture/model", "different/model"],
            ["Fixture CPU"] * 2,
            [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
            False,
            "not_comparable",
        ),
        (
            ["fixture/model", "fixture/model"],
            ["Fixture CPU", "Other CPU"],
            [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
            False,
            "C",
        ),
        (
            ["fixture/model", "fixture/model"],
            ["Fixture CPU"] * 2,
            [
                {"sampling_config": {"temperature": 0.2}, "concurrency": 1},
                {"sampling_config": {"temperature": 0.8}, "concurrency": 2},
            ],
            False,
            "not_comparable",
        ),
        (
            ["fixture/model", "fixture/model"],
            ["Fixture CPU"] * 2,
            [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
            True,
            "B",
        ),
    ],
)
def test_comparison_grade_reflects_artifact_hardware_extra_changes_and_metric_definitions(
    tmp_path,
    model_refs,
    hardware_names,
    workload_settings,
    metric_mismatch,
    grade,
) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        workload_settings,
        model_refs=model_refs,
        hardware_names=hardware_names,
        different_metric_definitions=metric_mismatch,
    )
    connection = open_database(app.state.database_path)
    try:
        comparison = build_run_comparison(
            connection,
            run_ids,
            declared_variable="sampling_config.temperature",
        )
    finally:
        connection.close()

    assert comparison["grade"] == grade
    assert comparison["controlled_comparison_badge"] is False
    if grade == "not_comparable":
        assert comparison["label"] == "Not comparable"
    if metric_mismatch:
        assert "output_tokens_per_second" not in {
            item["metric_name"] for item in comparison["shared_metrics"]
        }
        assert any(
            item["key"] == "measurement_sources" and item["state"] == "mismatch"
            for item in comparison["dimensions"]
        )


@pytest.mark.parametrize(
    ("dimension", "expected_grade", "expected_state"),
    [
        ("engine", "C", "mismatch"),
        ("contention", "C", "mismatch"),
        ("measurement", "B", "mismatch"),
    ],
)
def test_comparison_grades_runtime_contention_and_measurement_mismatches(
    tmp_path, dimension, expected_grade, expected_state
) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    options: dict[str, object] = {}
    if dimension == "engine":
        options["engine_versions"] = ["fixture-v1", "fixture-v2"]
        key = "runtime"
    elif dimension == "contention":
        options["contention"] = [False, True]
        key = "contention"
    else:
        options["different_measurement_sources"] = True
        key = "measurement_sources"
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
        **options,
    )
    connection = open_database(app.state.database_path)
    try:
        comparison = build_run_comparison(
            connection,
            run_ids,
            declared_variable="sampling_config.temperature",
        )
    finally:
        connection.close()

    assert comparison["grade"] == expected_grade
    assert comparison["controlled_comparison_badge"] is False
    dimension_result = next(item for item in comparison["dimensions"] if item["key"] == key)
    assert dimension_result["state"] == expected_state
    if dimension == "measurement":
        assert "output_tokens_per_second" not in {
            item["metric_name"] for item in comparison["shared_metrics"]
        }


def test_tokenizer_mismatch_blocks_controlled_comparison(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
        tokenizer_identities=["fixture-tokenizer-v1", "fixture-tokenizer-v2"],
    )
    connection = open_database(app.state.database_path)
    try:
        comparison = build_run_comparison(
            connection,
            run_ids,
            declared_variable="sampling_config.temperature",
        )
    finally:
        connection.close()

    assert comparison["grade"] == "not_comparable"
    assert comparison["controlled_comparison_badge"] is False
    tokenizer = next(item for item in comparison["dimensions"] if item["key"] == "tokenizer")
    assert tokenizer["state"] == "mismatch"


def test_missing_runtime_version_downgrades_a_comparison(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
        engine_versions=[None, None],
    )
    connection = open_database(app.state.database_path)
    try:
        comparison = build_run_comparison(
            connection,
            run_ids,
            declared_variable="sampling_config.temperature",
        )
    finally:
        connection.close()

    assert comparison["grade"] == "B"
    assert comparison["controlled_comparison_badge"] is False
    runtime = next(item for item in comparison["dimensions"] if item["key"] == "runtime")
    assert runtime["state"] == "unknown"


def test_missing_model_revision_downgrades_a_comparison(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
        model_revisions=[None, None],
    )
    connection = open_database(app.state.database_path)
    try:
        comparison = build_run_comparison(
            connection,
            run_ids,
            declared_variable="sampling_config.temperature",
        )
    finally:
        connection.close()

    assert comparison["grade"] == "B"
    assert comparison["controlled_comparison_badge"] is False
    artifact = next(item for item in comparison["dimensions"] if item["key"] == "model_artifact")
    assert artifact["state"] == "unknown"


def test_blank_identity_strings_are_unknown_not_matching_evidence(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
        tokenizer_identities=["", ""],
    )
    connection = open_database(app.state.database_path)
    try:
        repository = RecordRepository(connection, BenchmarkRun)
        for run_id in run_ids:
            run = repository.get(run_id)
            assert run is not None
            snapshot = json.loads(json.dumps(run.profile_snapshot))
            snapshot["machine"]["hardware"]["os_name"] = "   "
            repository.update(run.model_copy(update={"profile_snapshot": snapshot}))
        comparison = build_run_comparison(
            connection,
            run_ids,
            declared_variable="sampling_config.temperature",
        )
    finally:
        connection.close()

    assert comparison["grade"] == "B"
    assert comparison["controlled_comparison_badge"] is False
    hardware = next(item for item in comparison["dimensions"] if item["key"] == "hardware")
    tokenizer = next(item for item in comparison["dimensions"] if item["key"] == "tokenizer")
    assert hardware["state"] == "unknown"
    assert tokenizer["state"] == "unknown"


def test_comparison_rejects_a_run_that_is_still_active(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        _authorize(client)
        connection = open_database(app.state.database_path)
        try:
            active = RecordRepository(connection, BenchmarkRun).get(run_ids[0])
            assert active is not None
            RecordRepository(connection, BenchmarkRun).update(
                active.model_copy(update={"status": BenchmarkRunStatus.RUNNING})
            )
        finally:
            connection.close()
        response = client.post(
            "/api/v1/comparisons",
            json={"run_ids": [str(run_id) for run_id in run_ids]},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "run_not_terminal"


def test_report_rejects_a_run_that_is_still_active(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        _authorize(client)
        connection = open_database(app.state.database_path)
        try:
            active = RecordRepository(connection, BenchmarkRun).get(run_ids[0])
            assert active is not None
            RecordRepository(connection, BenchmarkRun).update(
                active.model_copy(update={"status": BenchmarkRunStatus.RUNNING})
            )
        finally:
            connection.close()
        response = client.post(
            "/api/v1/reports",
            json={
                "name": "In-progress report",
                "format": "json",
                "run_ids": [str(run_id) for run_id in run_ids],
            },
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "run_not_terminal"


def test_report_formats_redact_secrets_and_include_content_only_on_request(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, plan_ids, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
        retain_content=True,
        contention=[True, False],
    )
    group = ExperimentGroup(
        name="Report fixture sweep",
        description="api_key=group-private-value",
        status=ExperimentGroupStatus.COMPLETED,
        run_ids=run_ids,
        baseline_plan_id=plan_ids[0],
        independent_variable="sampling_config.temperature",
        baseline_value=0.2,
        variants=[ExperimentVariant(name="Higher", value=0.8)],
        repetitions=1,
        randomize_order=True,
        order_seed=91,
        trials=[
            ExperimentTrial(
                order=index,
                variant_name="Baseline" if index == 0 else "Higher",
                variant_value=0.2 if index == 0 else 0.8,
                repetition=1,
                plan_id=plan_ids[index],
                run_id=run_id,
                status=ExperimentTrialStatus.SUCCEEDED,
            )
            for index, run_id in enumerate(run_ids)
        ],
    )
    connection = open_database(app.state.database_path)
    try:
        RecordRepository(connection, ExperimentGroup).create(group)
    finally:
        connection.close()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        _authorize(client)
        unsafe = client.post(
            "/api/v1/reports",
            json={
                "name": "Unsafe report",
                "filename": "../../outside",
                "format": "json",
                "run_ids": [str(run_id) for run_id in run_ids],
            },
        )
        assert unsafe.status_code == 422

        reports: dict[str, str] = {}
        for file_format in ("json", "csv", "markdown"):
            created = client.post(
                "/api/v1/reports",
                json={
                    "name": "Report | <comparison>",
                    "format": file_format,
                    "run_ids": [str(run_id) for run_id in run_ids],
                    "include_retained_content": False,
                },
            )
            assert created.status_code == 201, created.text
            report_id = created.json()["report"]["id"]
            download = client.get(f"/api/v1/reports/{report_id}/download")
            assert download.status_code == 200
            reports[file_format] = download.text

        document = json.loads(reports["json"])
        assert document["report_version"] == 1
        assert document["privacy"]["credentials_included"] is False
        assert document["runs"][0]["request_content"] is None
        assert "must-not-export-this-value" not in reports["json"]
        assert "fixture-private-value" not in reports["json"]
        assert "prompt-private-value" not in reports["json"]
        assert "metric-private-label" not in "".join(reports.values())
        assert "suffix-api-secret" not in "".join(reports.values())
        assert "report_version" in reports["csv"]
        csv_records = list(csv.DictReader(io.StringIO(reports["csv"])))
        assert {row["record_type"] for row in csv_records} >= {
            "report",
            "comparison_dimension",
            "shared_metric",
            "run",
            "metric",
        }
        assert "Comparability" in reports["markdown"]
        assert r"output\_tokens\_per\_second" in reports["markdown"]
        assert "prompt-private-value" not in reports["markdown"]
        assert "Run proceeded while resources were potentially contended." in reports["csv"]
        assert "Run proceeded while resources were potentially contended" in reports["markdown"]

        grouped_reports: dict[str, str] = {}
        for file_format in ("json", "csv", "markdown"):
            grouped = client.post(
                "/api/v1/reports",
                json={
                    "name": "Grouped report",
                    "format": file_format,
                    "run_ids": [str(run_id) for run_id in reversed(run_ids)],
                    "experiment_group_id": str(group.id),
                },
            )
            assert grouped.status_code == 201, grouped.text
            grouped_download = client.get(
                f"/api/v1/reports/{grouped.json()['report']['id']}/download"
            )
            grouped_reports[file_format] = grouped_download.text
            if file_format == "json":
                assert json.loads(grouped_download.text)["comparability"]["baseline_run_id"] == str(
                    run_ids[0]
                )
        assert "group-private-value" not in "".join(grouped_reports.values())
        assert "order_seed" in grouped_reports["json"]
        assert "experiment_group_json" in grouped_reports["csv"]
        assert "Order seed" in grouped_reports["markdown"]
        assert "Variant" in grouped_reports["markdown"]

        retained = client.post(
            "/api/v1/reports",
            json={
                "name": "Selected content",
                "format": "json",
                "run_ids": [str(run_ids[0])],
                "include_retained_content": True,
            },
        )
        assert retained.status_code == 201, retained.text
        retained_doc = client.get(
            f"/api/v1/reports/{retained.json()['report']['id']}/download"
        ).json()
        request_content = retained_doc["runs"][0]["request_content"]
        assert request_content[0]["prompt_text"] == "api_key=[REDACTED]"
        assert request_content[0]["output_text"] == "safe retained output"
        assert "prompt-private-value" not in json.dumps(retained_doc)


def test_failed_report_write_keeps_runs_and_records_failure(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
    )
    (tmp_path / "reports").write_text("not a directory", encoding="utf-8")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        _authorize(client)
        response = client.post(
            "/api/v1/reports",
            json={
                "name": "Cannot write",
                "format": "json",
                "run_ids": [str(run_id) for run_id in run_ids],
            },
        )
        assert response.status_code == 500
        reports = client.get("/api/v1/reports").json()
        assert reports[-1]["status"] == "failed"
        persisted_runs = client.get("/api/v1/runs").json()
        assert {run["id"] for run in persisted_runs} >= {str(item) for item in run_ids}


def test_benchmark_plan_export_import_preserves_workload_configuration(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        workload = client.post(
            "/api/v1/workloads/presets",
            json={
                "name": "Temperature sweep baseline",
                "preset": "fixed_decode",
                "request_count": 3,
                "max_output_tokens": 12,
                "sampling_config": {"temperature": 0.3, "top_p": 0.8, "seed": 17},
            },
        )
        assert workload.status_code == 201, workload.text
        plan_response = client.post(
            "/api/v1/benchmark-plans",
            json={
                "name": "Export fixture plan",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": workload.json()["id"],
                "retain_prompt_output": True,
            },
        )
        assert plan_response.status_code == 201, plan_response.text
        plan_id = plan_response.json()["plan"]["id"]
        exported = client.get(f"/api/v1/benchmark-plans/{plan_id}/export")
        assert exported.status_code == 200
        bundle = exported.json()
        assert "api_key" not in exported.text.casefold()
        imported = client.post("/api/v1/benchmark-plans/import", json=bundle)
        assert imported.status_code == 201, imported.text
        imported_workload = imported.json()["plan"]["configuration_snapshot"]["workload"]
        assert imported_workload["sampling_config"] == {
            "temperature": 0.3,
            "top_p": 0.8,
            "seed": 17,
        }
        assert imported_workload["request_count"] == 3
        assert imported_workload["max_output_tokens"] == 12
        assert imported.json()["plan"]["retain_prompt_output"] is True
        assert imported_workload["id"] != bundle["workload"]["id"]


def test_small_sweep_runs_sequentially_compares_and_exports(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        workload = client.post(
            "/api/v1/workloads/presets",
            json={
                "name": "Two-trial fixture",
                "preset": "fixed_decode",
                "request_count": 1,
                "max_output_tokens": 2,
                "sampling_config": {"temperature": 0.2},
            },
        )
        assert workload.status_code == 201, workload.text
        plan = client.post(
            "/api/v1/benchmark-plans",
            json={
                "name": "Two-trial baseline",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": workload.json()["id"],
            },
        )
        assert plan.status_code == 201, plan.text
        reserved_variant = client.post(
            "/api/v1/experiments",
            json={
                "name": "Reserved baseline label",
                "baseline_plan_id": plan.json()["plan"]["id"],
                "independent_variable": "sampling_config.temperature",
                "variants": [{"name": "baseline", "value": 0.8}],
            },
        )
        assert reserved_variant.status_code == 422
        assert reserved_variant.json()["detail"]["code"] == "reserved_variant_name"
        created = client.post(
            "/api/v1/experiments",
            json={
                "name": "Temperature check",
                "baseline_plan_id": plan.json()["plan"]["id"],
                "independent_variable": "sampling_config.temperature",
                "variants": [{"name": "Higher temperature", "value": 0.8}],
                "repetitions": 1,
                "randomize_order": True,
                "order_seed": 27,
                "hypothesis": "The setting may change throughput.",
            },
        )
        assert created.status_code == 201, created.text
        group_id = created.json()["group"]["id"]
        assert created.json()["estimated_run_count"] == 2
        started = client.post(
            f"/api/v1/experiments/{group_id}/start",
            json={"confirm_estimated_runtime": True},
        )
        assert started.status_code == 200, started.text

        completed_run_ids: list[str] = []
        for trial_number in range(2):
            launched = client.post(f"/api/v1/experiments/{group_id}/next", json={})
            assert launched.status_code == 200, launched.text
            run = launched.json()["run"]
            assert run is not None
            deadline = time.monotonic() + 10
            current = client.get(f"/api/v1/runs/{run['id']}").json()
            while time.monotonic() < deadline and current["status"] not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }:
                time.sleep(0.05)
                current = client.get(f"/api/v1/runs/{run['id']}").json()
            assert current["status"] == "completed", current
            completed_run_ids.append(run["id"])
            refreshed = client.get(f"/api/v1/experiments/{group_id}").json()
            if trial_number == 0:
                assert refreshed["status"] == "running"
                assert sum(trial["status"] == "succeeded" for trial in refreshed["trials"]) == 1

        group = client.get(f"/api/v1/experiments/{group_id}").json()
        assert group["status"] == "completed"
        assert len(group["run_ids"]) == 2
        comparison = client.get(f"/api/v1/experiments/{group_id}/comparison")
        assert comparison.status_code == 200, comparison.text
        assert set(comparison.json()["run_ids"]) == set(completed_run_ids)
        assert (
            comparison.json()["declared_variable"]["path"] == "workload.sampling_config.temperature"
        )

        report = client.post(
            "/api/v1/reports",
            json={
                "name": "Two variant results",
                "format": "json",
                "run_ids": completed_run_ids,
                "experiment_group_id": group_id,
            },
        )
        assert report.status_code == 201, report.text
        payload = client.get(f"/api/v1/reports/{report.json()['report']['id']}/download").json()
        assert payload["experiment_group"]["order_seed"] == 27
        assert len(payload["experiment_group"]["trials"]) == 2
        assert payload["comparability"]["grade"] in {"A", "B", "C"}


def test_export_filename_rejects_traversal_and_other_platform_separators() -> None:
    report_id = UUID("d6da263b-5910-4451-913b-4dfb506b1eab")
    for filename in ("../report", "..\\report", "C:\\report", "/tmp/report"):
        with pytest.raises(ValueError):
            _safe_export_filename(filename, ReportFormat.JSON, report_id)
