"""Detect this machine and grade every catalog model for it (read-only; nothing is downloaded)."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from localforge.api.dependencies import DatabaseConnection
from localforge.core.models import HardwareSnapshot, ModelArtifact
from localforge.hardware_fit import HardwareFitResponse, build_hardware_fit
from localforge.model_catalog import (
    fetch_ollama_tags,
    installed_from_inventory,
    load_model_catalog,
    local_ollama_endpoints,
    suggestion_machine,
)
from localforge.recommendations import load_stored_context
from localforge.security.local_session import is_loopback_request_host
from localforge.storage.repositories import RecordRepository
from localforge.system.inspection import inspect_local_machine

router = APIRouter(prefix="/api/v1")


@router.get("/hardware-fit", response_model=HardwareFitResponse, tags=["models"])
async def hardware_fit(
    request: Request, connection: DatabaseConnection, refresh: bool = False
) -> HardwareFitResponse:
    """Grade the catalog for this machine. ``refresh`` detects the hardware again right now."""

    if not is_loopback_request_host(request.url.hostname):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "loopback_only",
                "message": "Model routes are available only through a loopback host.",
            },
        )
    context = load_stored_context(connection)
    saved = context.saved_machine.hardware if context.saved_machine else None
    if refresh or saved is None:
        hardware = (await asyncio.to_thread(inspect_local_machine)).hardware or HardwareSnapshot()
        detected_by = "a fresh scan of this machine"
    else:
        hardware = saved
        detected_by = "the saved machine profile"
    profiles = context.saved_profiles
    live = await fetch_ollama_tags(
        local_ollama_endpoints(profiles),
        transport=getattr(request.app.state, "ollama_tags_transport", None),
    )
    artifacts = RecordRepository(connection, ModelArtifact).list(limit=1000)
    machine = suggestion_machine(hardware, context.read_gib_per_second)
    return build_hardware_fit(
        hardware,
        load_model_catalog(),
        detected_by=detected_by,
        machine=machine,
        installed=installed_from_inventory(profiles, artifacts, live),
    )
