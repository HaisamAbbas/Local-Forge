"""Fixture-driven llama.cpp adapter and managed-process contract coverage."""

import asyncio
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.contracts import (
    AdapterConfiguration,
    AdapterProcessHandle,
    GenerationRequest,
    ProbeStatus,
    StreamEventType,
)
from localforge.adapters.llama_cpp import (
    LLAMA_CPP_ADAPTER_ID,
    LLAMA_CPP_TESTED_COMMIT,
    LLAMA_CPP_TESTED_VERSION,
    LlamaCppEngineAdapter,
    _metrics_from_prometheus,
)
from localforge.core.models import CapabilitySupport, EngineIntegrationMode, MetricSource
from tests.adapter_contract import assert_registered_adapter_contract


@dataclass
class LocalLlamaServer:
    scenario: str = "healthy"
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

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_headers = await reader.readuntil(b"\r\n\r\n")
            lines = raw_headers.decode("latin-1").split("\r\n")
            method, raw_path, _version = lines[0].split(" ", 2)
            path = urlsplit(raw_path).path
            headers = {
                key.strip().casefold(): value.strip()
                for line in lines[1:]
                if ":" in line
                for key, value in [line.split(":", 1)]
            }
            length = int(headers.get("content-length", "0"))
            raw_body = await reader.readexactly(length) if length else b""
            payload = json.loads(raw_body) if raw_body else None
            self.requests.append(
                {"method": method, "path": raw_path, "body": payload, "headers": headers}
            )

            if method == "GET" and path == "/health":
                await self._json(writer, 200, {"status": "ok"})
                return
            if method == "GET" and path == "/props":
                if self.scenario == "props_timeout":
                    await asyncio.sleep(0.25)
                build_info = (
                    "b99999-0000000"
                    if self.scenario == "bad_version"
                    else f"{LLAMA_CPP_TESTED_VERSION}-{LLAMA_CPP_TESTED_COMMIT[:7]}"
                )
                await self._json(
                    writer,
                    200,
                    {
                        "model_path": "C:/Models/fixture-model.gguf",
                        "build_info": build_info,
                        "default_generation_settings": {"n_ctx": 4096},
                        "total_slots": 2,
                    },
                )
                return
            if method == "GET" and path == "/metrics":
                if self.scenario == "metrics_disabled":
                    await self._json(writer, 501, {"error": "metrics disabled"})
                else:
                    await self._text(
                        writer,
                        200,
                        "# TYPE llamacpp:prompt_tokens_total counter\n"
                        "llamacpp:prompt_tokens_total 12\n"
                        "# TYPE llamacpp:spec_decode_num_accepted_tokens_per_pos_total counter\n"
                        'llamacpp:spec_decode_num_accepted_tokens_per_pos_total{position="0"} 3\n'
                        'llamacpp:spec_decode_num_accepted_tokens_per_pos_total{position="1"} 5\n'
                        "llamacpp:undocumented_metric 200\n",
                        content_type="text/plain; version=0.0.4",
                    )
                return
            if method == "GET" and path == "/v1/models":
                if self.scenario == "no_model_list":
                    await self._json(writer, 404, {"error": "models endpoint unavailable"})
                    return
                await self._json(
                    writer,
                    200,
                    {"data": [{"id": "fixture-model", "object": "model"}]},
                )
                return
            if method == "POST" and path == "/v1/chat/completions":
                if (payload or {}).get("stream"):
                    await self._stream(writer)
                else:
                    await self._json(writer, 200, self._completion())
                return
            await self._json(writer, 404, {"error": "not found"})
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    @staticmethod
    def _completion() -> dict[str, Any]:
        return {
            "id": "llama-fixture",
            "object": "chat.completion",
            "model": "fixture-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture reply"}}],
            "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
        }

    @staticmethod
    async def _headers(
        writer: asyncio.StreamWriter,
        status: int,
        content_type: str,
        body: bytes,
    ) -> None:
        reason = {200: "OK", 404: "Not Found", 501: "Not Implemented"}.get(status, "Error")
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(headers + body)
        await writer.drain()

    @classmethod
    async def _json(
        cls,
        writer: asyncio.StreamWriter,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        await cls._headers(writer, status, "application/json", json.dumps(payload).encode())

    @classmethod
    async def _text(
        cls,
        writer: asyncio.StreamWriter,
        status: int,
        payload: str,
        *,
        content_type: str,
    ) -> None:
        await cls._headers(writer, status, content_type, payload.encode())

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: close\r\n\r\n"
        )
        await writer.drain()
        events = (
            {"choices": [{"index": 0, "delta": {"content": "fixture "}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": "reply"}, "finish_reason": None}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
            },
        )
        for payload in events:
            writer.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
            await writer.drain()
        writer.write(b"data: [DONE]\n\n")
        await writer.drain()


def _attach_config(
    endpoint_url: str,
    *,
    extension: dict[str, Any] | None = None,
) -> AdapterConfiguration:
    values: dict[str, Any] = {"endpoint_url": endpoint_url}
    if extension:
        values["engine_extensions"] = {"llama_cpp": extension}
    return AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values=values,
    )


def _managed_config(
    temp_path: Path,
    *,
    port: int = 9931,
    executable_exists: bool = True,
) -> AdapterConfiguration:
    executable = temp_path / "llama-server.exe"
    model = temp_path / "fixture-model.gguf"
    temp_path.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"fixture; never loaded by a real binary")
    if executable_exists:
        executable.write_bytes(b"test fixture only")
        if os.name != "nt":
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return AdapterConfiguration(
        integration_mode=EngineIntegrationMode.MANAGED_PROCESS,
        values={
            "endpoint_url": f"http://127.0.0.1:{port}",
            "engine_extensions": {
                "llama_cpp": {
                    "executable_path": str(executable),
                    "model_path": str(model),
                    "working_directory": str(temp_path),
                    "startup_timeout_seconds": 0.1,
                    "context_size": 4096,
                    "parallelism": 2,
                    "batch_size": 2048,
                    "ubatch_size": 512,
                    "gpu_layers": "all",
                    "model_id": "fixture alias",
                }
            },
        },
    )


