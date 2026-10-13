"""Opt-in smoke test for a preinstalled model on a locally running Ollama service."""

import asyncio
import os

import pytest

from localforge.adapters.contracts import AdapterConfiguration, GenerationRequest, StreamEventType
from localforge.adapters.ollama import OllamaEngineAdapter
from localforge.core.models import EngineIntegrationMode


def test_preinstalled_local_ollama_model_smoke() -> None:
    if os.environ.get("LOCALFORGE_OLLAMA_INTEGRATION") != "1":
        pytest.skip("set LOCALFORGE_OLLAMA_INTEGRATION=1 to run the local Ollama smoke test")
    model = os.environ.get("LOCALFORGE_OLLAMA_MODEL", "").strip()
    if not model:
        pytest.fail("LOCALFORGE_OLLAMA_MODEL must name a model already installed in local Ollama")

    async def run() -> None:
        endpoint = os.environ.get("LOCALFORGE_OLLAMA_URL", "http://127.0.0.1:11434")
        adapter = OllamaEngineAdapter()
        config = AdapterConfiguration(
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            values={"endpoint_url": endpoint},
        )
        try:
            validation = await adapter.validate_config(config)
            assert validation.valid, validation.diagnostics
            probe = await adapter.probe(config)
            assert probe.status.value == "succeeded", probe.diagnostics
            assert model in {item.model_ref for item in probe.models}, (
                "the configured model must already be present in Ollama's local model inventory"
            )
            prepared = await adapter.prepare_request(
                config,
                GenerationRequest(
                    model_ref=model,
                    prompt="Reply with one short greeting.",
                    parameters={"max_tokens": 16},
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
