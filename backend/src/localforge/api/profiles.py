"""Local system and engine profile routes backed by the caller's SQLite database."""

import json
import sqlite3
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from localforge.adapters.catalog import AdapterNotFoundError
from localforge.adapters.contracts import (
    AdapterConfiguration,
    AdapterProcessHandle,
    DiagnosticSeverity,
    ProbeResult,
    ProbeStatus,
)
from localforge.api.dependencies import (
    DatabaseConnection,
    EngineProfileMutationLock,
    LocalAdapterCatalog,
)
from localforge.api.schemas import (
    EngineProbeResponse,
    EngineProfileCreate,
    EngineProfilePatch,
    EngineProfileView,
    MachineProfileCreate,
    MachineProfilePatch,
    ModelArtifactListItem,
    ProbeRequest,
    RemoteEgressDisclosure,
)
from localforge.comparisons import _find_quantization
from localforge.core.models import (
    CapabilitySnapshot,
    CapabilitySupport,
    EngineIntegrationMode,
    EngineProfile,
    EngineProfileStatus,
    FieldProvenance,
    JsonValue,
    MachineProfile,
    MachineProfileStatus,
    ModelArtifact,
    ModelArtifactStatus,
    ProvenanceSource,
    utc_now,
)
from localforge.core.urls import endpoint_is_remote
from localforge.discovery import (
    DiscoveryResponse,
    SavedEndpoint,
    discover_engines,
    parse_ports,
)
from localforge.security.local_session import is_loopback_request_host
from localforge.storage.repositories import RecordAlreadyExists, RecordRepository
from localforge.system.inspection import inspect_local_machine

router = APIRouter(prefix="/api/v1")


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _get_engine_profile(connection: sqlite3.Connection, profile_id: UUID) -> EngineProfile:
    profile = RecordRepository(connection, EngineProfile).get(profile_id)
    if profile is None:
        raise _http_error(404, "engine_profile_not_found", "The engine profile was not found.")
    return profile


def _get_machine_profile(connection: sqlite3.Connection, profile_id: UUID) -> MachineProfile:
    profile = RecordRepository(connection, MachineProfile).get(profile_id)
    if profile is None:
        raise _http_error(404, "machine_profile_not_found", "The machine profile was not found.")
    return profile


def _engine_profile_view(
    connection: sqlite3.Connection,
    profile: EngineProfile,
    *,
    managed_process_running: bool = False,
) -> EngineProfileView:
    capabilities = None
    if profile.capability_snapshot_id is not None:
        snapshot = RecordRepository(connection, CapabilitySnapshot).get(
            profile.capability_snapshot_id
        )
        if snapshot is not None:
            capabilities = snapshot.capabilities
    return EngineProfileView(
        id=profile.id,
        label=profile.label,
        adapter_id=profile.adapter_id,
        engine_display_name=profile.engine_display_name,
        adapter_version=profile.adapter_version,
        integration_mode=profile.integration_mode,
        status=profile.status,
        managed_process_running=managed_process_running,
        endpoint_url=profile.endpoint_url,
        is_remote=endpoint_is_remote(profile.endpoint_url),
        remote_access_enabled=profile.remote_access_enabled,
        engine_version=profile.engine_version,
        last_probed_at=profile.last_probed_at,
        capability_snapshot_id=profile.capability_snapshot_id,
        capabilities=capabilities,
        machine_profile_id=profile.machine_profile_id,
        selected_model_ref=profile.selected_model_ref,
        user_notes=profile.user_notes,
        engine_extensions=profile.engine_extensions,
        configured_fields=sorted(profile.config),
        credential_configured=bool(profile.secret_refs),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        schema_version=profile.schema_version,
    )


def _profile_adapter_configuration(profile: EngineProfile) -> AdapterConfiguration:
    values = dict(profile.config)
    if profile.endpoint_url is not None:
        values["endpoint_url"] = profile.endpoint_url
    if profile.health_route is not None:
        values["health_route"] = profile.health_route
    if profile.models_route is not None:
        values["models_route"] = profile.models_route
    if profile.engine_extensions:
        values["engine_extensions"] = cast(JsonValue, profile.engine_extensions)
    return AdapterConfiguration(
        integration_mode=profile.integration_mode,
        values=values,
        secret_refs=profile.secret_refs,
    )


