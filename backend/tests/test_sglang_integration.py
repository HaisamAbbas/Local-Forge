"""Opt-in live smoke test for an SGLang server with a model already loaded."""

import asyncio
import os

import pytest

from localforge.adapters.contracts import (
    AdapterConfiguration,
    GenerationRequest,
    StreamEventType,
)
from localforge.adapters.sglang import SglangEngineAdapter
from localforge.core.models import EngineIntegrationMode

_ENABLED = (
    os.environ.get("LOCALFORGE_SGLANG_INTEGRATION") == "1"
    and bool(os.environ.get("LOCALFORGE_SGLANG_BASE_URL"))
    and bool(os.environ.get("LOCALFORGE_SGLANG_MODEL_ID"))
)

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason=(
        "Set LOCALFORGE_SGLANG_INTEGRATION=1, a base URL, and an already served model ID "
        "to opt in to the live SGLang smoke test."
    ),
)


def test_opt_in_live_sglang_chat_completion() -> None:
    """Probe the caller's server, then send one short completion to its loaded model."""

    endpoint = os.environ["LOCALFORGE_SGLANG_BASE_URL"]
    model_id = os.environ["LOCALFORGE_SGLANG_MODEL_ID"]
    credential_ref = os.environ.get("LOCALFORGE_SGLANG_CREDENTIAL_REF")
    config = AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values={
            "endpoint_url": endpoint,
            "engine_extensions": {"sglang": {"model_id": model_id, "timeout_seconds": 60}},
        },
        secret_refs={"api_key": credential_ref} if credential_ref else {},
    )

    async def run() -> None:
        adapter = SglangEngineAdapter()
        try:
            validation = await adapter.validate_config(config)
            assert validation.valid, validation.diagnostics
            probe = await adapter.probe(config)
            assert probe.status.value == "succeeded", probe.diagnostics
            assert model_id in {item.model_ref for item in probe.models}, (
                "the configured model must already be served by SGLang"
            )
            prepared = await adapter.prepare_request(
                config,
                GenerationRequest(
                    model_ref=model_id,
                    prompt="Reply with exactly OK.",
                    parameters={"max_tokens": 3},
                ),
            )
            events = [event async for event in adapter.stream_request(prepared)]
            assert events[-1].event_type is StreamEventType.COMPLETED
            assert any(
                event.event_type in {StreamEventType.FIRST_OUTPUT, StreamEventType.OUTPUT_CHUNK}
                and event.text
                for event in events
            )
        finally:
            await adapter.close()

    asyncio.run(run())
