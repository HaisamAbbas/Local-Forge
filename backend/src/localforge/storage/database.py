"""SQLite connections and forward-only schema migrations."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

Migration = Callable[[sqlite3.Connection], None]


class UnsupportedSchemaVersion(RuntimeError):
    """Raised when a database was created by a newer LOCALFORGE version."""


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _migration_2(connection: sqlite3.Connection) -> None:
    """Add typed domain records and append-only raw sample tables."""

    connection.execute(
        """
        CREATE TABLE domain_records (
            entity_type TEXT NOT NULL CHECK (entity_type IN (
                'MachineProfile', 'EngineProfile', 'CapabilitySnapshot', 'ModelArtifact',
                'Dataset', 'Workload', 'BenchmarkPlan', 'BenchmarkRun',
                'ExperimentGroup', 'ReportMetadata'
            )),
            id TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            PRIMARY KEY (entity_type, id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX domain_records_created_at_idx ON domain_records(entity_type, created_at, id)"
    )
    connection.execute(
        """
        CREATE TABLE request_samples (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
            schema_version INTEGER NOT NULL CHECK (schema_version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            content_retained INTEGER NOT NULL CHECK (content_retained IN (0, 1)),
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            UNIQUE (run_id, sequence_number)
        )
        """
    )
    connection.execute(
        "CREATE INDEX request_samples_run_id_idx ON request_samples(run_id, sequence_number)"
    )
    connection.execute(
        """
        CREATE TRIGGER request_samples_require_run
        BEFORE INSERT ON request_samples
        WHEN NOT EXISTS (
            SELECT 1 FROM domain_records
            WHERE entity_type = 'BenchmarkRun' AND id = NEW.run_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'request sample run does not exist');
        END
        """
    )
    connection.execute(
        """
        CREATE TABLE metric_samples (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
        )
        """
    )
    connection.execute(
        "CREATE INDEX metric_samples_run_id_idx ON metric_samples(run_id, observed_at, id)"
    )
    connection.execute(
        """
        CREATE TRIGGER metric_samples_require_run
        BEFORE INSERT ON metric_samples
        WHEN NOT EXISTS (
            SELECT 1 FROM domain_records
            WHERE entity_type = 'BenchmarkRun' AND id = NEW.run_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'metric sample run does not exist');
        END
        """
    )


def _migration_3(connection: sqlite3.Connection) -> None:
    """Add replayable run events, worker leases, and per-resource locks."""

    connection.execute(
        """
        CREATE TABLE run_events (
            run_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL CHECK (sequence_number >= 0),
            event_type TEXT NOT NULL,
            request_id TEXT,
            monotonic_offset_ms REAL NOT NULL CHECK (monotonic_offset_ms >= 0),
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            PRIMARY KEY (run_id, sequence_number)
        )
        """
    )
    connection.execute(
        "CREATE INDEX run_events_type_idx ON run_events(run_id, event_type, sequence_number)"
    )
    connection.execute(
        """
        CREATE TRIGGER run_events_require_run
        BEFORE INSERT ON run_events
        WHEN NOT EXISTS (
            SELECT 1 FROM domain_records
            WHERE entity_type = 'BenchmarkRun' AND id = NEW.run_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'run event benchmark run does not exist');
        END
        """
    )
    connection.execute(
        """
        CREATE TABLE run_event_clocks (
            run_id TEXT PRIMARY KEY,
            origin_monotonic_ns INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE worker_leases (
            run_id TEXT PRIMARY KEY,
            app_instance_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            process_id INTEGER,
            heartbeat_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX worker_leases_heartbeat_idx ON worker_leases(heartbeat_at)")
    connection.execute(
        """
        CREATE TABLE resource_locks (
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            app_instance_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            override_contention INTEGER NOT NULL CHECK (override_contention IN (0, 1)),
            PRIMARY KEY (resource_type, resource_id, run_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX resource_locks_resource_idx ON resource_locks(resource_type, resource_id)"
    )
    connection.execute("CREATE INDEX resource_locks_expiry_idx ON resource_locks(expires_at)")


def _migration_4(connection: sqlite3.Connection) -> None:
    """Record the payload contract version beside each persisted run event."""

    connection.execute(
        """ALTER TABLE run_events
           ADD COLUMN event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version > 0)"""
    )


MIGRATIONS: dict[int, Migration] = {
    1: _migration_1,
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
}
CURRENT_SCHEMA_VERSION = max(MIGRATIONS, default=0)


def open_database(database_path: Path) -> sqlite3.Connection:
    """Open a local SQLite database with bounded lock waits and safe defaults."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    # Each connection is request-scoped; async routes may resume it on the event-loop thread
    # after FastAPI created it in a synchronous dependency worker thread.
    connection = sqlite3.connect(
        database_path,
        timeout=5.0,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_database(database_path: Path) -> int:
    """Apply pending migrations atomically and return the resulting schema version."""

    connection = open_database(database_path)
    try:
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > CURRENT_SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"Database schema {current_version} is newer than supported schema "
                f"{CURRENT_SCHEMA_VERSION}. Upgrade LOCALFORGE before opening this data."
            )

        for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
            migration = MIGRATIONS.get(version)
            if migration is None:
                raise RuntimeError(f"Missing migration for schema version {version}.")

            connection.execute("BEGIN IMMEDIATE")
            try:
                migration(connection)
                connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return CURRENT_SCHEMA_VERSION
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a group of repository operations atomically on an open connection."""

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
