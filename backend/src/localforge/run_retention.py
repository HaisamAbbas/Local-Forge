"""Delete finished benchmark runs and keep experiment groups consistent afterwards."""

import json
import sqlite3
from collections.abc import Collection, Iterable
from datetime import UTC, datetime
from uuid import UUID

from localforge.core.models import (
    BenchmarkRun,
    BenchmarkRunStatus,
    ExperimentGroup,
    ExperimentTrialStatus,
)
from localforge.runs import TERMINAL_RUN_STATUSES
from localforge.storage.database import transaction
from localforge.storage.repositories import RecordRepository, delete_benchmark_run_rows

DELETED_RUN_TRIAL_ERROR = "The benchmark run {run_id} for this trial was deleted."


class RunStillActive(RuntimeError):
    """Raised when a deletion targets a run that has not reached a terminal status."""

    def __init__(self, run: BenchmarkRun) -> None:
        super().__init__(f"benchmark run {run.id} is still {run.status.value}")
        self.run = run


def _detach_deleted_runs(connection: sqlite3.Connection, deleted: set[UUID]) -> None:
    """Record trials of deleted runs as failed trials without a run instead of dangling links."""

    repository = RecordRepository(connection, ExperimentGroup)
    rows = connection.execute(
        "SELECT payload_json FROM domain_records WHERE entity_type = ?",
        (ExperimentGroup.__name__,),
    ).fetchall()
    for row in rows:
        group = ExperimentGroup.model_validate(json.loads(row["payload_json"]))
        if not any(trial.run_id in deleted for trial in group.trials) and not deleted.intersection(
            group.run_ids
        ):
            continue
        trials = [
            trial.model_copy(
                update={
                    "run_id": None,
                    "status": ExperimentTrialStatus.FAILED,
                    "launch_started_at": None,
                    "error": DELETED_RUN_TRIAL_ERROR.format(run_id=trial.run_id),
                }
            )
            if trial.run_id in deleted
            else trial
            for trial in group.trials
        ]
        repository.update(
            ExperimentGroup.model_validate(
                group.model_dump(mode="python")
                | {
                    "trials": trials,
                    "run_ids": [run_id for run_id in group.run_ids if run_id not in deleted],
                }
            )
        )


def _delete_in_transaction(connection: sqlite3.Connection, run_ids: Iterable[UUID]) -> list[UUID]:
    repository = RecordRepository(connection, BenchmarkRun)
    deleted: list[UUID] = []
    for run_id in dict.fromkeys(run_ids):
        run = repository.get(run_id)
        if run is None:
            continue
        if run.status not in TERMINAL_RUN_STATUSES:
            raise RunStillActive(run)
        if delete_benchmark_run_rows(connection, run_id):
            deleted.append(run_id)
    if deleted:
        _detach_deleted_runs(connection, set(deleted))
    return deleted


def delete_terminal_runs(connection: sqlite3.Connection, run_ids: Iterable[UUID]) -> list[UUID]:
    """Atomically delete terminal runs with their samples, events and locks.

    Missing runs are skipped. A non-terminal run aborts the whole deletion with
    :class:`RunStillActive`. Saved reports are snapshots and are left untouched.
    """

    with transaction(connection):
        return _delete_in_transaction(connection, run_ids)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _select_cleanup_candidates(
    connection: sqlite3.Connection,
    *,
    before: datetime | None,
    statuses: Collection[BenchmarkRunStatus] | None,
    keep_latest: int | None,
) -> list[UUID]:
    rows = connection.execute(
        """SELECT payload_json FROM domain_records
           WHERE entity_type = ? ORDER BY created_at DESC, id DESC""",
        (BenchmarkRun.__name__,),
    ).fetchall()
    terminal = [
        run
        for run in (BenchmarkRun.model_validate(json.loads(row["payload_json"])) for row in rows)
        if run.status in TERMINAL_RUN_STATUSES
    ]
    cutoff = _as_utc(before) if before is not None else None
    candidates = terminal[keep_latest:] if keep_latest else terminal
    return [
        run.id
        for run in candidates
        if (cutoff is None or run.created_at < cutoff)
        and (statuses is None or run.status in statuses)
    ]


def cleanup_runs(
    connection: sqlite3.Connection,
    *,
    before: datetime | None = None,
    statuses: Collection[BenchmarkRunStatus] | None = None,
    keep_latest: int | None = None,
    dry_run: bool = True,
) -> list[UUID]:
    """Select (and unless ``dry_run`` delete) terminal runs matching every given filter.

    ``keep_latest`` protects the N most recently created terminal runs before the other
    filters apply. ``before`` compares against each run's creation time. Results are newest first.
    """

    if dry_run:
        return _select_cleanup_candidates(
            connection, before=before, statuses=statuses, keep_latest=keep_latest
        )
    with transaction(connection):
        selected = _select_cleanup_candidates(
            connection, before=before, statuses=statuses, keep_latest=keep_latest
        )
        return _delete_in_transaction(connection, selected)