def _validate_engine_profile(data: dict[str, object]) -> EngineProfile:
    try:
        return EngineProfile.model_validate(data)
    except ValidationError as error:
        raise _http_error(
            422,
            "invalid_engine_profile",
            "The profile is invalid. Check its endpoint URL, settings, and credential references.",
        ) from error


def _persist_probe_models(
    connection: sqlite3.Connection,
    profile: EngineProfile,
    result: ProbeResult,
) -> None:
    repository = RecordRepository(connection, ModelArtifact)
    existing_records = [
        record
        for record in repository.list(limit=1000)
        if record.engine_profile_id == profile.id and record.model_ref is not None
    ]
    existing_by_ref = {record.model_ref: record for record in existing_records}
    returned_refs: set[str] = set()
    for model in result.models:
        returned_refs.add(model.model_ref)
        tokenizer_identity = model.metadata.get("tokenizer_identity")
        if not isinstance(tokenizer_identity, str) or not tokenizer_identity.strip():
            tokenizer_identity = None
        raw_context_limit = model.metadata.get("max_context_tokens")
        max_context_tokens = (
            raw_context_limit
            if isinstance(raw_context_limit, int)
            and not isinstance(raw_context_limit, bool)
            and raw_context_limit > 0
            else None
        )
        raw_format = model.metadata.get("format")
        model_format = raw_format if isinstance(raw_format, str) and raw_format else None
        raw_size = model.metadata.get("size_bytes")
        size_bytes = (
            raw_size
            if isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size >= 0
            else None
        )
        existing = existing_by_ref.get(model.model_ref)
        if existing is None:
            model_extensions: dict[str, dict[str, JsonValue]] = {}
            if profile.adapter_id == "localforge.ollama":
                model_extensions["ollama"] = {
                    key: value
                    for key, value in model.metadata.items()
                    if key
                    in {
                        "runner",
                        "runner_state",
                        "format",
                        "family",
                        "families",
                        "parameter_size",
                        "quantization_level",
                        "digest",
                    }
                    and isinstance(value, (str, int, float, bool, list, dict))
                }
            repository.create(
                ModelArtifact(
                    display_name=model.display_name,
                    engine_profile_id=profile.id,
                    model_ref=model.model_ref,
                    revision=model.revision,
                    format=model_format,
                    size_bytes=size_bytes,
                    tokenizer_identity=tokenizer_identity,
                    max_context_tokens=max_context_tokens,
                    status=ModelArtifactStatus.AVAILABLE,
                    engine_extensions=model_extensions,
                )
            )
        else:
            updated_metadata: dict[str, object] = {
                "display_name": model.display_name,
                "revision": model.revision,
                "status": ModelArtifactStatus.AVAILABLE,
            }
            if model_format is not None:
                updated_metadata["format"] = model_format
            if size_bytes is not None:
                updated_metadata["size_bytes"] = size_bytes
            if tokenizer_identity is not None:
                updated_metadata["tokenizer_identity"] = tokenizer_identity
            if max_context_tokens is not None:
                updated_metadata["max_context_tokens"] = max_context_tokens
            if profile.adapter_id == "localforge.ollama":
                updated_metadata["engine_extensions"] = {
                    **existing.engine_extensions,
                    "ollama": {
                        key: value
                        for key, value in model.metadata.items()
                        if key
                        in {
                            "runner",
                            "runner_state",
                            "format",
                            "family",
                            "families",
                            "parameter_size",
                            "quantization_level",
                            "digest",
                        }
                        and isinstance(value, (str, int, float, bool, list, dict))
                    },
                }
            changed = ModelArtifact.model_validate(
                existing.model_dump(mode="python") | updated_metadata
            )
            repository.update(changed)

    inventory = result.capabilities.capabilities.get("model_inventory")
    if inventory is None or inventory.state is not CapabilitySupport.SUPPORTED:
        return
    for existing in existing_records:
        if existing.model_ref is None or existing.model_ref in returned_refs:
            continue
        missing = ModelArtifact.model_validate(
            existing.model_dump(mode="python") | {"status": ModelArtifactStatus.MISSING}
        )
        repository.update(missing)


@router.get("/system", response_model=MachineProfile, tags=["system"])
def get_local_system_profile() -> MachineProfile:
    """Return best-effort host details with field-level detection provenance."""

    return inspect_local_machine()


