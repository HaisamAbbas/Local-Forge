"""Opt-in smoke test for a user-supplied local llama-server and existing GGUF model."""

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.contracts import AdapterConfiguration, GenerationRequest, ProbeStatus
from localforge.adapters.llama_cpp import LLAMA_CPP_ADAPTER_ID, LlamaCppEngineAdapter
from localforge.core.models import EngineIntegrationMode
from localforge.core.urls import is_loopback_host

ENABLED = os.environ.get("LOCALFORGE_LLAMA_CPP_INTEGRATION") == "1"
BASE_URL = os.environ.get("LOCALFORGE_LLAMA_CPP_BASE_URL", "")
MODEL_ID = os.environ.get("LOCALFORGE_LLAMA_CPP_MODEL_ID", "")
MODEL_PATH = os.environ.get("LOCALFORGE_LLAMA_CPP_MODEL_PATH", "")

pytestmark = pytest.mark.skipif(
    not ENABLED or not BASE_URL or not MODEL_ID or not MODEL_PATH,
    reason=(
        "Set LOCALFORGE_LLAMA_CPP_INTEGRATION=1, a local base URL, a model ID, and an existing "
        "GGUF model path to opt in."
    ),
)


def test_user_supplied_local_llama_server_and_model_smoke() -> None:
    model_path = Path(MODEL_PATH)
    assert model_path.is_file() and model_path.suffix.casefold() == ".gguf"
    parsed = urlsplit(BASE_URL)
    assert parsed.scheme == "http" and is_loopback_host(parsed.hostname or "")

    async def run() -> None:
        catalog = AdapterCatalog()
        catalog.register(LlamaCppEngineAdapter())
        config = AdapterConfiguration(
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            values={
                "endpoint_url": BASE_URL,
                "engine_extensions": {
                    "llama_cpp": {
                        "model_id": MODEL_ID,
                        "timeout_seconds": 15,
                    }
                },
            },
        )
        try:
            probe = await catalog.probe(LLAMA_CPP_ADAPTER_ID, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            request = await catalog.prepare_request(
                LLAMA_CPP_ADAPTER_ID,
                config,
                GenerationRequest(
                    model_ref=MODEL_ID,
                    prompt="Reply with exactly OK.",
                    parameters={"max_tokens": 8, "temperature": 0},
                ),
            )
            events = [
                event async for event in catalog.stream_request(LLAMA_CPP_ADAPTER_ID, request)
            ]
            assert any(event.text for event in events)
            assert events[-1].event_type.value == "completed"
            await catalog.read_metrics(LLAMA_CPP_ADAPTER_ID, config)
        finally:
            await catalog.close()

    asyncio.run(run())
