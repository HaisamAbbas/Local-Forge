"""Explicitly opt-in live endpoint smoke test; skipped in default CI and local checks."""

import asyncio
import os

import pytest

from localforge.adapters.contracts import (
    AdapterConfiguration,
    EngineIntegrationMode,
    GenerationRequest,
    StreamEventType,
)
from localforge.adapters.openai_compatible import OpenAICompatibleEngineAdapter

_ENABLED = (
    os.environ.get("LOCALFORGE_OPENAI_COMPATIBLE_INTEGRATION") == "1"
    and bool(os.environ.get("LOCALFORGE_OPENAI_COMPATIBLE_BASE_URL"))
    and bool(os.environ.get("LOCALFORGE_OPENAI_COMPATIBLE_MODEL_ID"))
)

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason=(
        "Set LOCALFORGE_OPENAI_COMPATIBLE_INTEGRATION=1, a base URL, and a model ID "
        "to opt in to a real endpoint request."
    ),
)


def test_opt_in_live_chat_completion() -> None:
    """Send one tiny request only after the caller explicitly enables this live test."""

    endpoint = os.environ["LOCALFORGE_OPENAI_COMPATIBLE_BASE_URL"]
    model_id = os.environ["LOCALFORGE_OPENAI_COMPATIBLE_MODEL_ID"]
    credential_ref = os.environ.get("LOCALFORGE_OPENAI_COMPATIBLE_CREDENTIAL_REF")
    config = AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values={
            "endpoint_url": endpoint,
            "engine_extensions": {
                "openai_compatible": {
                    "model_id": model_id,
                    "max_retries": 0,
                    "streaming_mode": "auto",
                    "timeout_seconds": 30,
                }
            },
        },
        secret_refs={"api_key": credential_ref} if credential_ref else {},
    )

    async def send_smoke_request() -> None:
        adapter = OpenAICompatibleEngineAdapter()
        try:
            prepared = await adapter.prepare_request(
                config,
                GenerationRequest(
                    model_ref=model_id,
                    prompt="Reply with exactly OK.",
                    parameters={"max_tokens": 3},
                ),
            )
            events = [event async for event in adapter.stream_request(prepared)]
            assert any(event.event_type is StreamEventType.COMPLETED for event in events)
            assert not any(event.event_type is StreamEventType.ERROR for event in events)
        finally:
            await adapter.close()

    asyncio.run(send_smoke_request())
