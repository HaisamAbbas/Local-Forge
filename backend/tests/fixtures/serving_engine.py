"""Local HTTP fixture for versioned OpenAI-compatible serving adapters."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit


@dataclass
class ServingEngineServer:
    engine: str
    version: str | None = None
    metrics: str = ""
    metrics_status: int = 200
    metrics_delay: float = 0
    health_status: int = 200
    chat_status: int = 200
    chat_delay: float = 0
    expected_api_key: str | None = None
    usage_probe_body: str | None = None
    selected_backend: str | None = None
    backend_status: int = 200
    requests: list[dict[str, Any]] = field(default_factory=list)
    server: asyncio.AbstractServer | None = None
    endpoint_url: str = ""

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        socket = self.server.sockets[0]
        self.endpoint_url = f"http://127.0.0.1:{socket.getsockname()[1]}"

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
            method, target, _version = lines[0].split(" ", 2)
            path = urlsplit(target).path
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
                await self._json(
                    writer,
                    401,
                    {"error": {"message": "auth rejected: fixture-secret-value"}},
                )
                return

            if method == "GET" and path == "/v1/models":
                await self._json(writer, 200, {"data": [{"id": "fixture-model"}]})
                return
            if method == "GET" and path in {"/health", "/ready"}:
                await self._json(writer, self.health_status, {"status": "ok"})
                return
            if method == "GET" and path == "/version":
                if self.version is None:
                    await self._json(writer, 404, {"error": {"message": "not found"}})
                else:
                    await self._json(writer, 200, {"version": self.version})
                return
            if method == "GET" and path == "/info":
                if self.version is None:
                    await self._json(writer, 404, {"error": {"message": "not found"}})
                else:
                    await self._json(
                        writer,
                        200,
                        {
                            "version": self.version,
                            "model_id": "fixture-model",
                            "model_sha": "fixture-sha",
                            "docker_label": "fixture-label",
                        },
                    )
                return
            if method == "GET" and path == "/api/extra/version":
                await self._json(
                    writer,
                    200,
                    {"result": "KoboldCpp", "version": self.version or "1.122.1"},
                )
                return
            if method == "GET" and path.startswith("/api/models/config-json/"):
                await self._json(writer, self.backend_status, {"backend": self.selected_backend})
                return
            if method == "GET" and path == "/model_info":
                await self._json(
                    writer,
                    200,
                    {
                        "served_model_name": "fixture-model",
                        "model_path": "C:\\private\\weights\\fixture-model",
                        "tokenizer_path": "C:\\private\\tokenizer",
                        "model_type": "llama",
                        "architectures": ["FixtureForCausalLM"],
                        "load_format": "safetensors",
                        "weight_version": "weights-17",
                        "api_key": "fixture-secret-value",
                        "admin_api_key": "fixture-admin-secret",
                    },
                )
                return
            if method == "GET" and path == "/metrics":
                if self.metrics_delay:
                    await asyncio.sleep(self.metrics_delay)
                if self.metrics_status != 200:
                    await self._json(writer, self.metrics_status, {"error": "metrics unavailable"})
                else:
                    await self._response(
                        writer,
                        200,
                        self.metrics.encode(),
                        "text/plain; version=0.0.4; charset=utf-8",
                    )
                return
            if method == "GET" and path in {"/server_info", "/get_server_info"}:
                await self._json(
                    writer,
                    200,
                    {
                        "version": self.version or "0.0.0",
                        "api_key": "fixture-secret-value",
                        "admin_api_key": "fixture-admin-secret",
                        "ssl_keyfile_password": "fixture-ssl-secret",
                    },
                )
                return
            if method == "POST" and path == "/v1/chat/completions":
                if self.chat_delay:
                    await asyncio.sleep(self.chat_delay)
                if self.chat_status != 200:
                    await self._json(
                        writer,
                        self.chat_status,
                        {"error": {"message": "fixture-secret-value should not escape"}},
                    )
                    return
                if (
                    self.usage_probe_body is not None
                    and (body or {}).get("messages", [{}])[0].get("content")
                    == "LOCALFORGE usage check. Reply OK."
                ):
                    await self._response(
                        writer,
                        200,
                        self.usage_probe_body.encode(),
                        "application/json",
                    )
                    return
                if (body or {}).get("stream") is True:
                    await self._stream(writer)
                else:
                    await self._json(
                        writer,
                        200,
                        {
                            "id": "fixture-completion",
                            "model": "fixture-model",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": "ready"},
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 4,
                                "completion_tokens": 1,
                                "total_tokens": 5,
                            },
                        },
                    )
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
    async def _json(
        writer: asyncio.StreamWriter,
        status: int,
        payload: dict[str, object],
    ) -> None:
        await ServingEngineServer._response(
            writer,
            status,
            json.dumps(payload).encode(),
            "application/json",
        )

    @staticmethod
    async def _response(
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        reason = {
            200: "OK",
            401: "Unauthorized",
            404: "Not Found",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }.get(status, "Error")
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(headers + body)
        await writer.drain()

    @staticmethod
    async def _stream(writer: asyncio.StreamWriter) -> None:
        events = [
            {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": "ready"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                },
            },
        ]
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: close\r\n\r\n"
        )
        await writer.drain()
        for event in events:
            writer.write(b"data: " + json.dumps(event).encode() + b"\n\n")
        writer.write(b"data: [DONE]\n\n")
        await writer.drain()
