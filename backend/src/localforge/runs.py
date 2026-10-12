"""Durable run transitions, event history, worker leases, and resource locks."""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from localforge.core.models import (
    BenchmarkRun,
    BenchmarkRunStatus,
    JsonValue,
    RunEventRecord,
    utc_now,
)
from localforge.storage.database import transaction
from localforge.storage.repositories import RecordRepository

ACTIVE_RUN_STATUSES = frozenset(
    {
        BenchmarkRunStatus.QUEUED,
        BenchmarkRunStatus.PREFLIGHT,
        BenchmarkRunStatus.PREPARING,
        BenchmarkRunStatus.WARMING,
        BenchmarkRunStatus.RUNNING,
        BenchmarkRunStatus.CANCELLING,
    }
)
TERMINAL_RUN_STATUSES = frozenset(
    {
        BenchmarkRunStatus.COMPLETED,
        BenchmarkRunStatus.FAILED,
        BenchmarkRunStatus.CANCELLED,
        BenchmarkRunStatus.INTERRUPTED,
    }
)
_ALLOWED_TRANSITIONS: dict[BenchmarkRunStatus, frozenset[BenchmarkRunStatus]] = {
    BenchmarkRunStatus.QUEUED: frozenset(
        {
            BenchmarkRunStatus.PREPARING,
            BenchmarkRunStatus.CANCELLING,
            BenchmarkRunStatus.FAILED,
            BenchmarkRunStatus.INTERRUPTED,
        }
    ),
    BenchmarkRunStatus.PREFLIGHT: frozenset(
        {
            BenchmarkRunStatus.PREPARING,
            BenchmarkRunStatus.CANCELLING,
            BenchmarkRunStatus.FAILED,
            BenchmarkRunStatus.INTERRUPTED,
        }
    ),
    BenchmarkRunStatus.PREPARING: frozenset(
        {
            BenchmarkRunStatus.WARMING,
            BenchmarkRunStatus.RUNNING,
            BenchmarkRunStatus.CANCELLING,
            BenchmarkRunStatus.FAILED,
            BenchmarkRunStatus.INTERRUPTED,
        }
    ),
    BenchmarkRunStatus.WARMING: frozenset(
        {
            BenchmarkRunStatus.RUNNING,
            BenchmarkRunStatus.CANCELLING,
            BenchmarkRunStatus.FAILED,
            BenchmarkRunStatus.INTERRUPTED,
        }
    ),
    BenchmarkRunStatus.RUNNING: frozenset(
        {
            BenchmarkRunStatus.CANCELLING,
            BenchmarkRunStatus.COMPLETED,
            BenchmarkRunStatus.FAILED,
            BenchmarkRunStatus.INTERRUPTED,
        }
    ),
    BenchmarkRunStatus.CANCELLING: frozenset(
        {
            BenchmarkRunStatus.CANCELLED,
            BenchmarkRunStatus.FAILED,
            BenchmarkRunStatus.INTERRUPTED,
        }
    ),
    BenchmarkRunStatus.COMPLETED: frozenset(),
    BenchmarkRunStatus.FAILED: frozenset(),
    BenchmarkRunStatus.CANCELLED: frozenset(),
    BenchmarkRunStatus.INTERRUPTED: frozenset(),
}
_FORBIDDEN_EVENT_BODY_KEYS = frozenset(
    {"prompt", "prompttext", "output", "outputtext", "text", "content", "body", "responsebody"}
)
_FORBIDDEN_EVENT_SECRET_PARTS = frozenset(
    {
        "authorization",
        "password",
        "credential",
        "secret",
        "apikey",
        "accesskey",
        "privatekey",
        "bearer",
    }
)


class InvalidRunTransition(ValueError):
    """Raised when a persisted run is asked to make an illegal state transition."""


class UnsafeRunEvent(ValueError):
    """Raised when an event payload includes request bodies or credential-like fields."""


class ResourceLockConflict(RuntimeError):
    """Raised when a run cannot atomically acquire its requested resources."""

    def __init__(self, resources: list[str]) -> None:
        super().__init__("one or more benchmark resources are already locked")
        self.resources = resources


