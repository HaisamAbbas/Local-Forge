"""Local-socket contract tests for the attach-only Ollama adapter."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.contracts import (
    AdapterCapabilities,
    AdapterConfiguration,
    AdapterProcessHandle,
    GenerationRequest,
    ProbeResult,
    ProbeStatus,
    StreamEventType,
    capability,
)
from localforge.adapters.ollama import (
    OLLAMA_ADAPTER_ID,
    AdapterOperationUnsupported,
    OllamaEngineAdapter,
)
from localforge.api.profiles import _persist_probe_models
from localforge.core.models import (
    CapabilitySupport,
    EngineIntegrationMode,
    EngineProfile,
    MetricSource,
    ModelArtifact,
)
from localforge.storage.database import open_database
from localforge.storage.repositories import RecordRepository
from tests.adapter_contract import assert_registered_adapter_contract


@dataclass
class LocalOllamaServer:
    scenario: str = "normal"
    requests: list[dict[str, Any]] = field(default_factory=list)
    server: asyncio.AbstractServer | None = None
    endpoint_url: str = ""

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.endpoint_url = f"http://127.0.0.1:{port}"

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode("latin-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().casefold()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body = json.loads(await reader.readexactly(length)) if length else None
            self.requests.append({"method": method, "path": path, "body": body})

            if method == "HEAD" and path == "/":
                status = 500 if self.scenario == "unhealthy" else 200
                await self._response(writer, status, b"", "application/json")
            elif method == "GET" and path == "/api/version":
                if self.scenario == "version_unknown":
                    await self._json(writer, 200, {"version": None})
                elif self.scenario == "version_timeout":
                    await asyncio.sleep(0.25)
                    await self._json(writer, 200, {"version": "0.12.0"})
                else:
                    await self._json(writer, 200, {"version": "0.12.0"})
            elif method == "GET" and path == "/api/tags":
                if self.scenario in {"inventory_failure", "health_only"}:
                    await self._json(
                        writer, 503 if self.scenario == "inventory_failure" else 404, {}
                    )
                else:
                    await self._json(writer, 200, self._tags())
            elif method == "POST" and path == "/api/chat":
                if self.scenario == "chat_error":
                    await self._json(
                        writer,
                        400,
                        {"error": "test-secret-value must never escape"},
                    )
                elif self.scenario == "chat_timeout":
                    await asyncio.sleep(0.25)
                    await self._json(writer, 200, self._completion())
                elif isinstance(body, dict) and body.get("stream") is False:
                    await self._json(writer, 200, self._completion())
                else:
                    await self._stream(writer, incomplete=self.scenario == "incomplete_stream")
            else:
                await self._json(writer, 404, {"error": "unrecognized local fixture route"})
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    @staticmethod
    def _tags() -> dict[str, Any]:
        return {
            "models": [
                {
                    "name": "fixture-model:latest",
                    "model": "fixture-model:latest",
                    "digest": "sha256:fixture-digest",
                    "size": 1024,
                    "details": {
                        "format": "gguf",
                        "family": "llama",
                        "families": ["llama"],
                        "parameter_size": "7B",
                        "quantization_level": "Q4_K_M",
                    },
                },
                {
                    "name": "cloud-model:latest",
                    "model": "cloud-model:latest",
                    "remote_model": "provider/model",
                    "remote_host": "https://cloud.example",
                },
            ]
        }

    @staticmethod
    def _completion() -> dict[str, Any]:
        return {
            "model": "fixture-model:latest",
            "message": {"role": "assistant", "content": "hello from Ollama"},
            "done": True,
            "total_duration": 2_500_000,
            "load_duration": 500_000,
            "prompt_eval_count": 6,
            "prompt_eval_duration": 700_000,
            "eval_count": 3,
            "eval_duration": 900_000,
        }

    async def _stream(self, writer: asyncio.StreamWriter, *, incomplete: bool) -> None:
        chunks = [
            {
                "model": "fixture-model:latest",
                "message": {"role": "assistant", "content": "hello "},
                "done": False,
            },
            {
                "model": "fixture-model:latest",
                "message": {"role": "assistant", "content": "from Ollama"},
                "done": False,
            },
        ]
        if not incomplete:
            final = self._completion()
            final["message"] = {"role": "assistant", "content": ""}
            chunks.append(final)
        body = b"".join(json.dumps(chunk).encode() + b"\n" for chunk in chunks)
        await self._response(writer, 200, body, "application/x-ndjson")

    @staticmethod
    async def _json(writer: asyncio.StreamWriter, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        await LocalOllamaServer._response(writer, status, body, "application/json")

    @staticmethod
    async def _response(
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            500: "Error",
            503: "Unavailable",
        }.get(status, "Error")
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(headers + body)
        await writer.drain()


def _config(endpoint_url: str, *, timeout: float = 2.0, mode: str = "auto") -> AdapterConfiguration:
    return AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values={
            "endpoint_url": endpoint_url,
            "engine_extensions": {"ollama": {"timeout_seconds": timeout, "streaming_mode": mode}},
        },
    )


async def _events(
    adapter: OllamaEngineAdapter,
    config: AdapterConfiguration,
    *,
    model_ref: str = "fixture-model:latest",
    parameters: dict[str, Any] | None = None,
) -> list[Any]:
    prepared = await adapter.prepare_request(
        config,
        GenerationRequest(
            model_ref=model_ref, prompt="a private local prompt", parameters=parameters or {}
        ),
    )
    return [event async for event in adapter.stream_request(prepared)]


def test_ollama_passes_shared_adapter_contract_and_stays_attach_only() -> None:
    async def run() -> None:
        server = LocalOllamaServer()
        await server.start()
        adapter = OllamaEngineAdapter()
        catalog = AdapterCatalog()
        manifest = catalog.register(adapter)
        config = _config(server.endpoint_url)
        try:
            assert manifest.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
            await assert_registered_adapter_contract(catalog, OLLAMA_ADAPTER_ID, config)
            probe = await catalog.probe(OLLAMA_ADAPTER_ID, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            assert len(probe.models) == 1
            assert probe.models[0].metadata["runner"] == "unknown"
            assert probe.models[0].metadata["runner_state"] == "unknown"
            assert any(item.code == "remote_models_omitted" for item in probe.diagnostics)
            assert all(
                request["path"] in {"/", "/api/version", "/api/tags", "/api/chat"}
                for request in server.requests
            )
            assert not any(
                request["path"].endswith(("/pull", "/delete")) for request in server.requests
            )
            assert not any(request["method"] == "DELETE" for request in server.requests)

            process = AdapterProcessHandle(adapter_id=OLLAMA_ADAPTER_ID)
            with pytest.raises(AdapterOperationUnsupported, match="never stops"):
                await adapter.stop(process)
            with pytest.raises(AdapterOperationUnsupported, match="never starts"):
                await adapter.start(config)
            assert server.server is not None and server.server.is_serving()
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_chat_stream_normalizes_usage_and_server_timings() -> None:
    async def run() -> None:
        server = LocalOllamaServer()
        await server.start()
        adapter = OllamaEngineAdapter()
        config = _config(server.endpoint_url)
        try:
            events = await _events(
                adapter,
                config,
                parameters={
                    "temperature": 3.5,
                    "top_p": 0.9,
                    "top_k": 40,
                    "max_tokens": 32,
                    "stop_sequences": ["done"],
                },
            )
            assert "".join(event.text or "" for event in events) == "hello from Ollama"
            assert events[0].event_type is StreamEventType.FIRST_OUTPUT
            usage = next(event for event in events if event.event_type is StreamEventType.USAGE)
            assert usage.usage is not None
            assert (usage.usage.input_tokens, usage.usage.output_tokens, usage.usage.exact) == (
                6,
                3,
                True,
            )
            assert {metric.metric_name: metric.value for metric in usage.native_metrics} == {
                "ollama.total_duration": 2_500_000,
                "ollama.load_duration": 500_000,
                "ollama.prompt_eval_duration": 700_000,
                "ollama.eval_duration": 900_000,
                "ollama.prompt_eval_count": 6,
                "ollama.eval_count": 3,
            }
            assert {metric.metric_name: metric.unit for metric in usage.native_metrics} == {
                "ollama.total_duration": "ns",
                "ollama.load_duration": "ns",
                "ollama.prompt_eval_duration": "ns",
                "ollama.eval_duration": "ns",
                "ollama.prompt_eval_count": "tokens",
                "ollama.eval_count": "tokens",
            }
            assert all(
                metric.source is MetricSource.ENGINE_NATIVE for metric in usage.native_metrics
            )
            chat = next(request for request in server.requests if request["path"] == "/api/chat")
            assert chat["body"]["stream"] is True
            assert chat["body"]["options"]["temperature"] == 3.5
            assert chat["body"]["options"]["stop"] == ["done"]
            assert chat["body"]["options"]["num_predict"] == 32
            assert all("a private local prompt" not in str(event.model_dump()) for event in events)
        finally:
            await adapter.close()
            await server.close()

    asyncio.run(run())


def test_nonstream_response_maps_usage_and_options() -> None:
    async def run() -> None:
        server = LocalOllamaServer()
        await server.start()
        adapter = OllamaEngineAdapter()
        config = _config(server.endpoint_url, mode="disabled")
        try:
            events = await _events(adapter, config, parameters={"max_tokens": 48, "seed": 8})
            assert "".join(event.text or "" for event in events) == "hello from Ollama"
            assert events[-1].event_type is StreamEventType.COMPLETED
            chat = next(request for request in server.requests if request["path"] == "/api/chat")
            assert chat["body"]["stream"] is False
            assert chat["body"]["options"] == {"seed": 8, "num_predict": 48}
            usage = next(event for event in events if event.event_type is StreamEventType.USAGE)
            assert usage.usage is not None and usage.usage.input_tokens == 6
            assert len(usage.native_metrics) == 6
        finally:
            await adapter.close()
            await server.close()

    asyncio.run(run())


def test_missing_model_is_blocked_and_no_chat_or_download_is_attempted() -> None:
    async def run() -> None:
        server = LocalOllamaServer()
        await server.start()
        adapter = OllamaEngineAdapter()
        config = _config(server.endpoint_url)
        try:
            with pytest.raises(
                AdapterOperationUnsupported, match="not verified as locally installed"
            ):
                await adapter.prepare_request(
                    config,
                    GenerationRequest(model_ref="cloud-model:latest", prompt="private"),
                )
            assert not any(request["path"] == "/api/chat" for request in server.requests)
            assert all(request["path"] != "/api/pull" for request in server.requests)
        finally:
            await adapter.close()
            await server.close()

    asyncio.run(run())


def test_probe_succeeds_when_inventory_is_missing_or_unavailable() -> None:
    async def run() -> None:
        for scenario in ("health_only", "inventory_failure"):
            server = LocalOllamaServer(scenario)
            await server.start()
            catalog = AdapterCatalog()
            catalog.register(OllamaEngineAdapter())
            try:
                result = await catalog.probe(OLLAMA_ADAPTER_ID, _config(server.endpoint_url))
                assert result.status is ProbeStatus.SUCCEEDED
                assert result.health.state is CapabilitySupport.SUPPORTED
                assert result.checks["model_inventory"].state is CapabilitySupport.UNKNOWN
                assert result.models == []
                assert not any(request["method"] == "POST" for request in server.requests)
                assert not any(
                    request["path"].endswith(("/pull", "/delete")) for request in server.requests
                )
            finally:
                await catalog.close()
                await server.close()

    asyncio.run(run())


def test_incomplete_stream_and_http_error_are_safe() -> None:
    async def run() -> None:
        server = LocalOllamaServer("incomplete_stream")
        await server.start()
        adapter = OllamaEngineAdapter()
        config = _config(server.endpoint_url)
        try:
            events = await _events(adapter, config)
            assert events[-1].event_type is StreamEventType.DISCONNECTED
        finally:
            await adapter.close()
            await server.close()

        error_server = LocalOllamaServer("chat_error")
        await error_server.start()
        adapter = OllamaEngineAdapter()
        try:
            events = await _events(adapter, _config(error_server.endpoint_url))
            assert events[-1].event_type is StreamEventType.ERROR
            assert "test-secret-value" not in str([event.model_dump() for event in events])
        finally:
            await adapter.close()
            await error_server.close()

    asyncio.run(run())


def test_timeout_and_unknown_version_are_reported_without_guessing() -> None:
    async def run() -> None:
        server = LocalOllamaServer("version_unknown")
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(OllamaEngineAdapter())
        config = _config(server.endpoint_url)
        try:
            probe = await catalog.probe(OLLAMA_ADAPTER_ID, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            assert probe.engine_version is None
            assert probe.checks["version_discovery"].state is CapabilitySupport.UNKNOWN
        finally:
            await catalog.close()
            await server.close()

        slow_server = LocalOllamaServer("chat_timeout")
        await slow_server.start()
        adapter = OllamaEngineAdapter()
        try:
            events = await _events(adapter, _config(slow_server.endpoint_url, timeout=0.1))
            assert events[-1].event_type is StreamEventType.TIMEOUT
        finally:
            await adapter.close()
            await slow_server.close()

    asyncio.run(run())


def test_runner_provenance_is_persisted_only_as_unknown(tmp_path) -> None:
    database_path = tmp_path / "localforge.sqlite3"
    from localforge.storage.database import initialize_database

    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        profile = EngineProfile(
            label="Local Ollama",
            adapter_id=OLLAMA_ADAPTER_ID,
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            endpoint_url="http://127.0.0.1:11434",
        )
        result = ProbeResult(
            adapter_id=OLLAMA_ADAPTER_ID,
            adapter_version="1.0.0",
            status=ProbeStatus.SUCCEEDED,
            capabilities=AdapterCapabilities(
                adapter_id=OLLAMA_ADAPTER_ID,
                adapter_version="1.0.0",
                capabilities={"model_inventory": capability(CapabilitySupport.SUPPORTED)},
            ),
            models=[
                {
                    "model_ref": "fixture-model:latest",
                    "display_name": "fixture-model:latest",
                    "metadata": {"runner": "unknown", "runner_state": "unknown", "family": "llama"},
                }
            ],
        )
        _persist_probe_models(connection, profile, result)
        artifact = RecordRepository(connection, ModelArtifact).list(limit=1)[0]
        assert artifact.engine_extensions["ollama"] == {
            "runner": "unknown",
            "runner_state": "unknown",
            "family": "llama",
        }
    finally:
        connection.close()


def test_ollama_requests_ask_for_a_context_large_enough_for_the_prompt() -> None:
    from localforge.adapters.contracts import AdapterConfiguration, PreparedRequest
    from localforge.adapters.ollama import OllamaEngineAdapter, required_context_tokens
    from localforge.core.models import EngineIntegrationMode

    config = AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values={"endpoint_url": "http://127.0.0.1:11434"},
    )

    def options_for(prompt: str, **parameters: object) -> dict[str, Any]:
        request = PreparedRequest(model_ref="m", prompt=prompt, transport="ollama")
        payload = OllamaEngineAdapter._chat_payload(
            request, {"max_tokens": 128, **parameters}, config
        )
        return payload.get("options", {})  # type: ignore[no-any-return]

    # Short prompts keep Ollama's default window; a ~8k-token prompt gets 16384 so it is not cut.
    assert "num_ctx" not in options_for("hello " * 50)
    assert options_for("word " * 6_600)["num_ctx"] == 16_384
    # An explicit value wins, and sizes step by powers of two so the model is not reloaded.
    assert options_for("word " * 6_600, num_ctx=32_768)["num_ctx"] == 32_768
    assert required_context_tokens("x" * 7_000, 128) == 4_096
    assert required_context_tokens("x" * 7_800, 128) == 4_096
    assert required_context_tokens("x" * 12_000, 128) == 8_192
    assert required_context_tokens("x" * 10_000_000, 128) == 131_072
