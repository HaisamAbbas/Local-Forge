"""User-editable workspace settings persisted in the local SQLite database."""

import json
import os
import sqlite3

from pydantic import Field, ValidationError

from localforge.adapters.catalog import AdapterCatalog, AdapterNotFoundError
from localforge.adapters.contracts import AdapterCategory, AdapterManifest
from localforge.core.models import SchemaModel

SETTINGS_METADATA_KEY = "workspace_settings"
TEST_FIXTURE_DEFAULT_ENVIRONMENT = "LOCALFORGE_SHOW_TEST_FIXTURE_ENGINES"
LOW_DISK_SPACE_THRESHOLD_BYTES = 1024**3


def _environment_fixture_default() -> bool:
    """Development and test harnesses may opt in before any setting has been saved."""

    return os.environ.get(TEST_FIXTURE_DEFAULT_ENVIRONMENT, "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }


class WorkspaceSettings(SchemaModel):
    """Settings honored by the API and UI; every field has a consumer."""

    show_test_fixture_engines: bool = Field(default_factory=_environment_fixture_default)
    default_retain_prompt_output: bool = False
    default_request_timeout_seconds: float = Field(default=120, ge=1, le=3600)
    default_warmup_count: int = Field(default=2, ge=0, le=1000)


class WorkspaceSettingsPatch(SchemaModel):
    show_test_fixture_engines: bool | None = None
    default_retain_prompt_output: bool | None = None
    default_request_timeout_seconds: float | None = Field(default=None, ge=1, le=3600)
    default_warmup_count: int | None = Field(default=None, ge=0, le=1000)


def load_workspace_settings(connection: sqlite3.Connection) -> WorkspaceSettings:
    """Read saved settings, falling back to defaults for missing or unreadable values."""

    row = connection.execute(
        "SELECT value FROM app_metadata WHERE key = ?", (SETTINGS_METADATA_KEY,)
    ).fetchone()
    if row is None:
        return WorkspaceSettings()
    try:
        stored = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return WorkspaceSettings()
    if not isinstance(stored, dict):
        return WorkspaceSettings()
    known = {key: value for key, value in stored.items() if key in WorkspaceSettings.model_fields}
    try:
        return WorkspaceSettings.model_validate(known)
    except ValidationError:
        # Keep each valid saved value; reset only the invalid ones to their defaults.
        valid: dict[str, object] = {}
        for key, value in known.items():
            try:
                WorkspaceSettings.model_validate({key: value})
            except ValidationError:
                continue
            valid[key] = value
        return WorkspaceSettings.model_validate(valid)


def workspace_settings_saved(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM app_metadata WHERE key = ?", (SETTINGS_METADATA_KEY,)
    ).fetchone()
    return row is not None


def save_workspace_settings(
    connection: sqlite3.Connection, settings: WorkspaceSettings
) -> WorkspaceSettings:
    connection.execute(
        """
        INSERT INTO app_metadata (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (SETTINGS_METADATA_KEY, json.dumps(settings.model_dump(mode="json"), sort_keys=True)),
    )
    return settings


def reset_workspace_settings(connection: sqlite3.Connection) -> WorkspaceSettings:
    connection.execute("DELETE FROM app_metadata WHERE key = ?", (SETTINGS_METADATA_KEY,))
    return WorkspaceSettings()


def is_test_fixture_manifest(manifest: AdapterManifest) -> bool:
    return manifest.category is AdapterCategory.TEST_FIXTURE


def list_test_fixture_adapter_ids(catalog: AdapterCatalog) -> list[str]:
    return [
        manifest.adapter_id
        for manifest in catalog.list_manifests()
        if is_test_fixture_manifest(manifest)
    ]


def is_test_fixture_adapter(catalog: AdapterCatalog, adapter_id: str) -> bool:
    try:
        return is_test_fixture_manifest(catalog.manifest_for(adapter_id))
    except AdapterNotFoundError:
        return False


def is_test_fixture_engine_blocked(
    connection: sqlite3.Connection, catalog: AdapterCatalog, adapter_id: str
) -> bool:
    """True when an engine uses a test fixture and fixtures are hidden in settings."""

    return is_test_fixture_adapter(catalog, adapter_id) and not (
        load_workspace_settings(connection).show_test_fixture_engines
    )


TEST_FIXTURE_BLOCKER_CODE = "test_fixture_engine_disabled"
TEST_FIXTURE_BLOCKER_MESSAGE = (
    "This engine is a deterministic test fixture, not a language model. Enable "
    "'Show test fixture engines' in Settings to use it for workflow checks."
)