@router.put("/machine-profiles/current", response_model=MachineProfile, tags=["machines"])
def save_current_machine_profile(
    connection: DatabaseConnection,
) -> MachineProfile:
    """Persist the detected local-machine snapshot under one stable profile ID."""

    repository = RecordRepository(connection, MachineProfile)
    current = inspect_local_machine()
    existing = next(
        (
            profile
            for profile in repository.list()
            if profile.label == "This machine"
            and profile.status is not MachineProfileStatus.MANUAL_UNVERIFIED
        ),
        None,
    )
    if existing is None:
        return repository.create(current)
    updated = MachineProfile.model_validate(
        current.model_dump(mode="python") | {"id": existing.id, "created_at": existing.created_at}
    )
    return repository.update(updated)


@router.get("/machine-profiles", response_model=list[MachineProfile], tags=["machines"])
def list_machine_profiles(
    connection: DatabaseConnection,
) -> list[MachineProfile]:
    return RecordRepository(connection, MachineProfile).list()


@router.post("/machine-profiles", response_model=MachineProfile, tags=["machines"])
def create_manual_machine_profile(
    body: MachineProfileCreate,
    connection: DatabaseConnection,
) -> MachineProfile:
    observed_at = utc_now()
    supplied_fields = (
        body.hardware.model_dump(exclude_none=True) if body.hardware is not None else {}
    )
    provenance = {
        field_name: FieldProvenance(
            source=ProvenanceSource.USER_SUPPLIED,
            confidence=1.0,
            observed_at=observed_at,
        )
        for field_name in supplied_fields
    }
    profile = MachineProfile(
        label=body.label,
        status=MachineProfileStatus.MANUAL_UNVERIFIED,
        hardware=body.hardware,
        provenance=provenance,
    )
    RecordRepository(connection, MachineProfile).create(profile)
    return profile


@router.get("/machine-profiles/{profile_id}", response_model=MachineProfile, tags=["machines"])
def get_machine_profile(
    profile_id: UUID,
    connection: DatabaseConnection,
) -> MachineProfile:
    return _get_machine_profile(connection, profile_id)


@router.patch("/machine-profiles/{profile_id}", response_model=MachineProfile, tags=["machines"])
def update_machine_profile(
    profile_id: UUID,
    body: MachineProfilePatch,
    connection: DatabaseConnection,
) -> MachineProfile:
    current = _get_machine_profile(connection, profile_id)
    patch = body.model_dump(mode="python", exclude_unset=True)
    merged = current.model_dump(mode="python") | patch
    if "hardware" in patch:
        hardware = body.hardware
        supplied_fields = hardware.model_dump(exclude_none=True) if hardware is not None else {}
        observed_at = utc_now()
        merged["provenance"] = {
            field_name: FieldProvenance(
                source=ProvenanceSource.USER_SUPPLIED,
                confidence=1.0,
                observed_at=observed_at,
            )
            for field_name in supplied_fields
        }
        merged["status"] = MachineProfileStatus.MANUAL_UNVERIFIED
    try:
        updated = MachineProfile.model_validate(merged)
    except ValidationError as error:
        raise _http_error(
            422, "invalid_machine_profile", "The machine profile is invalid."
        ) from error
    return RecordRepository(connection, MachineProfile).update(updated)


@router.delete("/machine-profiles/{profile_id}", status_code=204, tags=["machines"])
def delete_machine_profile(
    profile_id: UUID,
    connection: DatabaseConnection,
) -> Response:
    _get_machine_profile(connection, profile_id)
    engine_profiles = RecordRepository(connection, EngineProfile).list()
    if any(profile.machine_profile_id == profile_id for profile in engine_profiles):
        raise _http_error(
            409,
            "machine_profile_in_use",
            "Remove or update the engine profiles using this machine profile first.",
        )
    RecordRepository(connection, MachineProfile).delete(profile_id)
    return Response(status_code=204)


@router.get("/engines", response_model=list[EngineProfileView], tags=["engines"])
def list_engine_profiles(
    request: Request,
    connection: DatabaseConnection,
) -> list[EngineProfileView]:
    profiles = RecordRepository(connection, EngineProfile).list()
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    return [
        _engine_profile_view(
            connection,
            profile,
            managed_process_running=profile.id in process_handles,
        )
        for profile in profiles
    ]