def _safe_payload(payload: dict[str, JsonValue]) -> str:
    def inspect(value: JsonValue) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = "".join(
                    character for character in key.casefold() if character.isalnum()
                )
                secret_value = any(part in normalized for part in _FORBIDDEN_EVENT_SECRET_PARTS)
                token_value = normalized == "token" or normalized.endswith("token")
                body_value = (
                    normalized in _FORBIDDEN_EVENT_BODY_KEYS
                    or (normalized.startswith("prompt") and normalized != "prompttokens")
                    or (normalized.startswith("output") and normalized != "outputtokens")
                )
                if body_value or secret_value or token_value:
                    raise UnsafeRunEvent("event payload contains a private or credential field")
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(payload)
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("event payload is not valid finite JSON") from error


def _next_sequence(connection: sqlite3.Connection, run_id: UUID) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(sequence_number), -1) + 1 FROM run_events WHERE run_id = ?",
        (str(run_id),),
    ).fetchone()
    return int(row[0])


def _append_event(
    connection: sqlite3.Connection,
    run_id: UUID,
    event_type: str,
    payload: dict[str, JsonValue],
    *,
    request_id: UUID | None = None,
) -> RunEventRecord:
    payload_json = _safe_payload(payload)
    now = utc_now()
    monotonic_ns = time.monotonic_ns()
    origin = connection.execute(
        "SELECT origin_monotonic_ns FROM run_event_clocks WHERE run_id = ?",
        (str(run_id),),
    ).fetchone()
    if origin is None:
        connection.execute(
            "INSERT INTO run_event_clocks (run_id, origin_monotonic_ns) VALUES (?, ?)",
            (str(run_id), monotonic_ns),
        )
        origin_ns = monotonic_ns
    else:
        origin_ns = int(origin[0])
    sequence_number = _next_sequence(connection, run_id)
    offset_ms = max(0.0, (monotonic_ns - origin_ns) / 1_000_000)
    connection.execute(
        """INSERT INTO run_events
           (run_id, sequence_number, event_type, request_id, monotonic_offset_ms,
            occurred_at, payload_json, event_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(run_id),
            sequence_number,
            event_type,
            str(request_id) if request_id is not None else None,
            offset_ms,
            now.isoformat(),
            payload_json,
            1,
        ),
    )
    return RunEventRecord(
        run_id=run_id,
        sequence_number=sequence_number,
        event_type=event_type,
        request_id=request_id,
        monotonic_offset_ms=offset_ms,
        occurred_at=now,
        payload=cast(dict[str, JsonValue], json.loads(payload_json)),
    )


def create_run_event_clock(connection: sqlite3.Connection, run_id: UUID) -> None:
    """Create the process-independent monotonic origin for a run's event sequence."""

    connection.execute(
        "INSERT INTO run_event_clocks (run_id, origin_monotonic_ns) VALUES (?, ?)",
        (str(run_id), time.monotonic_ns()),
    )


def create_queued_run(
    connection: sqlite3.Connection,
    run: BenchmarkRun,
    *,
    app_instance_id: str,
    resource_keys: list[tuple[str, str]],
    override_contention: bool,
) -> tuple[BenchmarkRun, list[str]]:
    """Persist the queued run, its first events, worker lease, and acquired locks."""

    now = utc_now()
    expiry = now + timedelta(seconds=15)
    conflicting_resources: list[str] = []
    with transaction(connection):
        connection.execute("DELETE FROM resource_locks WHERE expires_at < ?", (now.isoformat(),))
        for resource_type, resource_id in resource_keys:
            rows = connection.execute(
                """SELECT run_id FROM resource_locks
                   WHERE resource_type = ? AND resource_id = ? AND expires_at >= ?
                   ORDER BY acquired_at, run_id""",
                (resource_type, resource_id, now.isoformat()),
            ).fetchall()
            conflicting_resources.extend(
                f"{resource_type} {resource_id} is held by active run {row[0]}" for row in rows
            )
        if conflicting_resources and not override_contention:
            raise ResourceLockConflict(conflicting_resources)
        run = BenchmarkRun.model_validate(
            run.model_dump(mode="python") | {"potentially_contended": bool(conflicting_resources)}
        )
        RecordRepository(connection, BenchmarkRun).create(run)
        create_run_event_clock(connection, run.id)
        _append_event(
            connection,
            run.id,
            "run_status",
            {"from": None, "to": BenchmarkRunStatus.QUEUED.value},
        )
        connection.execute(
            """INSERT INTO worker_leases
               (run_id, app_instance_id, worker_id, process_id, heartbeat_at)
               VALUES (?, ?, ?, NULL, ?)""",
            (str(run.id), app_instance_id, cast(str, run.worker_id), now.isoformat()),
        )
        for resource_type, resource_id in resource_keys:
            connection.execute(
                """INSERT INTO resource_locks
                   (resource_type, resource_id, run_id, app_instance_id, worker_id,
                    acquired_at, expires_at, override_contention)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    resource_type,
                    resource_id,
                    str(run.id),
                    app_instance_id,
                    cast(str, run.worker_id),
                    now.isoformat(),
                    expiry.isoformat(),
                    int(override_contention),
                ),
            )
        if conflicting_resources:
            _append_event(
                connection,
                run.id,
                "warning",
                {
                    "code": "resource_contention_overridden",
                    "message": "This run may contend with another active benchmark.",
                    "resources": cast(JsonValue, conflicting_resources),
                },
            )
    return run, conflicting_resources


def append_run_event(
    connection: sqlite3.Connection,
    run_id: UUID,
    event_type: str,
    payload: dict[str, JsonValue],
    *,
    request_id: UUID | None = None,
) -> RunEventRecord:
    """Persist one sanitized event and return its stable monotonically increasing ID."""

    with transaction(connection):
        return _append_event(connection, run_id, event_type, payload, request_id=request_id)


def transition_run(
    connection: sqlite3.Connection,
    run_id: UUID,
    status: BenchmarkRunStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> BenchmarkRun:
    """Validate, persist, and emit one run lifecycle transition atomically."""

    with transaction(connection):
        repository = RecordRepository(connection, BenchmarkRun)
        run = repository.get(run_id)
        if run is None:
            raise LookupError("benchmark run was not found")
        if run.status is status:
            return run
        if status not in _ALLOWED_TRANSITIONS[run.status]:
            raise InvalidRunTransition(
                f"cannot move a run from {run.status.value} to {status.value}"
            )
        now = utc_now()
        started_at = run.started_at
        if status in {
            BenchmarkRunStatus.PREPARING,
            BenchmarkRunStatus.WARMING,
            BenchmarkRunStatus.RUNNING,
        }:
            started_at = started_at or now
        completed_at = now if status in TERMINAL_RUN_STATUSES else None
        updated = BenchmarkRun.model_validate(
            run.model_dump(mode="python")
            | {
                "status": status,
                "started_at": started_at,
                "completed_at": completed_at,
                "error_code": error_code,
                "error_message": error_message,
            }
        )
        saved = repository.update(updated)
        _append_event(
            connection,
            run_id,
            "run_status",
            cast(
                dict[str, JsonValue],
                {
                    "from": run.status.value,
                    "to": status.value,
                    "error_code": error_code,
                    "message": error_message,
                },
            ),
        )
        if status in TERMINAL_RUN_STATUSES:
            connection.execute("DELETE FROM resource_locks WHERE run_id = ?", (str(run_id),))
            connection.execute("DELETE FROM worker_leases WHERE run_id = ?", (str(run_id),))
        return saved


def emit_run_event(
    connection: sqlite3.Connection,
    run_id: UUID,
    event_type: str,
    payload: dict[str, JsonValue],
    *,
    request_id: UUID | None = None,
) -> RunEventRecord:
    return append_run_event(connection, run_id, event_type, payload, request_id=request_id)


def list_run_events(
    connection: sqlite3.Connection,
    run_id: UUID,
    *,
    after: int = -1,
    limit: int = 500,
) -> list[RunEventRecord]:
    if after < -1 or not 1 <= limit <= 1000:
        raise ValueError("event cursor or page size is out of range")
    rows = connection.execute(
        """SELECT sequence_number, event_type, request_id, monotonic_offset_ms,
                  occurred_at, payload_json, event_version
           FROM run_events WHERE run_id = ? AND sequence_number > ?
           ORDER BY sequence_number LIMIT ?""",
        (str(run_id), after, limit),
    ).fetchall()
    return [
        RunEventRecord(
            run_id=run_id,
            schema_version=row["event_version"],
            sequence_number=row["sequence_number"],
            event_type=row["event_type"],
            request_id=UUID(row["request_id"]) if row["request_id"] else None,
            monotonic_offset_ms=row["monotonic_offset_ms"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            payload=json.loads(row["payload_json"]),
        )
        for row in rows
    ]


def heartbeat_worker(
    connection: sqlite3.Connection,
    *,
    run_id: UUID,
    app_instance_id: str,
    worker_id: str,
    process_id: int,
    lease_seconds: int = 15,
) -> None:
    now = utc_now()
    expiry = now + timedelta(seconds=lease_seconds)
    with transaction(connection):
        connection.execute(
            """INSERT INTO worker_leases
               (run_id, app_instance_id, worker_id, process_id, heartbeat_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                 app_instance_id = excluded.app_instance_id,
                 worker_id = excluded.worker_id,
                 process_id = excluded.process_id,
                 heartbeat_at = excluded.heartbeat_at""",
            (str(run_id), app_instance_id, worker_id, process_id, now.isoformat()),
        )
        connection.execute(
            """UPDATE resource_locks SET expires_at = ?
               WHERE run_id = ? AND worker_id = ?""",
            (expiry.isoformat(), str(run_id), worker_id),
        )


def recover_runs_on_startup(
    connection: sqlite3.Connection,
    *,
    app_instance_id: str,
) -> int:
    """Mark every nonterminal run from a previous API lifetime interrupted."""

    del app_instance_id  # Each API lifespan owns a fresh set of child workers.
    rows = connection.execute(
        "SELECT payload_json FROM domain_records WHERE entity_type = 'BenchmarkRun'"
    ).fetchall()
    runs = [BenchmarkRun.model_validate(json.loads(row[0])) for row in rows]
    interrupted = 0
    for run in runs:
        if run.status not in ACTIVE_RUN_STATUSES:
            continue
        transition_run(
            connection,
            run.id,
            BenchmarkRunStatus.INTERRUPTED,
            error_code="app_restarted",
            error_message="The local app restarted before this run finished.",
        )
        interrupted += 1
    with transaction(connection):
        connection.execute("DELETE FROM resource_locks")
        connection.execute("DELETE FROM worker_leases")
    return interrupted


def reconcile_stale_workers(
    connection: sqlite3.Connection,
    *,
    stale_after_seconds: int = 15,
) -> int:
    """Interrupt runs whose heartbeat lease expired and release only their locks."""

    cutoff = (utc_now() - timedelta(seconds=stale_after_seconds)).isoformat()
    stale = connection.execute(
        "SELECT run_id FROM worker_leases WHERE heartbeat_at < ?", (cutoff,)
    ).fetchall()
    reconciled = 0
    for row in stale:
        run_id = UUID(row[0])
        try:
            run = RecordRepository(connection, BenchmarkRun).get(run_id)
            if run is not None and run.status in ACTIVE_RUN_STATUSES:
                transition_run(
                    connection,
                    run_id,
                    BenchmarkRunStatus.INTERRUPTED,
                    error_code="worker_lease_expired",
                    error_message="The benchmark worker stopped reporting its heartbeat.",
                )
                reconciled += 1
            else:
                with transaction(connection):
                    connection.execute(
                        "DELETE FROM resource_locks WHERE run_id = ?", (str(run_id),)
                    )
                    connection.execute("DELETE FROM worker_leases WHERE run_id = ?", (str(run_id),))
        except (InvalidRunTransition, LookupError):
            continue
    return reconciled