class FakeProcess:
    def __init__(self, pid: int = 24576) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def test_attach_contract_probes_versions_streams_usage_and_reads_native_metrics(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        server = LocalLlamaServer()
        await server.start()
        adapter = LlamaCppEngineAdapter()
        catalog = AdapterCatalog()
        catalog.register(adapter)
        launch_config = _managed_config(tmp_path)
        fake_process = FakeProcess()
        captured: dict[str, Any] = {}

        async def spawn(arguments: list[str], working_directory: Path) -> FakeProcess:
            captured["arguments"] = arguments
            captured["working_directory"] = working_directory
            return fake_process

        adapter._spawn = spawn  # type: ignore[method-assign]
        adapter._port_available = lambda _port: True  # type: ignore[method-assign]

        async def ready(_process: Any, _port: int, _timeout: float) -> bool:
            return True

        adapter._wait_until_ready = ready  # type: ignore[method-assign]
        try:
            await assert_registered_adapter_contract(
                catalog,
                LLAMA_CPP_ADAPTER_ID,
                _attach_config(server.endpoint_url),
                managed_config=launch_config,
            )
            probe = await catalog.probe(
                LLAMA_CPP_ADAPTER_ID,
                _attach_config(server.endpoint_url),
            )
            assert probe.status is ProbeStatus.SUCCEEDED
            assert (
                probe.engine_version == f"{LLAMA_CPP_TESTED_VERSION}-{LLAMA_CPP_TESTED_COMMIT[:7]}"
            )
            assert probe.checks["tested_server_version"].state is CapabilitySupport.SUPPORTED
            assert probe.checks["llama_health"].state is CapabilitySupport.SUPPORTED
            metrics = await catalog.read_metrics(
                LLAMA_CPP_ADAPTER_ID,
                _attach_config(server.endpoint_url),
            )
            assert metrics.available
            assert any(
                metric.metric_name == "llamacpp:prompt_tokens_total"
                and metric.value == 12
                and metric.unit == "tokens"
                and metric.source is MetricSource.ENGINE_NATIVE
                for metric in metrics.metrics
            )
            position_series = [
                metric
                for metric in metrics.metrics
                if metric.metric_name == "llamacpp:spec_decode_num_accepted_tokens_per_pos_total"
            ]
            assert {metric.labels["position"] for metric in position_series} == {"0", "1"}
            assert "llamacpp:undocumented_metric" not in {
                metric.metric_name for metric in metrics.metrics
            }
            assert captured["working_directory"] == Path(
                launch_config.values["engine_extensions"]["llama_cpp"]["working_directory"]
            )
            assert captured["arguments"][0].endswith("llama-server.exe")
            assert captured["arguments"][-2:] == ["--alias", "fixture alias"]
            assert (
                str(Path(launch_config.values["engine_extensions"]["llama_cpp"]["model_path"]))
                in captured["arguments"]
            )
            assert "--ctx-size" in captured["arguments"]
            assert "--metrics" in captured["arguments"]
            assert fake_process.terminated
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_streaming_usage_and_labels_are_preserved_through_the_catalog() -> None:
    async def run() -> None:
        server = LocalLlamaServer()
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(LlamaCppEngineAdapter())
        config = _attach_config(server.endpoint_url)
        try:
            prepared = await catalog.prepare_request(
                LLAMA_CPP_ADAPTER_ID,
                config,
                GenerationRequest(model_ref="fixture-model", prompt="private prompt"),
            )
            events = [
                event async for event in catalog.stream_request(LLAMA_CPP_ADAPTER_ID, prepared)
            ]
            assert "".join(event.text or "" for event in events) == "fixture reply"
            assert any(
                event.event_type is StreamEventType.USAGE
                and event.usage is not None
                and event.usage.input_tokens == 6
                and event.usage.output_tokens == 2
                for event in events
            )
            assert all("private prompt" not in str(event.model_dump()) for event in events)
            post = next(request for request in server.requests if request["method"] == "POST")
            assert post["path"] == "/v1/chat/completions"
            assert post["body"]["stream"] is True
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_model_identity_falls_back_to_read_only_server_properties() -> None:
    async def run() -> None:
        server = LocalLlamaServer("no_model_list")
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(LlamaCppEngineAdapter())
        try:
            models = await catalog.list_models(
                LLAMA_CPP_ADAPTER_ID,
                _attach_config(server.endpoint_url),
            )
            assert len(models) == 1
            assert models[0].model_ref == "C:/Models/fixture-model.gguf"
            assert models[0].display_name == "fixture-model.gguf"
            assert models[0].metadata["context_size"] == 4096
            assert models[0].metadata["parallelism"] == 2
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_bad_server_version_and_probe_timeout_are_reported_independently() -> None:
    async def run() -> None:
        server = LocalLlamaServer("bad_version")
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(LlamaCppEngineAdapter())
        try:
            result = await catalog.probe(LLAMA_CPP_ADAPTER_ID, _attach_config(server.endpoint_url))
            assert result.status is ProbeStatus.SUCCEEDED
            assert result.engine_version == "b99999-0000000"
            assert result.checks["tested_server_version"].state is CapabilitySupport.UNSUPPORTED
            assert any(item.code == "llama_tested_version_mismatch" for item in result.diagnostics)
        finally:
            await catalog.close()
            await server.close()

        slow_server = LocalLlamaServer("props_timeout")
        await slow_server.start()
        catalog = AdapterCatalog()
        catalog.register(LlamaCppEngineAdapter())
        try:
            result = await catalog.probe(
                LLAMA_CPP_ADAPTER_ID,
                _attach_config(slow_server.endpoint_url, extension={"timeout_seconds": 0.1}),
            )
            assert result.status is ProbeStatus.SUCCEEDED
            assert any(item.code == "request_timeout" for item in result.diagnostics)
            assert result.checks["server_properties"].state is CapabilitySupport.UNKNOWN
        finally:
            await catalog.close()
            await slow_server.close()

    asyncio.run(run())


def test_disabled_metrics_and_prometheus_type_mismatch_are_not_normalized() -> None:
    async def run() -> None:
        server = LocalLlamaServer("metrics_disabled")
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(LlamaCppEngineAdapter())
        try:
            metrics = await catalog.read_metrics(
                LLAMA_CPP_ADAPTER_ID,
                _attach_config(server.endpoint_url),
            )
            assert not metrics.available
            assert metrics.diagnostic is not None
            assert metrics.diagnostic.code == "llama_metrics_disabled"
        finally:
            await catalog.close()
            await server.close()

    metrics, rejected = _metrics_from_prometheus(
        "# TYPE llamacpp:prompt_tokens_total gauge\n"
        "llamacpp:prompt_tokens_total 12\n"
        "llamacpp:unknown_metric 90\n"
    )
    assert metrics == []
    assert rejected == 1
    asyncio.run(run())


def test_prometheus_parser_decodes_escaped_labels_and_skips_malformed_series() -> None:
    metrics, rejected = _metrics_from_prometheus(
        r'llamacpp:prompt_tokens_total{note="line\n\"quoted\"\\tail"} 1' + "\n"
        r'llamacpp:prompt_tokens_total{note="a",note="b"} 2'
    )

    assert len(metrics) == 1
    assert metrics[0].labels == {"note": 'line\n"quoted"\\tail'}
    assert rejected == 1


def test_managed_config_requires_local_http_existing_absolute_paths_and_gguf(
    tmp_path: Path,
) -> None:
    adapter = LlamaCppEngineAdapter()
    valid = _managed_config(tmp_path)
    assert asyncio.run(adapter.validate_config(valid)).valid

    missing_executable = _managed_config(
        tmp_path / "missing",
        executable_exists=False,
    )
    invalid_missing = asyncio.run(adapter.validate_config(missing_executable))
    assert not invalid_missing.valid
    assert any(item.code == "executable_path_invalid" for item in invalid_missing.diagnostics)

    extensions = valid.values["engine_extensions"]["llama_cpp"]
    bad_scheme = AdapterConfiguration(
        integration_mode=EngineIntegrationMode.MANAGED_PROCESS,
        values={**valid.values, "endpoint_url": "https://127.0.0.1:9931"},
    )
    invalid_scheme = asyncio.run(adapter.validate_config(bad_scheme))
    assert not invalid_scheme.valid
    assert any(
        item.code == "managed_endpoint_scheme_unsupported" for item in invalid_scheme.diagnostics
    )

    bad_host = AdapterConfiguration(
        integration_mode=EngineIntegrationMode.MANAGED_PROCESS,
        values={**valid.values, "endpoint_url": "http://localhost:9931"},
    )
    invalid_host = asyncio.run(adapter.validate_config(bad_host))
    assert not invalid_host.valid
    assert any(item.code == "managed_bind_must_be_loopback" for item in invalid_host.diagnostics)
    assert extensions["model_path"].endswith(".gguf")


def test_managed_start_refuses_a_busy_port_without_launching(tmp_path: Path) -> None:
    async def run() -> None:
        server = await asyncio.start_server(lambda _reader, _writer: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        adapter = LlamaCppEngineAdapter()
        launched = False

        async def spawn(_arguments: list[str], _working_directory: Path) -> FakeProcess:
            nonlocal launched
            launched = True
            return FakeProcess()

        adapter._spawn = spawn  # type: ignore[method-assign]
        try:
            with pytest.raises(OSError, match="already in use"):
                await adapter.start(_managed_config(tmp_path, port=port))
            assert not launched
        finally:
            await adapter.close()
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_managed_start_uses_owned_pid_and_only_accepts_its_process_handle(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        adapter = LlamaCppEngineAdapter()
        fake_process = FakeProcess()
        captured: dict[str, Any] = {}

        async def spawn(arguments: list[str], working_directory: Path) -> FakeProcess:
            captured["arguments"] = arguments
            captured["working_directory"] = working_directory
            return fake_process

        async def ready(_process: Any, _port: int, _timeout: float) -> bool:
            return True

        adapter._spawn = spawn  # type: ignore[method-assign]
        adapter._port_available = lambda _port: True  # type: ignore[method-assign]
        adapter._wait_until_ready = ready  # type: ignore[method-assign]
        try:
            config = _managed_config(tmp_path)
            model_path = tmp_path / "model with spaces;literal.gguf"
            model_path.write_bytes(b"fixture only")
            config.values["engine_extensions"]["llama_cpp"]["model_path"] = str(model_path)
            handle = await adapter.start(config)
            assert captured["working_directory"] == tmp_path
            assert captured["arguments"][0].endswith("llama-server.exe")
            assert captured["arguments"][1:3] == ["--model", str(model_path)]
            assert captured["arguments"][captured["arguments"].index("--host") + 1] == "127.0.0.1"
            for option, value in (
                ("--port", "9931"),
                ("--ctx-size", "4096"),
                ("--parallel", "2"),
                ("--batch-size", "2048"),
                ("--ubatch-size", "512"),
                ("--gpu-layers", "all"),
            ):
                assert captured["arguments"][captured["arguments"].index(option) + 1] == value
            assert str(model_path) in captured["arguments"]
            assert captured["arguments"][-2:] == ["--alias", "fixture alias"]
            forged = AdapterProcessHandle(
                adapter_id=LLAMA_CPP_ADAPTER_ID,
                process_id=handle.process_id,
                ownership_token=uuid4(),
            )
            with pytest.raises(RuntimeError, match="not recorded as owned"):
                await adapter.stop(forged)
            assert fake_process.returncode is None
            await adapter.stop(handle)
            assert fake_process.terminated
            assert fake_process.pid == 24576
            with pytest.raises(RuntimeError, match="not recorded as owned"):
                await adapter.stop(handle)
        finally:
            await adapter.close()

    asyncio.run(run())


def test_managed_start_terminates_owned_process_after_startup_timeout(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        adapter = LlamaCppEngineAdapter()
        fake_process = FakeProcess()

        async def spawn(_arguments: list[str], _working_directory: Path) -> FakeProcess:
            return fake_process

        async def not_ready(_process: Any, _port: int, _timeout: float) -> bool:
            return False

        adapter._spawn = spawn  # type: ignore[method-assign]
        adapter._port_available = lambda _port: True  # type: ignore[method-assign]
        adapter._wait_until_ready = not_ready  # type: ignore[method-assign]
        try:
            with pytest.raises(TimeoutError, match="did not become healthy"):
                await adapter.start(_managed_config(tmp_path))
            assert fake_process.terminated
            assert not adapter._owned_processes
        finally:
            await adapter.close()

    asyncio.run(run())