@router.get("/engines/discover", response_model=DiscoveryResponse, tags=["engines"])
async def discover_running_engines(
    request: Request,
    catalog: LocalAdapterCatalog,
    connection: DatabaseConnection,
    ports: str | None = None,
) -> DiscoveryResponse:
    """Scan well-known loopback ports for running engines; read-only and 127.0.0.1 only."""

    if not is_loopback_request_host(request.url.hostname):
        raise _http_error(
            403, "loopback_only", "Engine discovery is available only through a loopback host."
        )
    try:
        port_list = parse_ports(ports)
    except ValueError as error:
        raise _http_error(422, "invalid_ports", str(error)) from error

    self_ports: set[int] = set()
    server = request.scope.get("server")
    if isinstance(server, (tuple, list)) and len(server) == 2 and isinstance(server[1], int):
        self_ports.add(server[1])
    if request.url.port is not None:
        self_ports.add(request.url.port)
    implemented = {
        manifest.adapter_id: manifest.display_name
        for manifest in catalog.list_manifests()
        if manifest.implementation_status == "implemented"
    }
    saved = [
        SavedEndpoint(
            profile_id=profile.id,
            adapter_id=profile.adapter_id,
            endpoint_url=profile.endpoint_url,
        )
        for profile in RecordRepository(connection, EngineProfile).list(limit=1000)
    ]
    return await discover_engines(
        implemented_adapters=implemented,
        saved_endpoints=saved,
        ports=port_list,
        self_ports=self_ports,
        transport=getattr(request.app.state, "engine_discovery_transport", None),
    )


@router.post("/engines", response_model=EngineProfileView, status_code=201, tags=["engines"])
def create_engine_profile(
    body: EngineProfileCreate,
    catalog: LocalAdapterCatalog,
    connection: DatabaseConnection,
) -> EngineProfileView:
    try:
        manifest = catalog.manifest_for(body.adapter_id)
    except AdapterNotFoundError as error:
        raise _http_error(
            404, "adapter_not_found", "The selected adapter is not installed."
        ) from error
    if body.integration_mode not in manifest.integration_modes:
        raise _http_error(
            422,
            "integration_mode_unsupported",
            "The selected adapter does not support this integration mode.",
        )
    if (
        body.machine_profile_id is not None
        and RecordRepository(connection, MachineProfile).get(body.machine_profile_id) is None
    ):
        raise _http_error(
            422, "machine_profile_not_found", "The target machine profile was not found."
        )

    values: dict[str, object] = body.model_dump(mode="python")
    values.update(
        {
            "adapter_version": manifest.adapter_version,
            "status": EngineProfileStatus.DISABLED,
            "remote_access_enabled": False,
        }
    )
    profile = _validate_engine_profile(values)
    try:
        RecordRepository(connection, EngineProfile).create(profile)
    except RecordAlreadyExists as error:
        raise _http_error(
            409, "engine_profile_conflict", "The engine profile ID already exists."
        ) from error
    return _engine_profile_view(connection, profile)


@router.get("/engines/{profile_id}", response_model=EngineProfileView, tags=["engines"])
def get_engine_profile(
    profile_id: UUID,
    request: Request,
    connection: DatabaseConnection,
) -> EngineProfileView:
    profile = _get_engine_profile(connection, profile_id)
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    return _engine_profile_view(
        connection,
        profile,
        managed_process_running=profile.id in process_handles,
    )


@router.patch("/engines/{profile_id}", response_model=EngineProfileView, tags=["engines"])
def update_engine_profile(
    profile_id: UUID,
    body: EngineProfilePatch,
    request: Request,
    catalog: LocalAdapterCatalog,
    connection: DatabaseConnection,
    _profile_lock: EngineProfileMutationLock,
) -> EngineProfileView:
    current = _get_engine_profile(connection, profile_id)
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    if profile_id in process_handles:
        raise _http_error(
            409, "engine_process_running", "Stop the managed process before editing this profile."
        )

    patch = body.model_dump(mode="python", exclude_unset=True)
    target_machine_id = patch.get("machine_profile_id", current.machine_profile_id)
    if (
        target_machine_id is not None
        and target_machine_id != current.machine_profile_id
        and RecordRepository(connection, MachineProfile).get(target_machine_id) is None
    ):
        raise _http_error(
            422, "machine_profile_not_found", "The target machine profile was not found."
        )

    manifest = catalog.manifest_for(current.adapter_id)
    new_mode = patch.get("integration_mode", current.integration_mode)
    if new_mode not in manifest.integration_modes:
        raise _http_error(
            422,
            "integration_mode_unsupported",
            "The selected adapter does not support this integration mode.",
        )
    merged = current.model_dump(mode="python") | patch
    invalidates_probe = any(
        key in patch
        for key in {
            "endpoint_url",
            "health_route",
            "models_route",
            "integration_mode",
            "config",
            "secret_refs",
            "engine_extensions",
        }
    )
    if invalidates_probe:
        if patch.get("endpoint_url", current.endpoint_url) != current.endpoint_url:
            merged["remote_access_enabled"] = False
        merged.update(
            {
                "adapter_version": manifest.adapter_version,
                "engine_version": None,
                "last_probed_at": None,
                "capability_snapshot_id": None,
                "status": EngineProfileStatus.DISABLED,
            }
        )
    updated = _validate_engine_profile(merged)
    saved = RecordRepository(connection, EngineProfile).update(updated)
    return _engine_profile_view(connection, saved)


