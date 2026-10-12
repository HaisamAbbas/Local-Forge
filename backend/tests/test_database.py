"""Database initialization, repeat-startup, and schema-version tests."""

import sqlite3
from uuid import uuid4

import pytest

import localforge.storage.database as database
from localforge.storage.database import (
    CURRENT_SCHEMA_VERSION,
    UnsupportedSchemaVersion,
    initialize_database,
)


def test_fresh_database_migrates_and_repeated_startup_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "fresh.sqlite3"

    assert initialize_database(database_path) == CURRENT_SCHEMA_VERSION == 5
    assert initialize_database(database_path) == 5

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        columns = connection.execute("PRAGMA table_info(app_metadata)").fetchall()
        sample_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(request_samples)")
        }
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert [column[1] for column in columns] == ["key", "value"]
    assert {
        "domain_records",
        "request_samples",
        "metric_samples",
        "run_events",
        "resource_locks",
        "worker_leases",
    } <= tables
    assert "outcome" in sample_columns


def test_existing_version_one_database_upgrades_to_current_schema(tmp_path) -> None:
    database_path = tmp_path / "version-one.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("PRAGMA user_version = 1")

    assert initialize_database(database_path) == CURRENT_SCHEMA_VERSION == 5

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'domain_records'"
            ).fetchone()[0]
            == 1
        )


def test_version_three_database_adds_event_version_column(tmp_path) -> None:
    database_path = tmp_path / "version-three.sqlite3"
    with sqlite3.connect(database_path) as connection:
        database.MIGRATIONS[1](connection)
        database.MIGRATIONS[2](connection)
        database.MIGRATIONS[3](connection)
        connection.execute("PRAGMA user_version = 3")

    assert initialize_database(database_path) == 5
    with sqlite3.connect(database_path) as connection:
        columns = {column[1] for column in connection.execute("PRAGMA table_info(run_events)")}
        assert "event_version" in columns


def test_interrupted_migration_rolls_back_and_can_be_retried(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "interrupted.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("PRAGMA user_version = 1")

    migration = database.MIGRATIONS[2]

    def interrupted(connection: sqlite3.Connection) -> None:
        migration(connection)
        raise RuntimeError("simulated interrupted upgrade")

    monkeypatch.setitem(database.MIGRATIONS, 2, interrupted)
    with pytest.raises(RuntimeError, match="simulated interrupted"):
        initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'domain_records'"
            ).fetchone()[0]
            == 0
        )

    monkeypatch.setitem(database.MIGRATIONS, 2, migration)
    assert initialize_database(database_path) == 5


def test_version_four_migration_indexes_and_backfills_request_outcomes(tmp_path) -> None:
    database_path = tmp_path / "version-four.sqlite3"
    run_id = str(uuid4())
    sample_id = str(uuid4())
    timestamp = "2026-10-12T00:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        for version in range(1, 5):
            database.MIGRATIONS[version](connection)
        connection.execute(
            """INSERT INTO domain_records
               (entity_type, id, schema_version, created_at, updated_at, payload_json)
               VALUES ('BenchmarkRun', ?, 1, ?, ?, '{}')""",
            (run_id, timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO request_samples
               (id, run_id, sequence_number, schema_version, created_at, updated_at,
                content_retained, payload_json)
               VALUES (?, ?, 0, 1, ?, ?, 0, '{"outcome":"in_progress"}')""",
            (sample_id, run_id, timestamp, timestamp),
        )
        connection.execute("PRAGMA user_version = 4")

    assert initialize_database(database_path) == 5
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT outcome FROM request_samples WHERE id = ?", (sample_id,)
            ).fetchone()[0]
            == "in_progress"
        )
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(request_samples)")}
        assert "request_samples_run_outcome_idx" in indexes


def test_newer_database_schema_fails_without_downgrade(tmp_path) -> None:
    database_path = tmp_path / "future.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(UnsupportedSchemaVersion, match="newer than supported"):
        initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
