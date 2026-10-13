"""Workspace settings and read-only local storage status routes."""

import shutil
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.contracts import AdapterManifest
from localforge.api.dependencies import DatabaseConnection, LocalAdapterCatalog
from localforge.core.models import SchemaModel
from localforge.storage.database import open_database
from localforge.workspace_settings import (
    LOW_DISK_SPACE_THRESHOLD_BYTES,
    WorkspaceSettings,
    WorkspaceSettingsPatch,
    is_test_fixture_manifest,
    list_test_fixture_adapter_ids,
    load_workspace_settings,
    save_workspace_settings,
    workspace_settings_saved,
)

router = APIRouter(prefix="/api/v1")


class StorageStatus(SchemaModel):
    data_directory: str
    database_path: str
    free_bytes: int | None
    total_bytes: int | None
    low_space: bool | None
    low_space_threshold_bytes: int


class WorkspaceSettingsResponse(SchemaModel):
    settings: WorkspaceSettings
    defaults: WorkspaceSettings
    saved: bool
    test_fixture_adapter_ids: list[str]
    storage: StorageStatus


def storage_status(data_directory: Path, database_path: Path) -> StorageStatus:
    """Report free space on the data directory's volume; unknown values stay null."""

    try:
        usage = shutil.disk_usage(data_directory)
    except OSError:
        free_bytes = total_bytes = None
    else:
        free_bytes, total_bytes = int(usage.free), int(usage.total)
    return StorageStatus(
        data_directory=str(data_directory.resolve()),
        database_path=str(database_path.resolve()),
        free_bytes=free_bytes,
        total_bytes=total_bytes,
        low_space=(free_bytes < LOW_DISK_SPACE_THRESHOLD_BYTES if free_bytes is not None else None),
        low_space_threshold_bytes=LOW_DISK_SPACE_THRESHOLD_BYTES,
    )


def visible_catalog_manifests(
    catalog: AdapterCatalog, database_path: Path
) -> list[AdapterManifest]:
    """Return catalog manifests, omitting test fixtures unless Settings enables them."""

    connection = open_database(database_path)
    try:
        show_fixtures = load_workspace_settings(connection).show_test_fixture_engines
    finally:
        connection.close()
    return [
        manifest
        for manifest in catalog.list_manifests()
        if show_fixtures or not is_test_fixture_manifest(manifest)
    ]


def _response(
    request: Request, connection: DatabaseConnection, catalog: LocalAdapterCatalog
) -> WorkspaceSettingsResponse:
    return WorkspaceSettingsResponse(
        settings=load_workspace_settings(connection),
        defaults=WorkspaceSettings(),
        saved=workspace_settings_saved(connection),
        test_fixture_adapter_ids=list_test_fixture_adapter_ids(catalog),
        storage=storage_status(
            cast(Path, request.app.state.data_directory),
            cast(Path, request.app.state.database_path),
        ),
    )


@router.get("/settings", response_model=WorkspaceSettingsResponse, tags=["settings"])
def get_settings(
    request: Request, connection: DatabaseConnection, catalog: LocalAdapterCatalog
) -> WorkspaceSettingsResponse:
    """Return saved settings, their defaults, and the data directory's disk status."""

    return _response(request, connection, catalog)


@router.put("/settings", response_model=WorkspaceSettingsResponse, tags=["settings"])
def replace_settings(
    body: WorkspaceSettings,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> WorkspaceSettingsResponse:
    """Replace every setting; omitted fields take their default values."""

    save_workspace_settings(connection, body)
    return _response(request, connection, catalog)


@router.patch("/settings", response_model=WorkspaceSettingsResponse, tags=["settings"])
def update_settings(
    body: WorkspaceSettingsPatch,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> WorkspaceSettingsResponse:
    """Change only the supplied settings; null values leave a setting unchanged."""

    current = load_workspace_settings(connection)
    merged = WorkspaceSettings.model_validate(
        current.model_dump(mode="python") | body.model_dump(mode="python", exclude_none=True)
    )
    save_workspace_settings(connection, merged)
    return _response(request, connection, catalog)