def _quantization_label(artifact: ModelArtifact) -> str | None:
    value = _find_quantization(cast(JsonValue, artifact.engine_extensions))
    if isinstance(value, str):
        return value or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


@router.get("/models", response_model=list[ModelArtifactListItem], tags=["models"])
def list_all_models(connection: DatabaseConnection) -> list[ModelArtifactListItem]:
    """List every saved model artifact with its owning engine, sorted by engine then model."""

    rows = connection.execute(
        "SELECT payload_json FROM domain_records WHERE entity_type = ?",
        (ModelArtifact.__name__,),
    ).fetchall()
    artifacts = [ModelArtifact.model_validate(json.loads(row["payload_json"])) for row in rows]
    engines = {
        profile.id: profile
        for profile in RecordRepository(connection, EngineProfile).list(limit=1000)
    }
    items: list[ModelArtifactListItem] = []
    for artifact in artifacts:
        engine = (
            engines.get(artifact.engine_profile_id)
            if artifact.engine_profile_id is not None
            else None
        )
        items.append(
            ModelArtifactListItem(
                id=artifact.id,
                model_ref=artifact.model_ref,
                display_name=artifact.display_name,
                status=artifact.status,
                engine_profile_id=artifact.engine_profile_id,
                engine_label=engine.label if engine is not None else None,
                adapter_id=engine.adapter_id if engine is not None else None,
                format=artifact.format,
                quantization=_quantization_label(artifact),
                size_bytes=artifact.size_bytes,
                max_context_tokens=artifact.max_context_tokens,
                revision=artifact.revision,
                tokenizer_identity=artifact.tokenizer_identity,
                updated_at=artifact.updated_at,
            )
        )
    items.sort(
        key=lambda item: (
            item.engine_label is None,
            (item.engine_label or "").casefold(),
            item.model_ref is None,
            (item.model_ref or "").casefold(),
            str(item.id),
        )
    )
    return items


@router.get("/engines/{profile_id}/models", response_model=list[ModelArtifact], tags=["engines"])
def list_engine_models(
    profile_id: UUID,
    connection: DatabaseConnection,
) -> list[ModelArtifact]:
    _get_engine_profile(connection, profile_id)
    artifacts = RecordRepository(connection, ModelArtifact).list(limit=1000)
    return [artifact for artifact in artifacts if artifact.engine_profile_id == profile_id]


