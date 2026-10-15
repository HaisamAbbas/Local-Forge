"""Model suggestions for this machine and explicit, user-confirmed pulls through local Ollama."""

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.contracts import ProbeStatus
from localforge.api.dependencies import DatabaseConnection
from localforge.api.profiles import _persist_probe_models, _profile_adapter_configuration
from localforge.core.models import (
    CapabilitySnapshot,
    EngineIntegrationMode,
    EngineProfile,
    EngineProfileStatus,
    ModelArtifact,
)
from localforge.core.urls import endpoint_is_remote
from localforge.model_catalog import (
    ALLOWED_PULL_REGISTRIES,
    OLLAMA_ADAPTER_ID,
    OLLAMA_NAME_PATTERN,
    ModelSuggestionsResponse,
    UseCase,
    build_model_suggestions,
    fetch_ollama_tags,
    installed_from_inventory,
    load_model_catalog,
    local_ollama_endpoints,
    ollama_base_url,
    ollama_registry_host,
    suggestion_machine,
)
from localforge.model_pulls import ModelPullView, OllamaPullManager, PullConflict
from localforge.recommendations import load_stored_context
from localforge.security.local_session import is_loopback_request_host
from localforge.storage.database import open_database
from localforge.storage.repositories import RecordRepository

router = APIRouter(prefix="/api/v1")


def _error(status_code: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message, **extra}
    )


def _require_loopback(request: Request) -> None:
    if not is_loopback_request_host(request.url.hostname):
        raise _error(
            403, "loopback_only", "Model routes are available only through a loopback host."
        )


def _pull_manager(app: FastAPI) -> OllamaPullManager:
    manager = getattr(app.state, "model_pull_manager", None)
    if manager is None:
        manager = OllamaPullManager(transport=getattr(app.state, "ollama_pull_transport", None))
        app.state.model_pull_manager = manager
    return cast(OllamaPullManager, manager)


@router.get(
    "/models/suggestions",
    response_model=ModelSuggestionsResponse,
    tags=["models"],
)
async def model_suggestions(
    request: Request,
    connection: DatabaseConnection,
    use_case: Annotated[UseCase, Query()] = "chat",
) -> ModelSuggestionsResponse:
    """Rank catalog models for this machine; reads local Ollama tag lists only (no downloads)."""

    _require_loopback(request)
    context = load_stored_context(connection)
    profiles = context.saved_profiles
    hardware = context.saved_machine.hardware if context.saved_machine else None
    if hardware is None:
        from localforge.system.inspection import inspect_local_machine

        hardware = (await asyncio.to_thread(inspect_local_machine)).hardware
    machine = suggestion_machine(hardware, context.read_gib_per_second)
    live = await fetch_ollama_tags(
        local_ollama_endpoints(profiles),
        transport=getattr(request.app.state, "ollama_tags_transport", None),
    )
    artifacts = RecordRepository(connection, ModelArtifact).list(limit=1000)
    installed_engines = {profile.adapter_id for profile in profiles}
    if live:
        installed_engines.add(OLLAMA_ADAPTER_ID)
    return build_model_suggestions(
        use_case,
        machine,
        profiles=profiles,
        installed=installed_from_inventory(profiles, artifacts, live),
        installed_engines=installed_engines,
        ollama_reachable=bool(live),
    )


class ModelPullRequest(BaseModel):
    engine_profile_id: UUID
    ollama_tag: str = Field(min_length=1, max_length=200)
    confirm: bool = False
    # Set when the user typed a tag that is not in LocalForge's catalog and confirmed it.
    custom_tag: bool = False


async def probe_after_pull(app: FastAPI, profile_id: UUID) -> tuple[bool, str]:
    """Re-probe the Ollama profile so the new model shows up in its saved inventory."""

    locks = cast(dict[UUID, asyncio.Lock], app.state.engine_profile_lifecycle_locks)
    async with locks.setdefault(profile_id, asyncio.Lock()):
        connection = open_database(cast(Path, app.state.database_path))
        try:
            return await _probe(
                connection, cast(AdapterCatalog, app.state.adapter_catalog), profile_id
            )
        finally:
            connection.close()


