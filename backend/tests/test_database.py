"""Database initialization, repeat-startup, and schema-version tests."""

import sqlite3

import pytest

import localforge.storage.database as database
from localforge.storage.database import (
    CURRENT_SCHEMA_VERSION,
    UnsupportedSchemaVersion,
    initialize_database,
)


def test_fresh_database_migrates_and_repeated_startup_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "fresh.sqlite3"

    assert initialize_database(database_path) == CURRENT_SCHEMA_VERSION == 2
    assert initialize_database(database_path) == 2

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = connection.execute("PRAGMA table_info(app_metadata)").fetchall()
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert [column[1] for column in columns] == ["key", "value"]
    assert {"domain_records", "request_samples", "metric_samples"} <= tables


def test_existing_version_one_database_upgrades_to_current_schema(tmp_path) -> None:
    database_path = tmp_path / "version-one.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("PRAGMA user_version = 1")

    assert initialize_database(database_path) == CURRENT_SCHEMA_VERSION == 2

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'domain_records'"
            ).fetchone()[0]
            == 1
        )


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
    assert initialize_database(database_path) == 2


def test_newer_database_schema_fails_without_downgrade(tmp_path) -> None:
    database_path = tmp_path / "future.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(UnsupportedSchemaVersion, match="newer than supported"):
        initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