@router.post("/engines/{profile_id}/probe", response_model=EngineProbeResponse, tags=["engines"])
async def probe_engine_profile(
    profile_id: UUID,
    body: ProbeRequest,
    request: Request,
    catalog: LocalAdapterCatalog,
    connection: DatabaseConnection,
    _profile_lock: EngineProfileMutationLock,
) -> EngineProbeResponse:
    profile = _get_engine_profile(connection, profile_id)
    manifest = catalog.manifest_for(profile.adapter_id)
    if profile.integration_mode not in manifest.integration_modes:
        raise _http_error(
            422,
            "integration_mode_unsupported",
            "The selected adapter does not support this integration mode.",
        )

    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    if (
        profile.integration_mode is EngineIntegrationMode.MANAGED_PROCESS
        and profile_id not in process_handles
    ):
        raise _http_error(
            409,
            "managed_process_not_running",
            "Start the managed server before probing its endpoint.",
        )

    is_remote = endpoint_is_remote(profile.endpoint_url)
    confirm_remote = body.confirm_remote_egress
    if is_remote and not profile.remote_access_enabled and not confirm_remote:
        hostname = urlsplit(profile.endpoint_url or "").hostname or "the configured host"
        disclosure = RemoteEgressDisclosure(
            host=hostname,
            message=(
                f"This profile targets remote host {hostname}. Probing may send one or more "
                "short chat-completion checks, including a separate usage-count check when the "
                "adapter is configured to request usage. Benchmark prompts and requests will "
                "leave this machine. "
                "Confirm remote use before contacting this target."
            ),
        )
        raise HTTPException(status_code=409, detail=disclosure.model_dump(mode="json"))
    if is_remote and confirm_remote and not profile.remote_access_enabled:
        profile = RecordRepository(connection, EngineProfile).update(
            EngineProfile.model_validate(
                profile.model_dump(mode="python") | {"remote_access_enabled": True}
            )
        )

    # Engine identity is profile metadata; model names come from adapter inventory.
    config = _profile_adapter_configuration(profile)
    validation = await catalog.validate_config(profile.adapter_id, config)
    if not validation.valid:
        diagnostic = next(
            (item for item in validation.diagnostics if item.severity is DiagnosticSeverity.ERROR),
            None,
        )
        raise _http_error(
            422,
            diagnostic.code if diagnostic is not None else "adapter_config_invalid",
            diagnostic.message
            if diagnostic is not None
            else "The selected adapter rejected this profile configuration.",
        )
    try:
        result = await catalog.probe(profile.adapter_id, config)
    except AdapterNotFoundError as error:
        raise _http_error(
            404, "adapter_not_found", "The selected adapter is not installed."
        ) from error
    except Exception as error:
        raise _http_error(
            502,
            "adapter_probe_failed",
            "The adapter probe failed. Review the endpoint and connection settings.",
        ) from error

    snapshot = CapabilitySnapshot(
        adapter_id=result.adapter_id,
        adapter_version=result.adapter_version,
        probed_at=result.probed_at,
        capabilities=result.capabilities.capabilities,
    )
    RecordRepository(connection, CapabilitySnapshot).create(snapshot)
    _persist_probe_models(connection, profile, result)
    if result.status is ProbeStatus.FAILED:
        new_status = EngineProfileStatus.NEEDS_ATTENTION
    elif profile.status is EngineProfileStatus.NEEDS_ATTENTION:
        # A clean retry clears the error state but remains disabled until the user enables it.
        new_status = EngineProfileStatus.DISABLED
    else:
        new_status = profile.status
    profile = EngineProfile.model_validate(
        profile.model_dump(mode="python")
        | {
            "adapter_version": result.adapter_version,
            "engine_version": result.engine_version,
            "last_probed_at": result.probed_at,
            "capability_snapshot_id": snapshot.id,
            "status": new_status,
        }
    )
    saved = RecordRepository(connection, EngineProfile).update(profile)
    return EngineProbeResponse(
        profile=_engine_profile_view(
            connection,
            saved,
            managed_process_running=profile.id in process_handles,
        ),
        result=result,
    )


@router.post("/engines/{profile_id}/enable", response_model=EngineProfileView, tags=["engines"])
def enable_engine_profile(
    profile_id: UUID,
    request: Request,
    connection: DatabaseConnection,
    _profile_lock: EngineProfileMutationLock,
) -> EngineProfileView:
    profile = _get_engine_profile(connection, profile_id)
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    if (
        profile.integration_mode is EngineIntegrationMode.MANAGED_PROCESS
        and profile_id not in process_handles
    ):
        raise _http_error(
            409,
            "managed_process_not_running",
            "Start the managed server before enabling its profile.",
        )
    if profile.last_probed_at is None:
        raise _http_error(409, "engine_probe_required", "Probe this profile before enabling it.")
    if profile.status is EngineProfileStatus.NEEDS_ATTENTION:
        raise _http_error(409, "engine_needs_attention", "Resolve the latest probe failure first.")
    enabled = EngineProfile.model_validate(
        profile.model_dump(mode="python") | {"status": EngineProfileStatus.ENABLED}
    )
    saved = RecordRepository(connection, EngineProfile).update(enabled)
    return _engine_profile_view(
        connection,
        saved,
        managed_process_running=profile.id in process_handles,
    )


