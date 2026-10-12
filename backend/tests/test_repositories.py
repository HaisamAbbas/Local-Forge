"""Typed SQLite CRUD, transaction, raw sample, and retention behavior."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from localforge.core.models import (
    BenchmarkRun,
    EngineIntegrationMode,
    EngineProfile,
    MachineProfile,
    MetricSample,
    MetricSource,
    RequestOutcome,
    RequestSample,
)
from localforge.storage.database import initialize_database, open_database, transaction
from localforge.storage.repositories import (
    ContentRetentionNotEnabled,
    MetricSampleRepository,
    RecordAlreadyExists,
    RecordNotFound,
    RecordRepository,
    RequestSampleRepository,
    delete_benchmark_run,
    delete_retained_content,
)


@pytest.fixture
def connection(tmp_path):
    path = tmp_path / "repository.sqlite3"
    initialize_database(path)
    database = open_database(path)
    try:
        yield database
    finally:
        database.close()


def test_core_repository_crud_uses_the_supplied_temporary_database(connection) -> None:
    repository = RecordRepository(connection, MachineProfile)
    original = MachineProfile(
        label="First profile",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    repository.create(original)
    assert repository.get(original.id) == original
    assert repository.list() == [original]
    with pytest.raises(RecordAlreadyExists, match="already exists"):
        repository.create(original)

    updated = repository.update(original.model_copy(update={"label": "Updated profile"}))
    assert updated.label == "Updated profile"
    assert updated.updated_at > original.updated_at
    assert repository.get(original.id) == updated
    with pytest.raises(RecordNotFound, match="was not found"):
        repository.update(MachineProfile(label="Missing"))
    assert repository.delete(original.id)
    assert repository.get(original.id) is None
    assert not repository.delete(original.id)


def test_repository_operations_roll_back_together(connection) -> None:
    repository = RecordRepository(connection, MachineProfile)
    profile = MachineProfile(label="Rolled back")

    with pytest.raises(RuntimeError, match="abort batch"):
        with transaction(connection):
            repository.create(profile)
            raise RuntimeError("abort batch")

    assert repository.get(profile.id) is None


def _persist_run(connection) -> BenchmarkRun:
    run = BenchmarkRun(plan_id=uuid4())
    RecordRepository(connection, BenchmarkRun).create(run)
    return run


def test_raw_samples_preserve_metric_fields_and_default_to_redacted_content(connection) -> None:
    run = _persist_run(connection)
    request_repository = RequestSampleRepository(connection)
    metric_repository = MetricSampleRepository(connection)
    request = RequestSample(
        run_id=run.id,
        sequence_number=0,
        outcome=RequestOutcome.SUCCEEDED,
        started_at=datetime(2026, 10, 12, 12, 0, tzinfo=UTC),
        prompt_reference="sample://redacted/0",
        redacted_summary="prompt body omitted",
    )
    metric = MetricSample(
        run_id=run.id,
        metric_name="tokens_per_second",
        value=42.25,
        unit="tokens/s",
        source=MetricSource.ENGINE_NATIVE,
        observed_at=datetime(2026, 10, 12, 12, 0, tzinfo=UTC),
        labels={"device": "gpu:0"},
    )

    request_repository.create(request)
    metric_repository.create(metric)

    stored_request = request_repository.list_for_run(run.id)[0]
    stored_metric = metric_repository.list_for_run(run.id)[0]
    assert stored_request.prompt_text is None
    assert stored_request.content_retained is False
    assert stored_request.prompt_reference == "sample://redacted/0"
    assert stored_metric.value == 42.25
    assert stored_metric.unit == "tokens/s"
    assert stored_metric.source is MetricSource.ENGINE_NATIVE
    assert stored_metric.observed_at == metric.observed_at
    assert stored_metric.labels == {"device": "gpu:0"}


def test_clearing_retained_content_and_deleting_a_run_are_distinct(connection) -> None:
    run = _persist_run(connection)
    profile_repository = RecordRepository(connection, EngineProfile)
    profile = EngineProfile(
        label="Keep this profile",
        adapter_id="fake",
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
    )
    profile_repository.create(profile)
    request_repository = RequestSampleRepository(connection)
    metric_repository = MetricSampleRepository(connection)
    request = RequestSample(
        run_id=run.id,
        sequence_number=0,
        outcome=RequestOutcome.SUCCEEDED,
        started_at=datetime.now(UTC),
        prompt_reference="content://prompt/0",
        output_reference="content://output/0",
        redacted_summary="request bodies were retained by explicit choice",
        content_retained=True,
        prompt_text="private prompt",
        output_text="private answer",
    )
    metric = MetricSample(
        run_id=run.id,
        metric_name="latency",
        value=10,
        unit="ms",
        source=MetricSource.CLIENT_MEASURED,
    )
    request_repository.create(request, allow_content_retention=True)
    metric_repository.create(metric)

    assert delete_retained_content(connection, run.id) == 1
    redacted = request_repository.list_for_run(run.id)[0]
    assert redacted.prompt_text is None
    assert redacted.output_text is None
    assert redacted.prompt_reference is None
    assert redacted.output_reference is None
    assert redacted.content_retained is False
    assert redacted.redacted_summary == request.redacted_summary
    assert RecordRepository(connection, BenchmarkRun).get(run.id) == run
    assert metric_repository.list_for_run(run.id) == [metric]

    with pytest.raises(ValueError, match="delete_benchmark_run"):
        RecordRepository(connection, BenchmarkRun).delete(run.id)
    assert delete_benchmark_run(connection, run.id)
    assert RecordRepository(connection, BenchmarkRun).get(run.id) is None
    assert request_repository.list_for_run(run.id) == []
    assert metric_repository.list_for_run(run.id) == []
    assert profile_repository.get(profile.id) == profile


def test_request_bodies_cannot_be_persisted_without_explicit_opt_in(connection) -> None:
    run = _persist_run(connection)
    sample = RequestSample(
        run_id=run.id,
        sequence_number=0,
        outcome=RequestOutcome.SUCCEEDED,
        started_at=datetime.now(UTC),
        content_retained=True,
        prompt_text="private prompt",
    )

    with pytest.raises(ContentRetentionNotEnabled, match="enable content retention"):
        RequestSampleRepository(connection).create(sample)


def test_workload_and_engine_rows_use_separate_entity_types(connection) -> None:
    engine = EngineProfile(
        label="Local engine",
        adapter_id="fake",
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
    )
    repository = RecordRepository(connection, EngineProfile)
    repository.create(engine)

    assert repository.get(engine.id) == engine
    assert RecordRepository(connection, MachineProfile).list() == []