async def _probe(
    connection: sqlite3.Connection, catalog: AdapterCatalog, profile_id: UUID
) -> tuple[bool, str]:
    repository = RecordRepository(connection, EngineProfile)
    profile = repository.get(profile_id)
    if profile is None:
        return False, "The engine profile was removed."
    result = await catalog.probe(profile.adapter_id, _profile_adapter_configuration(profile))
    snapshot = CapabilitySnapshot(
        adapter_id=result.adapter_id,
        adapter_version=result.adapter_version,
        probed_at=result.probed_at,
        capabilities=result.capabilities.capabilities,
    )
    RecordRepository(connection, CapabilitySnapshot).create(snapshot)
    _persist_probe_models(connection, profile, result)
    failed = result.status is ProbeStatus.FAILED
    repository.update(
        EngineProfile.model_validate(
            profile.model_dump(mode="python")
            | {
                "adapter_version": result.adapter_version,
                "engine_version": result.engine_version,
                "last_probed_at": result.probed_at,
                "capability_snapshot_id": snapshot.id,
                "status": EngineProfileStatus.NEEDS_ATTENTION if failed else profile.status,
            }
        )
    )
    if failed:
        return False, "The engine probe failed after the download; open Engines for details."
    count = len(result.models)
    return True, f"Engine refreshed · {count} {'model' if count == 1 else 'models'} reported."


@router.post(
    "/models/pull",
    response_model=ModelPullView,
    status_code=202,
    tags=["models"],
)
async def pull_model(
    body: ModelPullRequest,
    request: Request,
    connection: DatabaseConnection,
) -> ModelPullView:
    """Ask a local, enabled Ollama profile to download a model. Requires ``confirm: true``."""

    _require_loopback(request)
    if not body.confirm:
        raise _error(
            422,
            "pull_confirmation_required",
            "Confirm the download (confirm: true); Ollama will fetch the model from its registry.",
        )
    tag = body.ollama_tag.strip()
    if not OLLAMA_NAME_PATTERN.match(tag):
        raise _error(422, "invalid_model_tag", "That is not a valid Ollama model name.")
    registry = ollama_registry_host(tag)
    if registry is not None and registry not in ALLOWED_PULL_REGISTRIES:
        raise _error(
            422,
            "registry_not_allowed",
            f"LocalForge only pulls from Ollama's registry or Hugging Face, not {registry}. "
            "Run `ollama pull` yourself to use another registry.",
        )
    catalog_model = load_model_catalog().by_ollama_tag(tag)
    if catalog_model is None and not body.custom_tag:
        raise _error(
            422,
            "unknown_model_tag",
            "This tag is not in LocalForge's model catalog. Confirm it as a custom tag to pull it.",
        )
    profile = RecordRepository(connection, EngineProfile).get(body.engine_profile_id)
    if profile is None:
        raise _error(404, "engine_profile_not_found", "The engine profile was not found.")
    if profile.adapter_id != OLLAMA_ADAPTER_ID or profile.integration_mode is not (
        EngineIntegrationMode.ATTACHED_ENDPOINT
    ):
        raise _error(409, "not_an_ollama_profile", "Only Ollama engine profiles can pull models.")
    if profile.endpoint_url is None or endpoint_is_remote(profile.endpoint_url):
        raise _error(
            409,
            "remote_pull_refused",
            "LocalForge pulls models only into an Ollama running on this machine.",
        )
    if profile.status is not EngineProfileStatus.ENABLED:
        raise _error(409, "engine_profile_not_enabled", "Enable this Ollama profile first.")

    app = cast(FastAPI, request.app)

    async def probe(profile_id: UUID) -> tuple[bool, str]:
        override = getattr(app.state, "model_pull_probe", None)
        if override is not None:
            return cast(tuple[bool, str], await override(profile_id))
        return await probe_after_pull(app, profile_id)

    try:
        return _pull_manager(app).start(
            engine_profile_id=profile.id,
            engine_label=profile.label,
            base_url=ollama_base_url(profile.endpoint_url),
            ollama_tag=tag,
            catalog_model_id=catalog_model.id if catalog_model else None,
            probe=probe,
        )
    except PullConflict as conflict:
        extra = {"pull_id": str(conflict.pull_id)} if conflict.pull_id else {}
        raise _error(409, conflict.code, conflict.message, **extra) from conflict


@router.get("/models/pulls", response_model=list[ModelPullView], tags=["models"])
def list_model_pulls(request: Request) -> list[ModelPullView]:
    """Pulls started since the API started (kept in memory only)."""

    _require_loopback(request)
    return _pull_manager(cast(FastAPI, request.app)).list()


@router.get("/models/pulls/{pull_id}", response_model=ModelPullView, tags=["models"])
def get_model_pull(pull_id: UUID, request: Request) -> ModelPullView:
    _require_loopback(request)
    view = _pull_manager(cast(FastAPI, request.app)).get(pull_id)
    if view is None:
        raise _error(404, "pull_not_found", "That pull was not found.")
    return view


@router.post("/models/pulls/{pull_id}/cancel", response_model=ModelPullView, tags=["models"])
def cancel_model_pull(pull_id: UUID, request: Request) -> ModelPullView:
    _require_loopback(request)
    view = _pull_manager(cast(FastAPI, request.app)).cancel(pull_id)
    if view is None:
        raise _error(404, "pull_not_found", "That pull was not found.")
    return view