@router.post("/engines/{profile_id}/disable", response_model=EngineProfileView, tags=["engines"])
def disable_engine_profile(
    profile_id: UUID,
    request: Request,
    connection: DatabaseConnection,
    _profile_lock: EngineProfileMutationLock,
) -> EngineProfileView:
    profile = _get_engine_profile(connection, profile_id)
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    if profile_id in process_handles:
        raise _http_error(
            409, "engine_process_running", "Stop the managed process before disabling it."
        )
    disabled = EngineProfile.model_validate(
        profile.model_dump(mode="python") | {"status": EngineProfileStatus.DISABLED}
    )
    saved = RecordRepository(connection, EngineProfile).update(disabled)
    return _engine_profile_view(connection, saved, managed_process_running=False)


@router.post("/engines/{profile_id}/start", response_model=EngineProfileView, tags=["engines"])
async def start_engine_profile(
    profile_id: UUID,
    request: Request,
    catalog: LocalAdapterCatalog,
    connection: DatabaseConnection,
    _profile_lock: EngineProfileMutationLock,
) -> EngineProfileView:
    profile = _get_engine_profile(connection, profile_id)
    if profile.integration_mode is not EngineIntegrationMode.MANAGED_PROCESS:
        raise _http_error(
            409,
            "managed_start_unsupported",
            "Only a managed-process profile can be started by LOCALFORGE.",
        )
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    if profile_id in process_handles:
        raise _http_error(
            409, "engine_already_started", "LOCALFORGE already owns a process for this profile."
        )
    config = _profile_adapter_configuration(profile)
    try:
        handle = await catalog.start(profile.adapter_id, config)
    except TimeoutError as error:
        raise _http_error(
            409,
            "managed_start_timeout",
            "The server did not become healthy before the startup timeout. Check the selected "
            "binary, model, and server settings.",
        ) from error
    except ValueError as error:
        # Adapter validation messages are fixed, user-facing text naming the failing setting.
        reason = str(error).strip()
        raise _http_error(
            409,
            "managed_start_invalid",
            f"The adapter rejected the saved launch settings: {reason}"
            if reason
            else "The adapter rejected the saved launch settings. Check the selected binary, "
            "model, working directory, endpoint, and tuning values.",
        ) from error
    except OSError as error:
        message = (
            "The selected loopback port is already in use. Choose another port or stop the "
            "service using it."
            if "already in use" in str(error).casefold()
            else "LOCALFORGE could not launch the selected binary. Check its permissions and "
            "platform compatibility."
        )
        raise _http_error(409, "managed_start_unavailable", message) from error
    except Exception as error:
        raise _http_error(
            409,
            "managed_start_unavailable",
            "The selected adapter cannot start this profile.",
        ) from error
    handles = cast(dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles)
    handles[profile_id] = handle
    started = EngineProfile.model_validate(
        profile.model_dump(mode="python") | {"status": EngineProfileStatus.ENABLED}
    )
    saved = RecordRepository(connection, EngineProfile).update(started)
    return _engine_profile_view(connection, saved, managed_process_running=True)


@router.post("/engines/{profile_id}/stop", response_model=EngineProfileView, tags=["engines"])
async def stop_engine_profile(
    profile_id: UUID,
    request: Request,
    catalog: LocalAdapterCatalog,
    connection: DatabaseConnection,
    _profile_lock: EngineProfileMutationLock,
) -> EngineProfileView:
    profile = _get_engine_profile(connection, profile_id)
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    handle = process_handles.get(profile_id)
    if handle is None:
        raise _http_error(
            409, "owned_process_not_found", "LOCALFORGE has no owned process for this profile."
        )
    try:
        await catalog.stop(profile.adapter_id, handle)
    except Exception as error:
        raise _http_error(
            409,
            "owned_process_stop_failed",
            "The adapter refused to stop a process that it does not own.",
        ) from error
    del process_handles[profile_id]
    stopped = EngineProfile.model_validate(
        profile.model_dump(mode="python") | {"status": EngineProfileStatus.DISABLED}
    )
    saved = RecordRepository(connection, EngineProfile).update(stopped)
    return _engine_profile_view(connection, saved, managed_process_running=False)


@router.delete("/engines/{profile_id}", status_code=204, tags=["engines"])
def delete_engine_profile(
    profile_id: UUID,
    request: Request,
    connection: DatabaseConnection,
    _profile_lock: EngineProfileMutationLock,
) -> Response:
    _get_engine_profile(connection, profile_id)
    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    if profile_id in process_handles:
        raise _http_error(
            409, "engine_process_running", "Stop the managed process before removing this profile."
        )
    RecordRepository(connection, EngineProfile).delete(profile_id)
    return Response(status_code=204)
