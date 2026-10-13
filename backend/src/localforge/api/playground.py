"""Ephemeral, single-prompt streaming for an enabled local engine profile."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from localforge.adapters.catalog import AdapterNotFoundError
from localforge.adapters.contracts import (
    AdapterCategory,
    AdapterConfiguration,
    AdapterProcessHandle,
    GenerationRequest,
    StreamEventType,
)
from localforge.api.dependencies import DatabaseConnection, LocalAdapterCatalog
from localforge.api.profiles import _profile_adapter_configuration
from localforge.api.schemas import PlaygroundRequest, RemoteEgressDisclosure
from localforge.core.models import (
    CapabilitySnapshot,
    CapabilitySupport,
    EngineProfile,
    EngineProfileStatus,
    ModelArtifact,
    ModelArtifactStatus,
)
from localforge.core.urls import endpoint_is_remote
from localforge.storage.repositories import RecordRepository

router = APIRouter(prefix="/api/v1")


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


def _json_line(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return encoded + "\n"


@router.post("/playground/stream", tags=["playground"])
async def stream_playground_prompt(
    body: PlaygroundRequest,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> StreamingResponse:
    """Stream one prompt; prompt text and generated content are never persisted here."""

    profile = RecordRepository(connection, EngineProfile).get(body.profile_id)
    if profile is None:
        raise _http_error(404, "engine_profile_not_found", "The engine profile was not found.")
    if profile.status is not EngineProfileStatus.ENABLED:
        raise _http_error(
            409, "engine_not_enabled", "Enable this engine profile before prompting it."
        )
    if profile.last_probed_at is None or profile.capability_snapshot_id is None:
        raise _http_error(409, "engine_probe_required", "Probe this engine before prompting it.")

    artifacts = RecordRepository(connection, ModelArtifact).list(limit=1000)
    selected_model = next(
        (
            artifact
            for artifact in artifacts
            if artifact.engine_profile_id == profile.id
            and artifact.model_ref == body.model_ref
            and artifact.status is ModelArtifactStatus.AVAILABLE
        ),
        None,
    )
    if selected_model is None:
        raise _http_error(
            409,
            "model_not_available",
            "Choose a model reported as available by the latest engine probe.",
        )

    is_remote = endpoint_is_remote(profile.endpoint_url)
    if is_remote and not body.confirm_remote_egress:
        hostname = urlsplit(profile.endpoint_url or "").hostname or "the configured host"
        disclosure = RemoteEgressDisclosure(
            host=hostname,
            message=(
                f"This prompt and the generated response will be sent to {hostname}. "
                "Confirm before sending this request."
            ),
        )
        raise HTTPException(status_code=409, detail=disclosure.model_dump(mode="json"))
    if is_remote and not profile.remote_access_enabled:
        raise _http_error(
            409,
            "remote_access_disabled",
            (
                "Remote access is disabled for this profile. Confirm remote use "
                "during its probe first."
            ),
        )

    try:
        manifest = catalog.manifest_for(profile.adapter_id)
    except AdapterNotFoundError as error:
        raise _http_error(
            409, "adapter_unavailable", "This engine adapter is not available."
        ) from error
    if manifest.category is AdapterCategory.TEST_FIXTURE:
        raise _http_error(
            409,
            "inference_engine_required",
            "Test fixtures do not run a language model. Choose a connected inference engine.",
        )
    if not {"chat_completion", "streaming"}.issubset(manifest.declared_capabilities):
        raise _http_error(
            409,
            "streaming_not_supported",
            "This adapter does not declare streaming chat support.",
        )

    snapshot = RecordRepository(connection, CapabilitySnapshot).get(profile.capability_snapshot_id)
    if snapshot is None:
        raise _http_error(409, "engine_probe_required", "Probe this engine before prompting it.")
    unsupported = [
        name
        for name in ("chat_completion", "streaming")
        if name in snapshot.capabilities
        and snapshot.capabilities[name].state is CapabilitySupport.UNSUPPORTED
    ]
    if unsupported:
        raise _http_error(
            409,
            "streaming_not_supported",
            "The latest probe reports that this engine cannot stream chat completions.",
        )

    process_handles = cast(
        dict[UUID, AdapterProcessHandle], request.app.state.engine_process_handles
    )
    if profile.integration_mode.value == "managed_process" and profile.id not in process_handles:
        raise _http_error(
            409, "managed_process_not_running", "Start this engine before prompting it."
        )

    request_id = uuid4()
    generation = GenerationRequest(
        request_id=request_id,
        model_ref=body.model_ref,
        prompt=body.prompt,
        parameters={"max_tokens": body.max_tokens, "temperature": body.temperature},
    )
    config: AdapterConfiguration = _profile_adapter_configuration(profile)
    try:
        prepared = await catalog.prepare_request(profile.adapter_id, config, generation)
    except Exception as error:
        raise _http_error(
            502,
            "inference_request_rejected",
            "The engine could not prepare this prompt. Check its connection and model settings.",
        ) from error

    async def events() -> AsyncIterator[str]:
        started = time.perf_counter()
        terminal_seen = False
        try:
            async for event in catalog.stream_request(profile.adapter_id, prepared):
                if event.event_type in {StreamEventType.FIRST_OUTPUT, StreamEventType.OUTPUT_CHUNK}:
                    if event.text:
                        yield _json_line({"type": "chunk", "text": event.text})
                elif event.event_type is StreamEventType.USAGE and event.usage is not None:
                    yield _json_line(
                        {
                            "type": "usage",
                            "input_tokens": event.usage.input_tokens,
                            "output_tokens": event.usage.output_tokens,
                            "exact": event.usage.exact,
                        }
                    )
                elif event.event_type is StreamEventType.COMPLETED:
                    terminal_seen = True
                    yield _json_line(
                        {
                            "type": "complete",
                            "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
                    return
                elif event.event_type in {
                    StreamEventType.ERROR,
                    StreamEventType.DISCONNECTED,
                    StreamEventType.TIMEOUT,
                    StreamEventType.CANCELLED,
                }:
                    terminal_seen = True
                    code = {
                        StreamEventType.ERROR: "inference_failed",
                        StreamEventType.DISCONNECTED: "engine_disconnected",
                        StreamEventType.TIMEOUT: "inference_timeout",
                        StreamEventType.CANCELLED: "inference_cancelled",
                    }[event.event_type]
                    message = {
                        "inference_failed": "The engine could not complete this prompt.",
                        "engine_disconnected": (
                            "The engine disconnected before completing the prompt."
                        ),
                        "inference_timeout": "The engine timed out before completing this prompt.",
                        "inference_cancelled": "The engine cancelled this prompt.",
                    }[code]
                    yield _json_line({"type": "error", "code": code, "message": message})
                    return
            yield _json_line(
                {
                    "type": "error",
                    "code": "incomplete_stream",
                    "message": "The engine stream ended without a completion event.",
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            yield _json_line(
                {
                    "type": "error",
                    "code": "inference_failed",
                    "message": "The engine stream failed. Check the connection and try again.",
                }
            )
        finally:
            if not terminal_seen:
                try:
                    await asyncio.wait_for(
                        catalog.abort_request(profile.adapter_id, request_id), timeout=1.0
                    )
                except Exception:
                    pass

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
