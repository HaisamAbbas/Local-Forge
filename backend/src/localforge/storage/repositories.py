"""Typed, parameterized SQLite repositories for domain records and raw samples."""

import json
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel

from localforge.core.models import (
    DOMAIN_RECORD_MODELS,
    BenchmarkRun,
    DomainModel,
    MetricSample,
    RequestSample,
)
from localforge.storage.database import transaction

_ALLOWED_RECORD_TYPES = {model.__name__ for model in DOMAIN_RECORD_MODELS}


class RecordAlreadyExists(RuntimeError):
    """Raised when a record with the same stable ID already exists."""


class RecordNotFound(LookupError):
    """Raised when an update targets a record that does not exist."""


class ContentRetentionNotEnabled(ValueError):
    """Raised when raw prompt/output bodies are stored without explicit opt-in."""


def _serialize(record: BaseModel) -> str:
    try:
        return json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("record contains values that cannot be represented as JSON") from error


def _metadata(record: DomainModel) -> tuple[str, str, str, int, str]:
    payload = _serialize(record)
    created_at = record.created_at.isoformat()
    updated_at = record.updated_at.isoformat()
    schema_version = record.schema_version
    return str(record.id), created_at, updated_at, schema_version, payload


class RecordRepository[RecordT: DomainModel]:
    """CRUD for one typed record class on a caller-owned SQLite connection."""

    def __init__(self, connection: sqlite3.Connection, record_type: type[RecordT]) -> None:
        if record_type.__name__ not in _ALLOWED_RECORD_TYPES:
            raise ValueError(f"unsupported domain record type: {record_type.__name__}")
        self._connection = connection
        self._record_type = record_type
        self._entity_type = record_type.__name__

    def _ensure_record_type(self, record: RecordT) -> None:
        if not isinstance(record, self._record_type):
            raise TypeError(f"expected {self._entity_type}, received {type(record).__name__}")

    def create(self, record: RecordT) -> RecordT:
        self._ensure_record_type(record)
        record_id, created_at, updated_at, schema_version, payload = _metadata(record)
        try:
            self._connection.execute(
                """INSERT INTO domain_records
                   (entity_type, id, schema_version, created_at, updated_at, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (self._entity_type, record_id, schema_version, created_at, updated_at, payload),
            )
        except sqlite3.IntegrityError as error:
            raise RecordAlreadyExists(
                f"{self._entity_type} record {record_id} already exists or is invalid"
            ) from error
        return record

    def get(self, record_id: UUID) -> RecordT | None:
        row = self._connection.execute(
            "SELECT payload_json FROM domain_records WHERE entity_type = ? AND id = ?",
            (self._entity_type, str(record_id)),
        ).fetchone()
        if row is None:
            return None
        return self._record_type.model_validate(json.loads(row["payload_json"]))

    def list(self, limit: int = 100) -> list[RecordT]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self._connection.execute(
            """SELECT payload_json FROM domain_records
               WHERE entity_type = ? ORDER BY created_at, id LIMIT ?""",
            (self._entity_type, limit),
        ).fetchall()
        return [self._record_type.model_validate(json.loads(row["payload_json"])) for row in rows]

    def update(self, record: RecordT) -> RecordT:
        self._ensure_record_type(record)
        updated_at = max(datetime.now(UTC), record.created_at, record.updated_at)
        updated = record.model_copy(update={"updated_at": updated_at})
        record_id, created_at, persisted_updated_at, schema_version, payload = _metadata(updated)
        cursor = self._connection.execute(
            """UPDATE domain_records SET schema_version = ?, created_at = ?, updated_at = ?,
               payload_json = ? WHERE entity_type = ? AND id = ?""",
            (
                schema_version,
                created_at,
                persisted_updated_at,
                payload,
                self._entity_type,
                record_id,
            ),
        )
        if cursor.rowcount == 0:
            raise RecordNotFound(f"{self._entity_type} record {record_id} was not found")
        return updated

    def delete(self, record_id: UUID) -> bool:
        if self._record_type is BenchmarkRun:
            raise ValueError(
                "use delete_benchmark_run to remove a run and its raw samples together"
            )
        cursor = self._connection.execute(
            "DELETE FROM domain_records WHERE entity_type = ? AND id = ?",
            (self._entity_type, str(record_id)),
        )
        return cursor.rowcount > 0


class RequestSampleRepository:
    """Append-only request sample storage with explicit inline-body opt-in."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self, sample: RequestSample, *, allow_content_retention: bool = False
    ) -> RequestSample:
        if (
            sample.prompt_text is not None or sample.output_text is not None
        ) and not allow_content_retention:
            raise ContentRetentionNotEnabled(
                "enable content retention in the benchmark plan before storing prompt/output bodies"
            )
        sample_id, created_at, updated_at, schema_version, payload = _metadata(sample)
        try:
            self._connection.execute(
                """INSERT INTO request_samples
                   (id, run_id, sequence_number, schema_version, created_at, updated_at,
                    content_retained, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sample_id,
                    str(sample.run_id),
                    sample.sequence_number,
                    schema_version,
                    created_at,
                    updated_at,
                    int(sample.content_retained),
                    payload,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise RecordAlreadyExists(
                f"request sample {sample_id} already exists or its run/sequence is invalid"
            ) from error
        return sample

    def get(self, sample_id: UUID) -> RequestSample | None:
        row = self._connection.execute(
            "SELECT payload_json FROM request_samples WHERE id = ?", (str(sample_id),)
        ).fetchone()
        if row is None:
            return None
        return RequestSample.model_validate(json.loads(row["payload_json"]))

    def list_for_run(
        self,
        run_id: UUID,
        *,
        after_sequence_number: int = -1,
        limit: int = 500,
    ) -> list[RequestSample]:
        if after_sequence_number < -1 or not 1 <= limit <= 1000:
            raise ValueError("sample cursor or page size is out of range")
        rows = self._connection.execute(
            """SELECT payload_json FROM request_samples
               WHERE run_id = ? AND sequence_number > ?
               ORDER BY sequence_number LIMIT ?""",
            (str(run_id), after_sequence_number, limit),
        ).fetchall()
        return [RequestSample.model_validate(json.loads(row["payload_json"])) for row in rows]


class MetricSampleRepository:
    """Append-only metric sample storage that preserves source, unit and labels."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, sample: MetricSample) -> MetricSample:
        sample_id, created_at, updated_at, schema_version, payload = _metadata(sample)
        try:
            self._connection.execute(
                """INSERT INTO metric_samples
                   (id, run_id, metric_name, observed_at, schema_version, created_at,
                    updated_at, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sample_id,
                    str(sample.run_id),
                    sample.metric_name,
                    sample.observed_at.isoformat(),
                    schema_version,
                    created_at,
                    updated_at,
                    payload,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise RecordAlreadyExists(
                f"metric sample {sample_id} already exists or its run is invalid"
            ) from error
        return sample

    def list_for_run(self, run_id: UUID) -> list[MetricSample]:
        rows = self._connection.execute(
            "SELECT payload_json FROM metric_samples WHERE run_id = ? ORDER BY observed_at, id",
            (str(run_id),),
        ).fetchall()
        return [MetricSample.model_validate(json.loads(row["payload_json"])) for row in rows]


def delete_retained_content(connection: sqlite3.Connection, run_id: UUID) -> int:
    """Clear inline bodies and content references while preserving run measurements."""

    cleared = 0
    with transaction(connection):
        rows = connection.execute(
            "SELECT id, payload_json FROM request_samples WHERE run_id = ?", (str(run_id),)
        ).fetchall()
        for row in rows:
            sample = RequestSample.model_validate(json.loads(row["payload_json"]))
            has_retained_content = any(
                (
                    sample.prompt_text,
                    sample.output_text,
                    sample.prompt_reference,
                    sample.output_reference,
                )
            )
            if not has_retained_content:
                continue
            updated_at = max(datetime.now(UTC), sample.created_at, sample.updated_at)
            redacted = sample.model_copy(
                update={
                    "updated_at": updated_at,
                    "content_retained": False,
                    "prompt_text": None,
                    "output_text": None,
                    "prompt_reference": None,
                    "output_reference": None,
                }
            )
            connection.execute(
                """UPDATE request_samples SET content_retained = 0, updated_at = ?, payload_json = ?
                   WHERE id = ?""",
                (updated_at.isoformat(), _serialize(redacted), row["id"]),
            )
            cleared += 1
    return cleared


def delete_benchmark_run(connection: sqlite3.Connection, run_id: UUID) -> bool:
    """Delete a run and its raw samples atomically, leaving referenced setup records intact."""

    with transaction(connection):
        connection.execute("DELETE FROM request_samples WHERE run_id = ?", (str(run_id),))
        connection.execute("DELETE FROM metric_samples WHERE run_id = ?", (str(run_id),))
        connection.execute("DELETE FROM run_events WHERE run_id = ?", (str(run_id),))
        connection.execute("DELETE FROM run_event_clocks WHERE run_id = ?", (str(run_id),))
        connection.execute("DELETE FROM resource_locks WHERE run_id = ?", (str(run_id),))
        connection.execute("DELETE FROM worker_leases WHERE run_id = ?", (str(run_id),))
        cursor = connection.execute(
            "DELETE FROM domain_records WHERE entity_type = ? AND id = ?",
            (BenchmarkRun.__name__, str(run_id)),
        )
    return cursor.rowcount > 0
