"""Run state, event replay, resource lock, worker, and SSE behavior."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.fake import FakeEngineAdapter, FakeEngineScenario, FakeStreamFailure
from localforge.api import runs as run_api
from localforge.api.main import AppSettings, create_app
from localforge.api.runs import _resource_keys
from localforge.core.models import (
    AcceleratorInfo,
    BenchmarkPlan,
    BenchmarkPlanStatus,
    BenchmarkRun,
    BenchmarkRunStatus,
    EngineIntegrationMode,
    EngineProfile,
    EngineProfileStatus,
    HardwareSnapshot,
    MachineProfile,
    RequestOutcome,
    RequestSample,
    TrafficMode,
    Workload,
    WorkloadInputMode,
    WorkloadKind,
    WorkloadPreset,
    WorkloadStatus,
)
from localforge.metrics import persist_derived_run_metrics
from localforge.runs import (
    InvalidRunTransition,
    ResourceLockConflict,
    UnsafeRunEvent,
    append_run_event,
    create_queued_run,
    finalize_request_metrics,
    list_run_events,
    reconcile_stale_workers,
    recover_runs_on_startup,
    transition_run,
)
from localforge.storage.database import initialize_database, open_database
from localforge.storage.repositories import (
    MetricSampleRepository,
    RecordRepository,
    RequestSampleRepository,
)
from localforge.worker.execution import execute_run


@pytest.fixture
def connection(tmp_path):
    database_path = tmp_path / "runs.sqlite3"
    initialize_database(database_path)
    database = open_database(database_path)
    try:
        yield database
    finally:
        database.close()


def _new_queued_run(connection, *, app_id: str = "test-app", locks=None) -> BenchmarkRun:
    run = BenchmarkRun(plan_id=uuid4(), worker_id=str(uuid4()))
    created, _ = create_queued_run(
        connection,
        run,
        app_instance_id=app_id,
        resource_keys=locks or [],
        override_contention=False,
    )
    return created


def test_run_state_machine_records_transitions_and_rejects_illegal_moves(connection) -> None:
    run = _new_queued_run(connection)
    transition_run(connection, run.id, BenchmarkRunStatus.PREPARING)
    transition_run(connection, run.id, BenchmarkRunStatus.WARMING)
    transition_run(connection, run.id, BenchmarkRunStatus.RUNNING)
    transition_run(connection, run.id, BenchmarkRunStatus.CANCELLING)
    completed = transition_run(connection, run.id, BenchmarkRunStatus.CANCELLED)

    assert completed.completed_at is not None
    with pytest.raises(InvalidRunTransition, match="cannot move"):
        transition_run(connection, run.id, BenchmarkRunStatus.RUNNING)
    events = list_run_events(connection, run.id)
    assert [event.sequence_number for event in events] == list(range(6))
    assert [event.payload.get("to") for event in events if event.event_type == "run_status"] == [
        "queued",
        "preparing",
        "warming",
        "running",
        "cancelling",
        "cancelled",
    ]


def test_run_events_replay_in_order_and_reject_bodies(connection) -> None:
    run = _new_queued_run(connection)
    append_run_event(connection, run.id, "request_started", {"sequence": 0})
    append_run_event(connection, run.id, "first_output", {"observed": True})
    append_run_event(connection, run.id, "output_chunk", {"chunk_index": 0, "characters": 12})
    append_run_event(
        connection,
        run.id,
        "engine_usage",
        {"input_tokens": 4, "output_tokens": 9, "exact": True},
    )

    assert [event.sequence_number for event in list_run_events(connection, run.id)] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert all(event.schema_version == 1 for event in list_run_events(connection, run.id))
    assert [event.event_type for event in list_run_events(connection, run.id, after=1)] == [
        "first_output",
        "output_chunk",
        "engine_usage",
    ]
    with pytest.raises(UnsafeRunEvent):
        append_run_event(connection, run.id, "request_started", {"prompt": "private"})
    with pytest.raises(UnsafeRunEvent):
        append_run_event(connection, run.id, "output_chunk", {"text": "private"})


def test_worker_interruption_recovers_partial_output_without_inventing_duration(connection) -> None:
    run = _new_queued_run(connection)
    request_id = uuid4()
    samples = RequestSampleRepository(connection)
    samples.create(
        RequestSample(
            run_id=run.id,
            request_id=request_id,
            sequence_number=0,
            outcome=RequestOutcome.IN_PROGRESS,
            started_at=datetime.now(UTC),
            dispatch_offset_ms=0,
        )
    )
    append_run_event(connection, run.id, "first_output", {"observed": True}, request_id=request_id)
    append_run_event(
        connection,
        run.id,
        "output_chunk",
        {"chunk_index": 0, "characters": 4},
        request_id=request_id,
    )
    append_run_event(
        connection,
        run.id,
        "output_chunk",
        {"chunk_index": 1, "characters": 6},
        request_id=request_id,
    )

    assert samples.interrupt_incomplete_for_run(run.id) == 1
    recovered = samples.list_for_run(run.id)[0]

    assert recovered.outcome is RequestOutcome.INTERRUPTED
    assert recovered.error_class == "worker_interrupted"
    assert recovered.first_output_offset_ms is not None
    assert recovered.ttft_ms == pytest.approx(recovered.first_output_offset_ms)
    assert recovered.completed_at is None
    assert recovered.completed_offset_ms is None
    assert recovered.latency_ms is None
    assert recovered.output_event_count == 2
    assert recovered.output_characters == 10
    assert recovered.partial_output_observed is True
    assert len(recovered.inter_event_latency_ms) == 1


def test_stale_worker_lease_releases_resource_lock(connection) -> None:
    run = _new_queued_run(connection, locks=[("cpu_group", "machine-1")])
    stale = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    connection.execute(
        "UPDATE worker_leases SET heartbeat_at = ? WHERE run_id = ?", (stale, str(run.id))
    )

    assert reconcile_stale_workers(connection, stale_after_seconds=15) == 1
    recovered = RecordRepository(connection, BenchmarkRun).get(run.id)
    assert recovered is not None and recovered.status is BenchmarkRunStatus.INTERRUPTED
    events = list_run_events(connection, run.id)
    metric_events = [event for event in events if event.event_type == "run_metric"]
    terminal_event = next(
        event
        for event in events
        if event.event_type == "run_status" and event.payload.get("to") == "interrupted"
    )
    assert metric_events
    assert max(event.sequence_number for event in metric_events) < terminal_event.sequence_number
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM resource_locks WHERE run_id = ?", (str(run.id),)
        ).fetchone()[0]
        == 0
    )


def test_resource_lock_acquisition_is_atomic_and_override_is_recorded(connection) -> None:
    _new_queued_run(connection, locks=[("accelerator", "machine-1:index:0")])
    with pytest.raises(ResourceLockConflict, match="already locked"):
        create_queued_run(
            connection,
            BenchmarkRun(plan_id=uuid4(), worker_id=str(uuid4())),
            app_instance_id="test-app",
            resource_keys=[("accelerator", "machine-1:index:0")],
            override_contention=False,
        )

    overridden, conflicts = create_queued_run(
        connection,
        BenchmarkRun(plan_id=uuid4(), worker_id=str(uuid4())),
        app_instance_id="test-app",
        resource_keys=[("accelerator", "machine-1:index:0")],
        override_contention=True,
    )
    assert overridden.potentially_contended is True
    assert len(conflicts) == 1
    events = list_run_events(connection, overridden.id)
    assert events[-1].payload["code"] == "resource_contention_overridden"


def test_local_endpoint_profiles_lock_the_shared_machine(connection) -> None:
    machine = MachineProfile(
        label="Shared host",
        host_name="shared-host",
        hardware=HardwareSnapshot(accelerators=[AcceleratorInfo(name="GPU", vendor="Example")]),
    )
    RecordRepository(connection, MachineProfile).create(machine)
    engine = EngineProfile(
        label="Local endpoint one",
        adapter_id="localforge.fake",
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        endpoint_url="http://127.0.0.1:8001/v1",
        machine_profile_id=machine.id,
    )
    other_engine = engine.model_copy(
        update={
            "id": uuid4(),
            "label": "Local endpoint two",
            "endpoint_url": "http://localhost:8002/v1",
        }
    )
    plan = BenchmarkPlan(
        name="Local endpoint plan",
        engine_profile_id=engine.id,
        workload_id=uuid4(),
        machine_profile_id=machine.id,
    )

    resources = _resource_keys(connection, plan, engine)
    other_resources = _resource_keys(connection, plan, other_engine)
    shared = set(resources) & set(other_resources)
    assert ("endpoint", str(engine.id)) in resources
    assert ("endpoint", str(other_engine.id)) in other_resources
    assert len(shared) == 1 and next(iter(shared))[0] == "accelerator"

    create_queued_run(
        connection,
        BenchmarkRun(plan_id=plan.id, worker_id=str(uuid4())),
        app_instance_id="test-app",
        resource_keys=resources,
        override_contention=False,
    )
    with pytest.raises(ResourceLockConflict):
        create_queued_run(
            connection,
            BenchmarkRun(plan_id=plan.id, worker_id=str(uuid4())),
            app_instance_id="test-app",
            resource_keys=other_resources,
            override_contention=False,
        )


def test_composed_stack_profiles_lock_the_shared_machine(connection) -> None:
    machine = MachineProfile(
        label="Shared stack host",
        host_name="shared-stack-host",
        hardware=HardwareSnapshot(accelerators=[AcceleratorInfo(name="GPU")]),
    )
    RecordRepository(connection, MachineProfile).create(machine)
    engine = EngineProfile(
        label="Stack one",
        adapter_id="localforge.fake",
        integration_mode=EngineIntegrationMode.COMPOSED_SERVING_STACK,
        machine_profile_id=machine.id,
    )
    other_engine = engine.model_copy(update={"id": uuid4(), "label": "Stack two"})
    plan = BenchmarkPlan(
        name="Stack plan",
        engine_profile_id=engine.id,
        workload_id=uuid4(),
        machine_profile_id=machine.id,
    )

    resources = _resource_keys(connection, plan, engine)
    other_resources = _resource_keys(connection, plan, other_engine)
    shared = set(resources) & set(other_resources)
    assert ("serving_stack", str(engine.id)) in resources
    assert ("serving_stack", str(other_engine.id)) in other_resources
    assert len(shared) == 1 and next(iter(shared))[0] == "accelerator"


def test_startup_marks_nonterminal_runs_interrupted(connection) -> None:
    run = _new_queued_run(connection, locks=[("endpoint", "engine-1")])

    assert recover_runs_on_startup(connection, app_instance_id="new-app") == 1
    recovered = RecordRepository(connection, BenchmarkRun).get(run.id)
    assert recovered is not None and recovered.status is BenchmarkRunStatus.INTERRUPTED
    assert recovered.error_code == "app_restarted"
    events = list_run_events(connection, run.id)
    metric_events = [event for event in events if event.event_type == "run_metric"]
    assert metric_events
    assert events[-1].event_type == "run_status"
    assert events[-1].payload["to"] == "interrupted"
    assert max(event.sequence_number for event in metric_events) < events[-1].sequence_number


def test_finalization_replays_persisted_metric_events_without_duplicates(connection) -> None:
    run = _new_queued_run(connection)
    RequestSampleRepository(connection).create(
        RequestSample(
            run_id=run.id,
            sequence_number=0,
            outcome=RequestOutcome.SUCCEEDED,
            started_at=datetime.now(UTC),
            dispatch_offset_ms=0,
            completed_offset_ms=100,
            latency_ms=100,
        )
    )

    persisted = persist_derived_run_metrics(connection, run.id)
    assert persisted
    assert not [
        event for event in list_run_events(connection, run.id) if event.event_type == "run_metric"
    ]

    finalize_request_metrics(connection, run.id)
    first_events = [
        event for event in list_run_events(connection, run.id) if event.event_type == "run_metric"
    ]
    finalize_request_metrics(connection, run.id)
    second_events = [
        event for event in list_run_events(connection, run.id) if event.event_type == "run_metric"
    ]

    assert len(first_events) == len(persisted)
    assert len(second_events) == len(first_events)


def test_new_app_instance_recovers_persisted_active_run(tmp_path) -> None:
    settings = AppSettings(data_directory=tmp_path)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as first_client:
        connection = open_database(first_client.app.state.database_path)
        try:
            run = _new_queued_run(
                connection,
                app_id=first_client.app.state.app_instance_id,
                locks=[("cpu_group", "local-machine:cpu")],
            )
        finally:
            connection.close()

    with TestClient(create_app(settings), base_url="http://127.0.0.1") as restarted_client:
        recovered = restarted_client.get(f"/api/v1/runs/{run.id}")
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "interrupted"
        assert recovered.json()["error_code"] == "app_restarted"


def _worker_fixture(connection) -> tuple[BenchmarkRun, BenchmarkPlan, EngineProfile, Workload]:
    engine = EngineProfile(
        label="Fake target",
        adapter_id="localforge.fake",
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        status=EngineProfileStatus.ENABLED,
        selected_model_ref="fake://deterministic-model",
    )
    workload = Workload(
        name="Cancellation fixture",
        kind=WorkloadKind.CHAT,
        status=WorkloadStatus.READY,
        input_mode=WorkloadInputMode.SYNTHETIC,
        request_count=10,
        concurrency=1,
        max_output_tokens=4,
        target_input_tokens=4,
        target_output_tokens=4,
    )
    plan = BenchmarkPlan(
        name="Cancellation plan",
        status=BenchmarkPlanStatus.READY,
        engine_profile_id=engine.id,
        workload_id=workload.id,
        warmup_requests=0,
        measured_requests=10,
        concurrency=1,
        timeout_seconds=20,
    )
    for record_type, record in (
        (EngineProfile, engine),
        (Workload, workload),
        (BenchmarkPlan, plan),
    ):
        RecordRepository(connection, record_type).create(record)
    run = BenchmarkRun(plan_id=plan.id, worker_id=str(uuid4()))
    run, _ = create_queued_run(
        connection,
        run,
        app_instance_id="test-app",
        resource_keys=[("endpoint", str(engine.id))],
        override_contention=False,
    )
    return run, plan, engine, workload


def test_worker_executes_each_concurrency_sweep_level(tmp_path) -> None:
    database_path = tmp_path / "sweep.sqlite3"
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        run, plan, _, workload = _worker_fixture(connection)
        updated_workload = Workload.model_validate(
            workload.model_dump(mode="python")
            | {
                "preset": WorkloadPreset.CONCURRENCY_SWEEP,
                "request_count": 2,
                "concurrency": 2,
                "concurrency_levels": [1, 2],
                "warmup_count": 1,
            }
        )
        RecordRepository(connection, Workload).update(updated_workload)
        RecordRepository(connection, BenchmarkPlan).update(
            BenchmarkPlan.model_validate(
                plan.model_dump(mode="python")
                | {"measured_requests": 2, "concurrency": 2, "warmup_requests": 1}
            )
        )
    finally:
        connection.close()

    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter())
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

    connection = open_database(database_path)
    try:
        samples = RequestSampleRepository(connection).list_for_run(run.id)
        metrics = MetricSampleRepository(connection).list_for_run(run.id)
        events = list_run_events(connection, run.id)
        measurement_starts = [
            event
            for event in events
            if event.event_type == "request_started" and event.payload.get("phase") == "measurement"
        ]
        warmup_starts = [
            event
            for event in events
            if event.event_type == "request_started" and event.payload.get("phase") == "warmup"
        ]
        measurement_samples = [sample for sample in samples if sample.phase == "measurement"]
        assert len(samples) == 6
        assert len(measurement_samples) == 4
        assert [sample.sequence_number for sample in measurement_samples] == [2, 3, 4, 5]
        assert {sample.concurrency_level for sample in measurement_samples} == {1, 2}
        assert {sample.effective_concurrency for sample in measurement_samples} == {1, 2}
        assert {event.payload["concurrency_level"] for event in measurement_starts} == {1, 2}
        assert {event.payload["concurrency_level"] for event in warmup_starts} == {1, 2}
        attempted_by_level = [
            metric
            for metric in metrics
            if metric.metric_name == "requests_attempted" and metric.aggregation == "run_total"
        ]
        assert {metric.labels["concurrency_level"] for metric in attempted_by_level} == {
            "1",
            "2",
        }
        assert all(metric.value == 2 for metric in attempted_by_level)
    finally:
        connection.close()


def test_worker_retains_request_bodies_at_terminal_sample_when_enabled(tmp_path) -> None:
    database_path = tmp_path / "retained-request.sqlite3"
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        run, plan, _, workload = _worker_fixture(connection)
        RecordRepository(connection, BenchmarkPlan).update(
            BenchmarkPlan.model_validate(
                plan.model_dump(mode="python")
                | {"measured_requests": 1, "retain_prompt_output": True}
            )
        )
        RecordRepository(connection, Workload).update(
            Workload.model_validate(workload.model_dump(mode="python") | {"request_count": 1})
        )
    finally:
        connection.close()

    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter())
    assert (
        asyncio.run(
            execute_run(
                run_id=run.id,
                database_path=database_path,
                data_directory=tmp_path,
                app_instance_id="test-app",
                worker_id=run.worker_id or "",
                catalog_factory=lambda: catalog,
            )
        )
        == 0
    )

    connection = open_database(database_path)
    try:
        sample = RequestSampleRepository(connection).list_for_run(run.id)[0]
        assert sample.outcome is RequestOutcome.SUCCEEDED
        assert sample.content_retained is True
        assert sample.prompt_text is not None
        assert sample.output_text == "deterministic fake response"
    finally:
        connection.close()


def test_worker_persists_native_metric_snapshots_and_counter_delta(tmp_path) -> None:
    database_path = tmp_path / "native-metrics.sqlite3"
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        run, plan, engine, workload = _worker_fixture(connection)
        RecordRepository(connection, EngineProfile).update(
            EngineProfile.model_validate(
                engine.model_dump(mode="python")
                | {"adapter_version": "1.0.0", "engine_version": "fake-1"}
            )
        )
        RecordRepository(connection, BenchmarkPlan).update(
            BenchmarkPlan.model_validate(plan.model_dump(mode="python") | {"measured_requests": 2})
        )
        RecordRepository(connection, Workload).update(
            Workload.model_validate(workload.model_dump(mode="python") | {"request_count": 2})
        )
    finally:
        connection.close()

    adapter = FakeEngineAdapter(FakeEngineScenario(native_metrics=True))
    catalog = AdapterCatalog()
    catalog.register(adapter)
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

    connection = open_database(database_path)
    try:
        metrics = MetricSampleRepository(connection).list_for_run(run.id)
        raw = [
            metric
            for metric in metrics
            if metric.metric_name == "fake.requests_total" and metric.aggregation == "raw"
        ]
        delta = next(
            metric
            for metric in metrics
            if metric.metric_name == "fake.requests_total" and metric.aggregation == "delta"
        )
        assert [(metric.value, metric.labels["snapshot"]) for metric in raw] == [
            (0, "before"),
            (2, "after"),
        ]
        assert all(metric.adapter_id == "localforge.fake" for metric in raw)
        assert all(metric.engine_version == "fake-1" for metric in raw)
        assert delta.value == 2
        assert delta.metric_kind == "counter"
    finally:
        connection.close()


def test_open_loop_late_dispatch_emits_one_load_generator_saturation_warning(tmp_path) -> None:
    database_path = tmp_path / "load-saturation.sqlite3"
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        run, plan, _, workload = _worker_fixture(connection)
        RecordRepository(connection, BenchmarkPlan).update(
            BenchmarkPlan.model_validate(plan.model_dump(mode="python") | {"measured_requests": 2})
        )
        RecordRepository(connection, Workload).update(
            Workload.model_validate(
                workload.model_dump(mode="python")
                | {
                    "request_count": 2,
                    "traffic_mode": TrafficMode.OPEN_LOOP,
                    "arrival_rate_per_second": 1_000,
                }
            )
        )
    finally:
        connection.close()

    first_token_gate = asyncio.Event()
    scenario = FakeEngineScenario(first_token_gate=first_token_gate)
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(scenario))

    async def execute_after_releasing_gate() -> int:
        async def release() -> None:
            await asyncio.sleep(0.05)
            first_token_gate.set()

        release_task = asyncio.create_task(release())
        try:
            return await execute_run(
                run_id=run.id,
                database_path=database_path,
                data_directory=tmp_path,
                app_instance_id="test-app",
                worker_id=run.worker_id or "",
                catalog_factory=lambda: catalog,
            )
        finally:
            await release_task

    assert asyncio.run(execute_after_releasing_gate()) == 0

    connection = open_database(database_path)
    try:
        warnings = [
            event
            for event in list_run_events(connection, run.id)
            if event.event_type == "warning"
            and event.payload.get("code") == "load_generator_saturated"
        ]
        assert len(warnings) == 1
        assert warnings[0].payload["queue_delay_ms"] > 5
    finally:
        connection.close()


def test_failed_warmup_fails_run_without_entering_measurement(tmp_path) -> None:
    database_path = tmp_path / "warmup-failure.sqlite3"
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        run, plan, _, _ = _worker_fixture(connection)
        RecordRepository(connection, BenchmarkPlan).update(
            BenchmarkPlan.model_validate(
                plan.model_dump(mode="python") | {"warmup_requests": 1, "measured_requests": 2}
            )
        )
    finally:
        connection.close()

    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(FakeEngineScenario(failure=FakeStreamFailure.ERROR)))
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
    assert result == 1

    connection = open_database(database_path)
    try:
        saved = RecordRepository(connection, BenchmarkRun).get(run.id)
        events = list_run_events(connection, run.id)
        assert saved is not None and saved.status is BenchmarkRunStatus.FAILED
        assert saved.error_code == "warmup_failed"
        assert not any(
            event.event_type == "request_started" and event.payload.get("phase") == "measurement"
            for event in events
        )
    finally:
        connection.close()


def test_fake_adapter_cancellation_drains_within_bound_and_persists_sample(tmp_path) -> None:
    database_path = tmp_path / "cancel.sqlite3"
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        run, _, _, _ = _worker_fixture(connection)
    finally:
        connection.close()
    scenario = FakeEngineScenario(first_token_gate=asyncio.Event())
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(scenario))

    async def execute_and_cancel() -> tuple[int, float]:
        worker = asyncio.create_task(
            execute_run(
                run_id=run.id,
                database_path=database_path,
                data_directory=tmp_path,
                app_instance_id="test-app",
                worker_id=run.worker_id or "",
                catalog_factory=lambda: catalog,
            )
        )
        await asyncio.wait_for(scenario.stream_started.wait(), timeout=3)
        started = time.perf_counter()
        cancel_connection = open_database(database_path)
        try:
            transition_run(cancel_connection, run.id, BenchmarkRunStatus.CANCELLING)
        finally:
            cancel_connection.close()
        result = await asyncio.wait_for(worker, timeout=3)
        return result, time.perf_counter() - started

    result, elapsed = asyncio.run(execute_and_cancel())
    assert result == 0
    assert elapsed < 3

    connection = open_database(database_path)
    try:
        saved = RecordRepository(connection, BenchmarkRun).get(run.id)
        samples = RequestSampleRepository(connection).list_for_run(run.id)
        assert saved is not None and saved.status is BenchmarkRunStatus.CANCELLED
        assert len(samples) == 1
        assert samples[0].outcome is RequestOutcome.CANCELLED
        assert samples[0].prompt_text is None and samples[0].output_text is None
    finally:
        connection.close()


def _api_plan(client: TestClient) -> tuple[str, str]:
    from tests.test_workload_api import _ready_engine

    engine_id, model_id = _ready_engine(client)
    workload = client.post(
        "/api/v1/workloads/presets",
        json={"name": "Run integration", "preset": "fixed_decode", "request_count": 3},
    )
    assert workload.status_code == 201, workload.text
    plan = client.post(
        "/api/v1/benchmark-plans",
        json={
            "name": "Run integration plan",
            "engine_profile_id": engine_id,
            "model_artifact_id": model_id,
            "workload_id": workload.json()["id"],
        },
    )
    assert plan.status_code == 201, plan.text
    return engine_id, plan.json()["plan"]["id"]


def _authorized_client(tmp_path):
    from tests.test_workload_api import _authorize

    app = create_app(AppSettings(data_directory=tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, app, _authorize


def test_run_api_executes_in_child_process_and_replays_sanitized_events(tmp_path) -> None:
    client, _, authorize = _authorized_client(tmp_path)
    with client:
        authorize(client)
        _, plan_id = _api_plan(client)
        started = client.post("/api/v1/runs", json={"plan_id": plan_id})
        assert started.status_code == 202, started.text
        run_id = started.json()["run"]["id"]

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = client.get(f"/api/v1/runs/{run_id}").json()
            if current["status"] in {"completed", "failed", "interrupted", "cancelled"}:
                break
            time.sleep(0.05)

        assert current["status"] == "completed", current
        page = client.get(f"/api/v1/runs/{run_id}/events").json()
        events = page["events"]
        assert [event["sequence_number"] for event in events] == list(range(len(events)))
        assert any(event["event_type"] == "request_started" for event in events)
        assert any(event["event_type"] == "engine_usage" for event in events)
        assert any(event["event_type"] == "request_completed" for event in events)
        run_metric_events = [event for event in events if event["event_type"] == "run_metric"]
        assert run_metric_events
        attempted_event = next(
            event
            for event in run_metric_events
            if event["payload"].get("metric_name") == "requests_attempted"
        )
        assert attempted_event["payload"]["formula"] == "count of measurement request records"
        assert attempted_event["payload"]["method_version"] == "localforge.metrics.v1"
        assert attempted_event["payload"]["labels"]["concurrency_level"] == "1"
        assert all(
            "metric_name" in event["payload"]
            and "formula" in event["payload"]
            and "labels" in event["payload"]
            for event in run_metric_events
        )
        completed_status = next(
            event
            for event in events
            if event["event_type"] == "run_status" and event["payload"].get("to") == "completed"
        )
        assert (
            max(event["sequence_number"] for event in run_metric_events)
            < completed_status["sequence_number"]
        )
        system_event = next(event for event in events if event["event_type"] == "system_sample")
        assert system_event["payload"]["process_id"] != os.getpid()
        assert "LOCALFORGE synthetic benchmark request" not in json.dumps(events)
        samples = client.get(f"/api/v1/runs/{run_id}/samples").json()["samples"]
        assert len(samples) == 3
        assert all(sample["prompt_text"] is None for sample in samples)
        metrics_response = client.get(f"/api/v1/runs/{run_id}/metrics")
        assert metrics_response.status_code == 200
        persisted_metrics = metrics_response.json()["metrics"]
        attempted_metric = next(
            item for item in persisted_metrics if item["metric_name"] == "requests_attempted"
        )
        assert attempted_metric["value"] == 3
        assert attempted_metric["formula"] == "count of measurement request records"
        assert attempted_metric["method_version"] == "localforge.metrics.v1"
        sample_page = client.get(f"/api/v1/runs/{run_id}/samples?limit=2").json()
        assert [sample["sequence_number"] for sample in sample_page["samples"]] == [0, 1]
        assert sample_page["next_sequence_number"] == 1
        sample_replay = client.get(
            f"/api/v1/runs/{run_id}/samples?after={sample_page['next_sequence_number']}"
        ).json()
        assert [sample["sequence_number"] for sample in sample_replay["samples"]] == [2]

        replay = client.get(
            f"/api/v1/runs/{run_id}/events/stream",
            headers={"Last-Event-ID": f"{run_id}:0"},
        )
        assert replay.status_code == 200
        assert f"id: {run_id}:0" not in replay.text
        assert f"id: {run_id}:1" in replay.text


def test_worker_process_death_interrupts_run_and_keeps_completed_samples(tmp_path) -> None:
    client, app, authorize = _authorized_client(tmp_path)
    with client:
        authorize(client)
        _, plan_id = _api_plan(client)
        connection = open_database(app.state.database_path)
        try:
            plan_repository = RecordRepository(connection, BenchmarkPlan)
            plan = plan_repository.get(UUID(plan_id))
            assert plan is not None
            plan_repository.update(
                BenchmarkPlan.model_validate(
                    plan.model_dump(mode="python") | {"measured_requests": 1_000_000}
                )
            )
            workload_repository = RecordRepository(connection, Workload)
            workload = workload_repository.get(plan.workload_id)
            assert workload is not None
            workload_repository.update(
                Workload.model_validate(
                    workload.model_dump(mode="python") | {"request_count": 1_000_000}
                )
            )
        finally:
            connection.close()

        started = client.post("/api/v1/runs", json={"plan_id": plan_id})
        assert started.status_code == 202, started.text
        run_id = started.json()["run"]["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            saved_samples = client.get(f"/api/v1/runs/{run_id}/samples").json()["samples"]
            if saved_samples:
                break
            time.sleep(0.05)
        assert saved_samples, "the worker did not persist its first measurement sample"
        worker_process = app.state.run_worker_processes[run_id]
        worker_process.kill()
        worker_process.wait(timeout=5)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = client.get(f"/api/v1/runs/{run_id}").json()
            if current["status"] == "interrupted":
                break
            time.sleep(0.05)
        assert current["status"] == "interrupted", current
        assert current["error_code"] == "worker_exited"
        assert len(client.get(f"/api/v1/runs/{run_id}/samples").json()["samples"]) >= 1
        metrics = client.get(f"/api/v1/runs/{run_id}/metrics").json()["metrics"]
        assert (
            next(item for item in metrics if item["metric_name"] == "requests_attempted")["value"]
            >= 1
        )
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        terminal_event = next(
            event
            for event in events
            if event["event_type"] == "run_status" and event["payload"].get("to") == "interrupted"
        )
        metric_sequences = [
            event["sequence_number"] for event in events if event["event_type"] == "run_metric"
        ]
        assert metric_sequences and max(metric_sequences) < terminal_event["sequence_number"]


def test_run_api_cancellation_stops_dispatch_and_preserves_samples(tmp_path) -> None:
    client, app, authorize = _authorized_client(tmp_path)
    with client:
        authorize(client)
        _, plan_id = _api_plan(client)
        connection = open_database(app.state.database_path)
        try:
            plan_repository = RecordRepository(connection, BenchmarkPlan)
            plan = plan_repository.get(UUID(plan_id))
            assert plan is not None
            plan_repository.update(
                BenchmarkPlan.model_validate(
                    plan.model_dump(mode="python") | {"measured_requests": 1_000_000}
                )
            )
            workload_repository = RecordRepository(connection, Workload)
            workload = workload_repository.get(plan.workload_id)
            assert workload is not None
            workload_repository.update(
                Workload.model_validate(
                    workload.model_dump(mode="python") | {"request_count": 1_000_000}
                )
            )
        finally:
            connection.close()

        started = client.post("/api/v1/runs", json={"plan_id": plan_id})
        assert started.status_code == 202, started.text
        run_id = started.json()["run"]["id"]
        deadline = time.monotonic() + 10
        saved_samples = []
        while time.monotonic() < deadline:
            saved_samples = client.get(f"/api/v1/runs/{run_id}/samples").json()["samples"]
            if saved_samples:
                break
            time.sleep(0.05)
        assert saved_samples

        cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = client.get(f"/api/v1/runs/{run_id}").json()
            if current["status"] in {"cancelled", "interrupted", "failed", "completed"}:
                break
            time.sleep(0.05)
        assert current["status"] == "cancelled", current
        samples = client.get(f"/api/v1/runs/{run_id}/samples").json()["samples"]
        assert samples


def test_run_lock_conflict_has_explicit_override_and_warning(tmp_path) -> None:
    client, app, authorize = _authorized_client(tmp_path)
    with client:
        authorize(client)
        engine_id, plan_id = _api_plan(client)
        connection = open_database(app.state.database_path)
        try:
            plan = RecordRepository(connection, BenchmarkPlan).get(UUID(plan_id))
            assert plan is not None
            held_run = BenchmarkRun(plan_id=plan.id, worker_id=str(uuid4()))
            held_run, _ = create_queued_run(
                connection,
                held_run,
                app_instance_id=app.state.app_instance_id,
                resource_keys=[("endpoint", engine_id)],
                override_contention=False,
            )
        finally:
            connection.close()

        blocked = client.post("/api/v1/runs", json={"plan_id": plan_id})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "resource_lock_conflict"
        override = client.post(
            "/api/v1/runs",
            json={"plan_id": plan_id, "override_contention": True},
        )
        assert override.status_code == 202, override.text
        assert override.json()["run"]["potentially_contended"] is True
        run_id = override.json()["run"]["id"]
        warning = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        assert any(
            event["payload"].get("code") == "resource_contention_overridden" for event in warning
        )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if client.get(f"/api/v1/runs/{run_id}").json()["status"] in {
                "completed",
                "failed",
                "interrupted",
            }:
                break
            time.sleep(0.05)
        cleanup = open_database(app.state.database_path)
        try:
            transition_run(cleanup, held_run.id, BenchmarkRunStatus.INTERRUPTED)
        finally:
            cleanup.close()


def test_sse_reconnect_emits_heartbeat_and_client_can_disconnect(tmp_path, monkeypatch) -> None:
    client, app, authorize = _authorized_client(tmp_path)
    monkeypatch.setattr(run_api, "SSE_HEARTBEAT_INTERVAL_SECONDS", 0.01)
    with client:
        authorize(client)
        engine_id, plan_id = _api_plan(client)
        connection = open_database(app.state.database_path)
        try:
            plan = RecordRepository(connection, BenchmarkPlan).get(UUID(plan_id))
            assert plan is not None
            run = BenchmarkRun(plan_id=plan.id, worker_id=str(uuid4()))
            run, _ = create_queued_run(
                connection,
                run,
                app_instance_id=app.state.app_instance_id,
                resource_keys=[("endpoint", engine_id)],
                override_contention=False,
            )
        finally:
            connection.close()

        with client.stream(
            "GET",
            f"/api/v1/runs/{run.id}/events/stream?after=0",
        ) as response:
            assert response.status_code == 200
            lines = response.iter_lines()
            assert next(lines) == "event: heartbeat"
            assert json.loads(next(lines).removeprefix("data: "))["type"] == "heartbeat"

        cleanup = open_database(app.state.database_path)
        try:
            transition_run(cleanup, run.id, BenchmarkRunStatus.INTERRUPTED)
        finally:
            cleanup.close()


def test_run_list_returns_most_recent_records_first(tmp_path) -> None:
    client, app, _ = _authorized_client(tmp_path)
    with client:
        connection = open_database(app.state.database_path)
        try:
            runs = [
                BenchmarkRun(
                    plan_id=uuid4(),
                    created_at=datetime(2026, 1, day, tzinfo=UTC),
                    updated_at=datetime(2026, 1, day, tzinfo=UTC),
                )
                for day in (1, 2, 3)
            ]
            repository = RecordRepository(connection, BenchmarkRun)
            for run in runs:
                repository.create(run)
        finally:
            connection.close()

        response = client.get("/api/v1/runs?limit=2")
        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == [str(runs[2].id), str(runs[1].id)]
