"""Local-socket contract tests for the generic OpenAI-compatible adapter."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.contracts import (
    AdapterConfiguration,
    GenerationRequest,
    ProbeStatus,
    StreamEventType,
)
from localforge.adapters.openai_compatible import (
    OPENAI_COMPATIBLE_ADAPTER_ID,
    OpenAICompatibleEngineAdapter,
)
from localforge.core.models import CapabilitySupport, EngineIntegrationMode


@dataclass
class LocalOpenAIMock:
    scenario: str
    expected_api_key: str | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    release_stream: asyncio.Event = field(default_factory=asyncio.Event)
    server: asyncio.AbstractServer | None = None
    base_url: str = ""

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        socket = self.server.sockets[0]
        port = socket.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}/v1"

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        self.release_stream.set()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_headers = await reader.readuntil(b"\r\n\r\n")
            lines = raw_headers.decode("latin-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().casefold()] = value.strip()
            length = int(headers.get("content-length", "0"))
            raw_body = await reader.readexactly(length) if length else b""
            body = json.loads(raw_body) if raw_body else None
            self.requests.append({"method": method, "path": path, "headers": headers, "body": body})

            if self.expected_api_key is not None and headers.get("authorization") != (
                f"Bearer {self.expected_api_key}"
            ):
                await self._json(writer, 401, {"error": {"message": "auth rejected"}})
                return

            if method == "GET" and path == "/v1/models":
                if self.scenario == "unauthorized":
                    await self._json(
                        writer,
                        401,
                        {"error": {"message": "Authorization rejected: test-secret-value"}},
                    )
                elif self.scenario == "no_model_list":
                    await self._json(writer, 404, {"error": {"message": "not available"}})
                elif self.scenario == "timeout":
                    await asyncio.sleep(0.25)
                    await self._json(writer, 200, {"data": [{"id": "fixture-model"}]})
                else:
                    await self._json(writer, 200, {"data": [{"id": "fixture-model"}]})
                return

            if method == "GET" and path == "/health":
                await self._json(writer, 200, {"status": "ok"})
                return

            if method == "POST" and path == "/v1/chat/completions":
                if self.scenario == "rate_limited":
                    await self._json(
                        writer,
                        429,
                        {"error": {"message": "test-secret-value should not be exposed"}},
                    )
                    return
                if self.scenario == "disconnect":
                    writer.close()
                    await writer.wait_closed()
                    return
                if self.scenario == "timeout":
                    await asyncio.sleep(0.25)
                    await self._json(writer, 200, self._completion())
                    return
                streaming = bool((body or {}).get("stream"))
                if streaming and self.scenario == "stream_unsupported":
                    await self._json(
                        writer,
                        400,
                        {"error": {"message": "Streaming is not supported by this endpoint."}},
                    )
                    return
                if streaming:
                    await self._stream(writer)
                else:
                    await self._json(writer, 200, self._completion())
                return

            await self._json(writer, 404, {"error": {"message": "not found"}})
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    @staticmethod
    def _completion(*, with_usage: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": "local-mock-completion",
            "object": "chat.completion",
            "model": "fixture-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
        }
        if with_usage:
            result["usage"] = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
        return result

    @staticmethod
    async def _json(writer: asyncio.StreamWriter, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        reason = {
            200: "OK",
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            429: "Too Many Requests",
        }.get(status, "Error")
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(headers + body)
        await writer.drain()

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: close\r\n\r\n"
        )
        await writer.drain()
        if self.scenario == "malformed":
            writer.write(b"data: {not json}\n\n")
            await writer.drain()
            return
        first = {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
        second = {"choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}]}
        for payload in (first, second):
            writer.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
            await writer.drain()
        if self.scenario == "cancel":
            await self.release_stream.wait()
            return
        if self.scenario == "disconnect_after_output":
            return
        if self.scenario != "missing_usage":
            usage = {
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }
            writer.write(b"data: " + json.dumps(usage).encode() + b"\n\n")
        writer.write(b"data: [DONE]\n\n")
        await writer.drain()


def _configuration(
    endpoint: LocalOpenAIMock,
    *,
    extension: dict[str, Any] | None = None,
    health_route: str | None = None,
    secret_refs: dict[str, str] | None = None,
) -> AdapterConfiguration:
    values: dict[str, Any] = {"endpoint_url": endpoint.base_url}
    if health_route:
        values["health_route"] = health_route
    if extension:
        values["engine_extensions"] = {"openai_compatible": extension}
    return AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values=values,
        secret_refs=secret_refs or {},
    )


async def _start(endpoint: LocalOpenAIMock) -> AdapterCatalog:
    await endpoint.start()
    return AdapterCatalog()


@pytest.mark.parametrize("scenario", ["stream", "missing_usage"])
def test_streaming_completion_normalizes_chunks_and_optional_usage(scenario: str) -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock(scenario)
        catalog = await _start(endpoint)
        adapter = OpenAICompatibleEngineAdapter()
        catalog.register(adapter)
        config = _configuration(endpoint)
        try:
            probe = await catalog.probe(OPENAI_COMPATIBLE_ADAPTER_ID, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            assert probe.engine_version is None
            assert probe.checks["streaming"].state is CapabilitySupport.SUPPORTED
            model = (await catalog.list_models(OPENAI_COMPATIBLE_ADAPTER_ID, config))[0]
            prepared = await catalog.prepare_request(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                config,
                GenerationRequest(model_ref=model.model_ref, prompt="private prompt"),
            )
            events = [
                event
                async for event in catalog.stream_request(OPENAI_COMPATIBLE_ADAPTER_ID, prepared)
            ]
            assert [event.text for event in events if event.text] == ["hello"]
            assert events[0].event_type is StreamEventType.FIRST_OUTPUT
            assert events[-1].event_type is StreamEventType.COMPLETED
            usage_events = [event for event in events if event.event_type is StreamEventType.USAGE]
            assert bool(usage_events) is (scenario == "stream")
            assert all("private prompt" not in str(event.model_dump()) for event in events)
            assert all(
                request["path"] == "/v1/models"
                for request in endpoint.requests
                if request["method"] == "GET"
            )
            assert all(
                "engine" not in str(request["body"]).casefold()
                for request in endpoint.requests
                if request["body"] is not None
            )
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


def test_non_streaming_completion_and_optional_usage() -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock("stream")
        catalog = await _start(endpoint)
        catalog.register(OpenAICompatibleEngineAdapter())
        config = _configuration(endpoint, extension={"streaming_mode": "disabled"})
        try:
            prepared = await catalog.prepare_request(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                config,
                GenerationRequest(model_ref="fixture-model", prompt="short prompt"),
            )
            events = [
                event
                async for event in catalog.stream_request(OPENAI_COMPATIBLE_ADAPTER_ID, prepared)
            ]
            assert events[0].event_type is StreamEventType.FIRST_OUTPUT
            assert events[0].text == "hello"
            assert any(event.event_type is StreamEventType.USAGE for event in events)
            assert events[-1].event_type is StreamEventType.COMPLETED
            assert endpoint.requests[-1]["body"]["stream"] is False
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


def test_healthy_endpoint_without_model_list_uses_explicit_model_id() -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock("no_model_list")
        catalog = await _start(endpoint)
        catalog.register(OpenAICompatibleEngineAdapter())
        config = _configuration(
            endpoint,
            extension={"model_id": "user-selected-model"},
            health_route="/health",
        )
        try:
            result = await catalog.probe(OPENAI_COMPATIBLE_ADAPTER_ID, config)
            assert result.status is ProbeStatus.SUCCEEDED
            assert result.engine_version is None
            assert result.models[0].model_ref == "user-selected-model"
            assert result.models[0].metadata["identity"] == "unknown/custom"
            assert result.checks["model_inventory"].state is CapabilitySupport.UNSUPPORTED
            assert result.checks["health"].state is CapabilitySupport.SUPPORTED
            assert any(item.code == "healthy_without_model_list" for item in result.diagnostics)
            metrics = await catalog.read_metrics(OPENAI_COMPATIBLE_ADAPTER_ID, config)
            assert not metrics.available
            fingerprint = await catalog.collect_fingerprint(OPENAI_COMPATIBLE_ADAPTER_ID, config)
            assert fingerprint.engine_version is None
            assert fingerprint.settings["openai_compatible"]["model_id"] == "user-selected-model"
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


def test_streaming_unsupported_falls_back_only_after_explicit_rejection() -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock("stream_unsupported")
        catalog = await _start(endpoint)
        catalog.register(OpenAICompatibleEngineAdapter())
        config = _configuration(endpoint)
        try:
            probe = await catalog.probe(OPENAI_COMPATIBLE_ADAPTER_ID, config)
            assert probe.checks["streaming"].state is CapabilitySupport.UNSUPPORTED
            prepared = await catalog.prepare_request(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                config,
                GenerationRequest(model_ref="fixture-model", prompt="fallback prompt"),
            )
            events = [
                event
                async for event in catalog.stream_request(OPENAI_COMPATIBLE_ADAPTER_ID, prepared)
            ]
            assert events[0].text == "hello"
            assert events[-1].event_type is StreamEventType.COMPLETED
            posts = [request for request in endpoint.requests if request["method"] == "POST"]
            assert [request["body"]["stream"] for request in posts[-2:]] == [True, False]
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("malformed", StreamEventType.ERROR),
        ("rate_limited", StreamEventType.ERROR),
        ("disconnect", StreamEventType.DISCONNECTED),
    ],
)
def test_stream_errors_are_safe_and_actionable(scenario: str, expected: StreamEventType) -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock(scenario)
        catalog = await _start(endpoint)
        catalog.register(
            OpenAICompatibleEngineAdapter(
                credential_resolver=lambda _reference: "test-secret-value"
            )
        )
        config = _configuration(
            endpoint,
            secret_refs={"api_key": "keyring://LocalForge/test"},
        )
        try:
            prepared = await catalog.prepare_request(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                config,
                GenerationRequest(model_ref="fixture-model", prompt="do not expose this"),
            )
            events = [
                event
                async for event in catalog.stream_request(OPENAI_COMPATIBLE_ADAPTER_ID, prepared)
            ]
            assert events[-1].event_type is expected
            assert "test-secret-value" not in str([event.model_dump() for event in events])
            assert "do not expose this" not in str([event.model_dump() for event in events])
            if scenario == "rate_limited":
                assert events[-1].error_code == "endpoint_rate_limited"
            if scenario == "malformed":
                assert events[-1].error_code == "malformed_stream_event"
            assert endpoint.requests[-1]["headers"]["authorization"] == "Bearer test-secret-value"
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


def test_probe_distinguishes_unauthorized_and_redacts_response_body() -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock("unauthorized")
        catalog = await _start(endpoint)
        catalog.register(OpenAICompatibleEngineAdapter())
        try:
            result = await catalog.probe(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                _configuration(endpoint),
            )
            assert result.status is ProbeStatus.FAILED
            assert result.reachable.state is CapabilitySupport.SUPPORTED
            assert any(item.code == "endpoint_unauthorized" for item in result.diagnostics)
            assert "test-secret-value" not in str(result.model_dump())
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


def test_probe_timeout_is_reported_without_endpoint_exception_details() -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock("timeout")
        catalog = await _start(endpoint)
        catalog.register(OpenAICompatibleEngineAdapter())
        try:
            result = await catalog.probe(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                _configuration(endpoint, extension={"timeout_seconds": 0.1}),
            )
            assert result.status is ProbeStatus.FAILED
            assert any(item.code == "endpoint_timeout" for item in result.diagnostics)
            assert "127.0.0.1" not in str(result.diagnostics)
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


def test_abort_closes_active_http_stream_and_returns_cancelled_event() -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock("cancel")
        catalog = await _start(endpoint)
        catalog.register(OpenAICompatibleEngineAdapter())
        config = _configuration(endpoint)
        try:
            prepared = await catalog.prepare_request(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                config,
                GenerationRequest(model_ref="fixture-model", prompt="cancellation test"),
            )
            stream = catalog.stream_request(OPENAI_COMPATIBLE_ADAPTER_ID, prepared)
            first = await asyncio.wait_for(anext(stream), timeout=2)
            assert first.event_type is StreamEventType.FIRST_OUTPUT
            abort = await catalog.abort_request(OPENAI_COMPATIBLE_ADAPTER_ID, prepared.request_id)
            assert abort.supported and abort.accepted
            remaining = [event async for event in stream]
            assert remaining[-1].event_type is StreamEventType.CANCELLED
        finally:
            endpoint.release_stream.set()
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())


def test_no_retry_occurs_after_stream_output_and_url_checks_are_strict() -> None:
    async def run() -> None:
        endpoint = LocalOpenAIMock("disconnect_after_output")
        catalog = await _start(endpoint)
        catalog.register(OpenAICompatibleEngineAdapter())
        config = _configuration(endpoint, extension={"max_retries": 2})
        try:
            prepared = await catalog.prepare_request(
                OPENAI_COMPATIBLE_ADAPTER_ID,
                config,
                GenerationRequest(model_ref="fixture-model", prompt="one attempt"),
            )
            events = [
                event
                async for event in catalog.stream_request(OPENAI_COMPATIBLE_ADAPTER_ID, prepared)
            ]
            assert any(event.event_type is StreamEventType.FIRST_OUTPUT for event in events)
            assert events[-1].event_type is StreamEventType.DISCONNECTED
            assert (
                len([request for request in endpoint.requests if request["method"] == "POST"]) == 1
            )
            invalid = AdapterConfiguration(
                integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
                values={"endpoint_url": "https://user:password@example.test/v1?api_key=hidden"},
            )
            validation = await catalog.validate_config(OPENAI_COMPATIBLE_ADAPTER_ID, invalid)
            assert not validation.valid
            assert "password" not in str(validation.diagnostics)
        finally:
            await catalog.close()
            await endpoint.close()

    asyncio.run(run())
