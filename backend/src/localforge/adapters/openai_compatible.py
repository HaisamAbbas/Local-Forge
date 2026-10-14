"""Generic attached adapter for OpenAI-compatible chat-completion APIs.

The adapter uses only the portable Chat Completions request and response shape. It deliberately
does not infer a vendor, engine version, model family, or server implementation from the endpoint.
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import UUID

import httpx2

from localforge.adapters.contracts import (
    AbortResult,
    AdapterCapabilities,
    AdapterCategory,
    AdapterConfiguration,
    AdapterDiagnostic,
    AdapterManifest,
    AdapterModel,
    AdapterPlatform,
    AdapterProcessHandle,
    AdapterStreamEvent,
    ConfigValidationResult,
    DiagnosticSeverity,
    GenerationRequest,
    MetricsResult,
    PreparedRequest,
    ProbeResult,
    ProbeStatus,
    RuntimeFingerprint,
    StreamEventType,
    UsageCounts,
    capability,
)
from localforge.core.models import (
    CapabilitySupport,
    CapabilityValue,
    EngineIntegrationMode,
    JsonValue,
)
from localforge.core.urls import parse_engine_endpoint

OPENAI_COMPATIBLE_ADAPTER_ID = "localforge.openai_compatible"
OPENAI_COMPATIBLE_ADAPTER_VERSION = "1.0.0"
_EXTENSION_NAMESPACE = "openai_compatible"
_EXTENSION_KEYS = {
    "model_id",
    "timeout_seconds",
    "max_retries",
    "retry_backoff_seconds",
    "streaming_mode",
    "include_usage",
    "metrics_route",
    "request_extensions",
}
_CHAT_PARAMETER_KEYS = {
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "stop",
}
_STREAM_UNSUPPORTED_TEXT = (
    "streaming is not supported",
    "stream is not supported",
    "streaming unsupported",
    "unsupported stream",
    "unsupported value for stream",
)
_MAX_MODEL_LIST_BYTES = 1_048_576
_MAX_COMPLETION_BYTES = 16_777_216
_MAX_SSE_EVENT_CHARS = 1_048_576


class AdapterOperationUnsupported(RuntimeError):
    """Raised when a compatible endpoint does not implement an optional operation."""


class CredentialUnavailableError(RuntimeError):
    """Raised without exposing either a credential value or its reference."""


@dataclass
class _RequestContext:
    config: AdapterConfiguration
    parameters: dict[str, JsonValue]


CredentialResolver = Callable[[str], str | None]


def _read_os_credential(reference: str) -> str | None:
    """Resolve an opaque reference from an installed OS keyring backend."""

    parsed = urlsplit(reference)
    if parsed.scheme == "keyring" and parsed.netloc and parsed.path.strip("/"):
        service = unquote(parsed.netloc)
        username = unquote(parsed.path.strip("/"))
    elif parsed.scheme == "localforge-credential":
        service = "LocalForge"
        username = unquote((parsed.netloc + parsed.path).strip("/"))
    else:
        return None
    if not service or not username or parsed.query or parsed.fragment:
        return None

    try:
        import keyring
    except ImportError:
        return None

    backend = keyring.get_keyring()
    backend_module = backend.__class__.__module__.casefold()
    safe_backend_markers = ("windows", "macos", "secretservice", "kwallet", "keychain")
    if not any(marker in backend_module for marker in safe_backend_markers):
        return None
    return keyring.get_password(service, username)


def _extension(config: AdapterConfiguration) -> Mapping[str, JsonValue]:
    namespaces = config.values.get("engine_extensions")
    if not isinstance(namespaces, dict):
        return {}
    values = namespaces.get(_EXTENSION_NAMESPACE)
    return values if isinstance(values, dict) else {}


def _setting(
    config: AdapterConfiguration,
    key: str,
    default: JsonValue,
) -> JsonValue:
    return _extension(config).get(key, default)


def _base_url(config: AdapterConfiguration) -> str:
    value = config.values.get("endpoint_url")
    return value.strip().rstrip("/") if isinstance(value, str) else ""


def _route_url(base_url: str, route: str) -> str:
    """Resolve a safe absolute path or a path relative to the configured API base."""

    parts = urlsplit(base_url)
    if route.startswith("/"):
        path = route
    else:
        path = f"{parts.path.rstrip('/')}/{route}"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _safe_route(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 500:
        return False
    decoded = unquote(value)
    if any(character in decoded for character in "?#\\\r\n") or "://" in decoded:
        return False
    segments = decoded.replace("\\", "/").split("/")
    return all(segment not in {".", ".."} for segment in segments)


def _valid_credential_reference(reference: str) -> bool:
    if any(character.isspace() for character in reference):
        return False
    parsed = urlsplit(reference)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return False
    if parsed.scheme == "keyring":
        return bool(parsed.netloc and parsed.path.strip("/"))
    if parsed.scheme == "localforge-credential":
        return bool((parsed.netloc + parsed.path).strip("/"))
    return False


async def _read_bounded(response: httpx2.Response, limit: int) -> bytes | None:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            return None
    return bytes(body)


def _number(value: object, *, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= float(value) <= maximum
    )


def _bounded_float(value: object, default: float, *, minimum: float, maximum: float) -> float:
    if not _number(value, minimum=minimum, maximum=maximum):
        return default
    return float(cast(int | float, value))


_ABSOLUTE_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def model_display_name(model_ref: str) -> str:
    """Show a local file path model id by its file name; keep ids such as ``org/model`` intact.

    Servers like llama-server report the loaded file path as the model id. The id must still be
    sent on requests, but the full path is noise on screen and in exported reports.
    """

    if model_ref.startswith("/") or "\\" in model_ref or _ABSOLUTE_WINDOWS_PATH.match(model_ref):
        name = model_ref.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if name:
            return name[:240]
    return model_ref[:240]


def _models_from_payload(payload: object) -> list[AdapterModel] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    models: list[AdapterModel] = []
    for item in data[:1000]:
        if not isinstance(item, dict):
            continue
        model_ref = item.get("id")
        if not isinstance(model_ref, str) or not model_ref.strip() or len(model_ref) > 500:
            continue
        normalized = model_ref.strip()
        models.append(
            AdapterModel(
                model_ref=normalized,
                display_name=model_display_name(normalized),
                metadata={"source": "openai_compatible_models_endpoint"},
            )
        )
    return models


def _usage(payload: object) -> UsageCounts | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    if isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens < 0:
        input_tokens = None
    if isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or output_tokens < 0:
        output_tokens = None
    if input_tokens is None and output_tokens is None:
        return None
    return UsageCounts(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        exact=input_tokens is not None and output_tokens is not None,
    )


def _completion_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = [part.get("text") for part in content if isinstance(part, dict)]
        if all(isinstance(piece, str) for piece in pieces):
            return "".join(cast(list[str], pieces))
    return None


def _stream_delta(payload: object) -> tuple[str | None, UsageCounts | None, bool]:
    if not isinstance(payload, dict):
        return None, None, False
    usage = _usage(payload)
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return None, usage, usage is not None
    if not choices:
        return None, usage, usage is not None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None, usage, False
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return None, usage, False
    content = delta.get("content")
    if content is None:
        return None, usage, True
    if isinstance(content, str):
        return content, usage, True
    if isinstance(content, list):
        pieces = [part.get("text") for part in content if isinstance(part, dict)]
        if all(isinstance(piece, str) for piece in pieces):
            return "".join(cast(list[str], pieces)), usage, True
    return None, usage, False


def _http_error(response: httpx2.Response) -> tuple[str, str]:
    status = response.status_code
    if status in {401, 403}:
        return (
            "endpoint_unauthorized",
            "The endpoint rejected authorization. Check its credential reference.",
        )
    if status == 429:
        return (
            "endpoint_rate_limited",
            "The endpoint rate-limited this request. Retry after its limit resets.",
        )
    if status >= 500:
        return "endpoint_server_error", f"The endpoint returned HTTP {status}."
    return (
        "endpoint_request_rejected",
        f"The endpoint rejected the chat request with HTTP {status}.",
    )


async def _response_rejects_streaming(response: httpx2.Response) -> bool:
    if response.status_code not in {400, 404, 405, 406, 415, 422}:
        return False
    body = await _read_bounded(response, 65_536)
    if body is None:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return False
    text = json.dumps(payload, ensure_ascii=True).casefold()
    return any(marker in text for marker in _STREAM_UNSUPPORTED_TEXT)


def _diagnostic(
    code: str,
    message: str,
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO,
    *,
    retryable: bool = False,
) -> AdapterDiagnostic:
    return AdapterDiagnostic(code=code, message=message, severity=severity, retryable=retryable)


class OpenAICompatibleEngineAdapter:
    """OpenAI-compatible attach-mode adapter for the portable chat-completions API."""

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver = _read_os_credential,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._transport = transport
        self._client: httpx2.AsyncClient | None = None
        self._prepared: dict[UUID, _RequestContext] = {}
        self._active_responses: dict[UUID, httpx2.Response] = {}
        self._cancelled: set[UUID] = set()
        self._closed = False

    def describe(self) -> AdapterManifest:
        return AdapterManifest(
            protocol_version=1,
            adapter_id=OPENAI_COMPATIBLE_ADAPTER_ID,
            adapter_version=OPENAI_COMPATIBLE_ADAPTER_VERSION,
            display_name="OpenAI-compatible endpoint",
            description=(
                "Attach to a user-selected endpoint using the portable Chat Completions API. "
                "The underlying engine remains unknown unless the user names it."
            ),
            category=AdapterCategory.OPENAI_COMPATIBLE,
            integration_modes=[EngineIntegrationMode.ATTACHED_ENDPOINT],
            operating_systems=list(AdapterPlatform),
            declared_capabilities={
                "chat_completion",
                "streaming",
                "cancellation",
                "usage_counts",
                "health",
                "model_inventory",
            },
            supported_transports=["http", "https", "server_sent_events"],
            native_metrics=[],
            config_schema={
                "type": "object",
                "properties": {
                    "endpoint_url": {"type": "string", "format": "uri"},
                    "engine_extensions": {
                        "type": "object",
                        "properties": {
                            _EXTENSION_NAMESPACE: {
                                "type": "object",
                                "properties": {
                                    "model_id": {"type": "string"},
                                    "timeout_seconds": {"type": "number", "minimum": 0.1},
                                    "max_retries": {"type": "integer", "minimum": 0},
                                    "streaming_mode": {"enum": ["auto", "required", "disabled"]},
                                    "metrics_route": {"type": "string"},
                                },
                            }
                        },
                    },
                },
            },
            known_limitations=[
                "Only the Chat Completions text path is normalized; "
                "tools and multimodal inputs are not.",
                "The generic adapter does not infer a vendor, runtime, or engine version.",
                "No vendor-specific metrics are normalized by this adapter.",
                "Automatic retries are limited to connection failures before an HTTP response.",
            ],
            documentation_url=(
                "https://developers.openai.com/api/reference/resources/chat/"
                "subresources/completions/methods/create"
            ),
        )

    async def start(self, config: AdapterConfiguration) -> AdapterProcessHandle:
        del config
        raise AdapterOperationUnsupported(
            "The generic OpenAI-compatible adapter only attaches to an already running endpoint."
        )

    async def stop(self, process: AdapterProcessHandle) -> None:
        del process
        raise AdapterOperationUnsupported(
            "The generic OpenAI-compatible adapter does not own endpoint processes."
        )

    async def _get_client(self) -> httpx2.AsyncClient:
        if self._closed:
            raise RuntimeError("OpenAI-compatible adapter is closed.")
        if self._client is None:
            self._client = httpx2.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            )
        return self._client

    async def _authorization_headers(self, config: AdapterConfiguration) -> dict[str, str]:
        reference = config.secret_refs.get("api_key")
        if reference is None:
            return {"Accept": "application/json"}
        try:
            token = await asyncio.to_thread(self._credential_resolver, reference)
        except Exception as error:
            raise CredentialUnavailableError(
                "The configured endpoint credential could not be read from the OS credential store."
            ) from error
        if not token:
            raise CredentialUnavailableError(
                "The configured endpoint credential is unavailable in the OS credential store."
            )
        return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    def _timeout(self, config: AdapterConfiguration) -> httpx2.Timeout:
        seconds = _setting(config, "timeout_seconds", 30)
        timeout = _bounded_float(seconds, 30.0, minimum=0.1, maximum=300)
        return httpx2.Timeout(timeout)

    def _models_route(self, config: AdapterConfiguration) -> str:
        value = config.values.get("models_route")
        return value if isinstance(value, str) and value else "models"

    def _health_route(self, config: AdapterConfiguration) -> str | None:
        value = config.values.get("health_route")
        return value if isinstance(value, str) and value else None

    def _metrics_route(self, config: AdapterConfiguration) -> str | None:
        value = _setting(config, "metrics_route", None)
        return value if isinstance(value, str) and value else None

    def _configured_model(self, config: AdapterConfiguration) -> str | None:
        value = _setting(config, "model_id", None)
        return value.strip() if isinstance(value, str) and value.strip() else None

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        diagnostics: list[AdapterDiagnostic] = []
        if config.integration_mode is not EngineIntegrationMode.ATTACHED_ENDPOINT:
            diagnostics.append(
                _diagnostic(
                    "integration_mode_unsupported",
                    "This adapter supports attached endpoints only.",
                    DiagnosticSeverity.ERROR,
                )
            )
        base_url = _base_url(config)
        try:
            parse_engine_endpoint(base_url)
        except ValueError:
            diagnostics.append(
                _diagnostic(
                    "endpoint_url_invalid",
                    "Enter a valid HTTP loopback URL or HTTPS endpoint URL without credentials, "
                    "query, or fragment.",
                    DiagnosticSeverity.ERROR,
                )
            )

        extension = _extension(config)
        unknown = set(extension) - _EXTENSION_KEYS
        if unknown:
            diagnostics.append(
                _diagnostic(
                    "extension_fields_unsupported",
                    "The OpenAI-compatible settings contain an unsupported field.",
                    DiagnosticSeverity.ERROR,
                )
            )
        timeout = extension.get("timeout_seconds", 30)
        if not _number(timeout, minimum=0.1, maximum=300):
            diagnostics.append(
                _diagnostic(
                    "timeout_invalid",
                    "Request timeout must be between 0.1 and 300 seconds.",
                    DiagnosticSeverity.ERROR,
                )
            )
        retries = extension.get("max_retries", 0)
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 3:
            diagnostics.append(
                _diagnostic(
                    "retry_count_invalid",
                    "Connection retry count must be an integer from 0 to 3.",
                    DiagnosticSeverity.ERROR,
                )
            )
        backoff = extension.get("retry_backoff_seconds", 0.25)
        if not _number(backoff, minimum=0, maximum=2):
            diagnostics.append(
                _diagnostic(
                    "retry_backoff_invalid",
                    "Retry delay must be between 0 and 2 seconds.",
                    DiagnosticSeverity.ERROR,
                )
            )
        streaming_mode = extension.get("streaming_mode", "auto")
        if not isinstance(streaming_mode, str) or streaming_mode not in {
            "auto",
            "required",
            "disabled",
        }:
            diagnostics.append(
                _diagnostic(
                    "streaming_mode_invalid",
                    "Streaming mode must be auto, required, or disabled.",
                    DiagnosticSeverity.ERROR,
                )
            )
        include_usage = extension.get("include_usage", False)
        if not isinstance(include_usage, bool):
            diagnostics.append(
                _diagnostic(
                    "include_usage_invalid",
                    "Streaming usage option must be a boolean.",
                    DiagnosticSeverity.ERROR,
                )
            )
        request_extensions = extension.get("request_extensions", {})
        if not isinstance(request_extensions, dict):
            diagnostics.append(
                _diagnostic(
                    "request_extensions_invalid",
                    "Namespaced request extensions must be a JSON object.",
                    DiagnosticSeverity.ERROR,
                )
            )
        elif set(request_extensions).intersection(
            {"model", "messages", "stream", "authorization", "api_key"}
        ):
            diagnostics.append(
                _diagnostic(
                    "request_extensions_reserved",
                    "Request extensions cannot override model, messages, streaming, or "
                    "credentials.",
                    DiagnosticSeverity.ERROR,
                )
            )

        for route in (
            self._models_route(config),
            self._health_route(config),
            self._metrics_route(config),
        ):
            if route is not None and not _safe_route(route):
                diagnostics.append(
                    _diagnostic(
                        "route_invalid",
                        "Configured endpoint paths must be local paths without a host, query, "
                        "fragment, or traversal segments.",
                        DiagnosticSeverity.ERROR,
                    )
                )
                break
        if "api_key" in config.secret_refs:
            reference = config.secret_refs["api_key"]
            if not _valid_credential_reference(reference):
                diagnostics.append(
                    _diagnostic(
                        "credential_reference_invalid",
                        "Use a keyring://service/account or "
                        "localforge-credential://account reference.",
                        DiagnosticSeverity.ERROR,
                    )
                )
        unsupported_secret_keys = set(config.secret_refs) - {"api_key"}
        if unsupported_secret_keys:
            diagnostics.append(
                _diagnostic(
                    "credential_name_unsupported",
                    "This adapter accepts one API key credential reference named api_key.",
                    DiagnosticSeverity.ERROR,
                )
            )
        try:
            extension_model = self._configured_model(config)
            if extension_model is not None and len(extension_model) > 500:
                raise ValueError
        except (AttributeError, ValueError):
            diagnostics.append(
                _diagnostic(
                    "model_id_invalid",
                    "The configured model ID is invalid.",
                    DiagnosticSeverity.ERROR,
                )
            )
        return ConfigValidationResult(
            valid=not any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics),
            diagnostics=diagnostics,
        )

    async def _get_models(
        self,
        config: AdapterConfiguration,
        headers: dict[str, str],
    ) -> tuple[list[AdapterModel] | None, int | None]:
        client = await self._get_client()
        async with client.stream(
            "GET",
            _route_url(_base_url(config), self._models_route(config)),
            headers=headers,
            timeout=self._timeout(config),
        ) as response:
            if response.status_code != 200:
                return None, response.status_code
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdecimal():
                if int(content_length) > _MAX_MODEL_LIST_BYTES:
                    return None, response.status_code
            body = await _read_bounded(response, _MAX_MODEL_LIST_BYTES)
            if body is None:
                return None, response.status_code
            try:
                payload = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                return None, response.status_code
        return _models_from_payload(payload), response.status_code

    async def _health_check(
        self,
        config: AdapterConfiguration,
        headers: dict[str, str],
    ) -> tuple[CapabilitySupport, str | None, int | None]:
        route = self._health_route(config)
        if route is None:
            return CapabilitySupport.UNKNOWN, "No health route was configured.", None
        client = await self._get_client()
        async with client.stream(
            "GET",
            _route_url(_base_url(config), route),
            headers=headers,
            timeout=self._timeout(config),
        ) as response:
            if 200 <= response.status_code < 300:
                return (
                    CapabilitySupport.SUPPORTED,
                    "Configured health route returned a success status.",
                    response.status_code,
                )
            if response.status_code in {401, 403}:
                return (
                    CapabilitySupport.UNSUPPORTED,
                    "The health route rejected authorization.",
                    response.status_code,
                )
            return (
                CapabilitySupport.UNSUPPORTED,
                f"The configured health route returned HTTP {response.status_code}.",
                response.status_code,
            )

    async def _metrics_check(
        self,
        config: AdapterConfiguration,
        headers: dict[str, str],
    ) -> tuple[CapabilitySupport, str]:
        route = self._metrics_route(config)
        if route is None:
            return CapabilitySupport.UNSUPPORTED, "No optional metrics route was configured."
        client = await self._get_client()
        async with client.stream(
            "GET",
            _route_url(_base_url(config), route),
            headers=headers,
            timeout=self._timeout(config),
        ) as response:
            if 200 <= response.status_code < 300:
                return (
                    CapabilitySupport.UNKNOWN,
                    "The optional route responded, but generic metric names and units are not "
                    "normalized.",
                )
            return (
                CapabilitySupport.UNSUPPORTED,
                f"The optional metrics route returned HTTP {response.status_code}.",
            )

    async def _probe_streaming(
        self,
        config: AdapterConfiguration,
        model_ref: str,
        headers: dict[str, str],
    ) -> tuple[CapabilitySupport, str, int | None]:
        client = await self._get_client()
        extension = _extension(config)
        request_extensions = extension.get("request_extensions", {})
        payload: dict[str, JsonValue] = {
            "model": model_ref,
            "messages": [
                {"role": "user", "content": "LOCALFORGE connection check. Reply with one word."}
            ],
            "max_tokens": 1,
            "stream": True,
        }
        if isinstance(request_extensions, dict):
            payload.update(request_extensions)
        try:
            async with client.stream(
                "POST",
                _route_url(_base_url(config), "chat/completions"),
                headers=headers,
                json=payload,
                timeout=self._timeout(config),
            ) as response:
                if response.status_code in {401, 403}:
                    return (
                        CapabilitySupport.UNSUPPORTED,
                        "Authorization was rejected by the endpoint.",
                        response.status_code,
                    )
                if await _response_rejects_streaming(response):
                    return (
                        CapabilitySupport.UNSUPPORTED,
                        "The endpoint rejected the streaming request.",
                        None,
                    )
                if not 200 <= response.status_code < 300:
                    return (
                        CapabilitySupport.UNKNOWN,
                        f"The chat probe returned HTTP {response.status_code}.",
                        None,
                    )
                if "text/event-stream" not in response.headers.get("content-type", "").casefold():
                    return (
                        CapabilitySupport.UNSUPPORTED,
                        "The endpoint returned a non-streaming response.",
                        None,
                    )
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        data = "\n".join(data_lines)
                        if data == "[DONE]":
                            return (
                                CapabilitySupport.SUPPORTED,
                                "A valid streaming completion was observed.",
                                None,
                            )
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            return (
                                CapabilitySupport.UNKNOWN,
                                "The endpoint returned a malformed stream event.",
                                None,
                            )
                        _text, _usage_counts, valid = _stream_delta(chunk)
                        if valid:
                            return (
                                CapabilitySupport.SUPPORTED,
                                "A valid streaming completion was observed.",
                                None,
                            )
                        return (
                            CapabilitySupport.UNKNOWN,
                            "The endpoint returned an unsupported stream event shape.",
                            None,
                        )
                return (
                    CapabilitySupport.UNKNOWN,
                    "The stream ended before a valid completion event arrived.",
                    None,
                )
        except httpx2.TimeoutException:
            return CapabilitySupport.UNKNOWN, "The streaming probe timed out.", None
        except httpx2.RequestError:
            return CapabilitySupport.UNKNOWN, "The streaming probe could not be completed.", None

    async def probe(self, config: AdapterConfiguration) -> ProbeResult:
        validation = await self.validate_config(config)
        if not validation.valid:
            errors = [
                item for item in validation.diagnostics if item.severity is DiagnosticSeverity.ERROR
            ]
            message = errors[0].message if errors else "The adapter configuration is invalid."
            return self._probe_result(
                status=ProbeStatus.FAILED,
                reachable=CapabilitySupport.UNKNOWN,
                health=CapabilitySupport.UNKNOWN,
                models=[],
                checks={
                    "endpoint_reachability": capability(CapabilitySupport.UNKNOWN),
                    "health": capability(CapabilitySupport.UNKNOWN),
                    "model_inventory": capability(CapabilitySupport.UNKNOWN),
                    "streaming": capability(CapabilitySupport.UNKNOWN),
                    "native_metrics": capability(CapabilitySupport.UNSUPPORTED),
                },
                diagnostics=[
                    _diagnostic("configuration_invalid", message, DiagnosticSeverity.ERROR)
                ],
                capability_map={
                    "chat_completion": capability(CapabilitySupport.UNKNOWN),
                    "streaming": capability(CapabilitySupport.UNKNOWN),
                    "cancellation": capability(CapabilitySupport.SUPPORTED),
                    "usage_counts": capability(CapabilitySupport.UNKNOWN),
                    "health": capability(CapabilitySupport.UNKNOWN),
                    "model_inventory": capability(CapabilitySupport.UNKNOWN),
                },
            )

        diagnostics = list(validation.diagnostics)
        checks: dict[str, CapabilityValue] = {}
        models: list[AdapterModel] = []
        streaming_state = CapabilitySupport.UNKNOWN
        streaming_details = "A model ID is required before streaming can be probed."
        auth_status: int | None = None
        service_error = False
        reachable = CapabilitySupport.UNKNOWN
        health = CapabilitySupport.UNKNOWN
        model_inventory = CapabilitySupport.UNKNOWN
        metrics_state = CapabilitySupport.UNSUPPORTED
        metrics_details = "No optional metrics route was configured."
        headers: dict[str, str]
        try:
            headers = await self._authorization_headers(config)
            observed_models, models_status = await self._get_models(config, headers)
            if models_status in {401, 403}:
                auth_status = models_status
                reachable = CapabilitySupport.SUPPORTED
                model_inventory = CapabilitySupport.UNSUPPORTED
                diagnostics.append(
                    _diagnostic(
                        "endpoint_unauthorized",
                        "The endpoint responded but rejected authorization. "
                        "Check the saved API key reference.",
                        DiagnosticSeverity.ERROR,
                    )
                )
            elif models_status is not None and 200 <= models_status < 300:
                reachable = CapabilitySupport.SUPPORTED
                if observed_models is None:
                    model_inventory = CapabilitySupport.UNSUPPORTED
                    diagnostics.append(
                        _diagnostic(
                            "model_inventory_unavailable",
                            "The endpoint responded, but its models route did not return an "
                            "OpenAI-compatible model list.",
                            DiagnosticSeverity.WARNING,
                        )
                    )
                else:
                    models = observed_models
                    model_inventory = CapabilitySupport.SUPPORTED
            elif models_status in {404, 405, 501}:
                reachable = CapabilitySupport.SUPPORTED
                model_inventory = CapabilitySupport.UNSUPPORTED
                diagnostics.append(
                    _diagnostic(
                        "healthy_without_model_list",
                        "The endpoint is reachable, but it does not expose a compatible model "
                        "list.",
                        DiagnosticSeverity.WARNING,
                    )
                )
            elif models_status is not None and models_status >= 500:
                reachable = CapabilitySupport.SUPPORTED
                model_inventory = CapabilitySupport.UNSUPPORTED
                service_error = True
                diagnostics.append(
                    _diagnostic(
                        "endpoint_server_error",
                        f"The models route returned HTTP {models_status}.",
                        DiagnosticSeverity.ERROR,
                        retryable=True,
                    )
                )
            elif models_status is not None:
                reachable = CapabilitySupport.SUPPORTED
                model_inventory = CapabilitySupport.UNSUPPORTED
                diagnostics.append(
                    _diagnostic(
                        "model_inventory_unavailable",
                        f"The models route returned HTTP {models_status}.",
                        DiagnosticSeverity.WARNING,
                    )
                )
            else:
                reachable = CapabilitySupport.UNSUPPORTED

            try:
                health, health_details, health_status = await self._health_check(config, headers)
            except httpx2.TimeoutException:
                health_details, health_status = "The configured health route timed out.", None
            except httpx2.RequestError:
                health_details, health_status = (
                    "The configured health route could not be reached.",
                    None,
                )
            if health_status in {401, 403}:
                auth_status = health_status
            if health is CapabilitySupport.UNSUPPORTED and self._health_route(config):
                diagnostics.append(
                    _diagnostic(
                        "health_check_failed", cast(str, health_details), DiagnosticSeverity.WARNING
                    )
                )
            elif health is CapabilitySupport.UNKNOWN:
                diagnostics.append(_diagnostic("health_check_unknown", cast(str, health_details)))

            configured_model = self._configured_model(config)
            if not models and configured_model:
                models.append(
                    AdapterModel(
                        model_ref=configured_model,
                        display_name=model_display_name(configured_model),
                        metadata={"source": "user_supplied", "identity": "unknown/custom"},
                    )
                )
            selected_model = configured_model or (models[0].model_ref if models else None)
            if reachable is CapabilitySupport.SUPPORTED and selected_model and auth_status is None:
                (
                    streaming_state,
                    streaming_details,
                    stream_auth_status,
                ) = await self._probe_streaming(config, selected_model, headers)
                if stream_auth_status is not None:
                    auth_status = stream_auth_status
                    diagnostics.append(
                        _diagnostic(
                            "endpoint_unauthorized",
                            "The chat-completions route rejected authorization. Check the saved "
                            "API key reference.",
                            DiagnosticSeverity.ERROR,
                        )
                    )
            if streaming_state is CapabilitySupport.UNSUPPORTED:
                diagnostics.append(
                    _diagnostic(
                        "streaming_unsupported", streaming_details, DiagnosticSeverity.WARNING
                    )
                )
            elif streaming_state is CapabilitySupport.UNKNOWN:
                diagnostics.append(_diagnostic("streaming_unknown", streaming_details))

            try:
                metrics_state, metrics_details = await self._metrics_check(config, headers)
            except httpx2.TimeoutException:
                metrics_state, metrics_details = (
                    CapabilitySupport.UNKNOWN,
                    "The optional metrics route timed out.",
                )
            except httpx2.RequestError:
                metrics_state, metrics_details = (
                    CapabilitySupport.UNSUPPORTED,
                    "The optional metrics route could not be reached.",
                )
            if metrics_state is not CapabilitySupport.SUPPORTED:
                diagnostics.append(
                    _diagnostic(
                        "native_metrics_unavailable", metrics_details, DiagnosticSeverity.INFO
                    )
                )
            status = (
                ProbeStatus.SUCCEEDED
                if reachable is CapabilitySupport.SUPPORTED
                else ProbeStatus.FAILED
            )
            if service_error and health is not CapabilitySupport.SUPPORTED:
                status = ProbeStatus.FAILED
            if auth_status is not None:
                status = ProbeStatus.FAILED
            if status is ProbeStatus.FAILED and not any(
                item.severity is DiagnosticSeverity.ERROR for item in diagnostics
            ):
                diagnostics.append(
                    _diagnostic(
                        "endpoint_unreachable",
                        "The endpoint could not be reached. Check its URL and network "
                        "availability.",
                        DiagnosticSeverity.ERROR,
                        retryable=True,
                    )
                )
        except CredentialUnavailableError:
            reachable = CapabilitySupport.UNKNOWN
            health = CapabilitySupport.UNKNOWN
            model_inventory = CapabilitySupport.UNKNOWN
            status = ProbeStatus.FAILED
            diagnostics.append(
                _diagnostic(
                    "credential_unavailable",
                    "The endpoint credential reference could not be resolved from the OS "
                    "credential store.",
                    DiagnosticSeverity.ERROR,
                )
            )
        except httpx2.TimeoutException:
            reachable = CapabilitySupport.UNSUPPORTED
            health = CapabilitySupport.UNSUPPORTED
            model_inventory = CapabilitySupport.UNKNOWN
            status = ProbeStatus.FAILED
            diagnostics.append(
                _diagnostic(
                    "endpoint_timeout",
                    "The endpoint did not respond before the configured timeout.",
                    DiagnosticSeverity.ERROR,
                    retryable=True,
                )
            )
        except httpx2.RequestError:
            reachable = CapabilitySupport.UNSUPPORTED
            health = CapabilitySupport.UNSUPPORTED
            model_inventory = CapabilitySupport.UNKNOWN
            status = ProbeStatus.FAILED
            diagnostics.append(
                _diagnostic(
                    "endpoint_unreachable",
                    "The endpoint could not be reached. Check its URL and network availability.",
                    DiagnosticSeverity.ERROR,
                    retryable=True,
                )
            )

        checks = {
            "endpoint_reachability": capability(reachable, source="openai_compatible_probe"),
            "health": capability(health, source="openai_compatible_probe"),
            "model_inventory": capability(model_inventory, source="openai_compatible_probe"),
            "streaming": capability(
                streaming_state, details=streaming_details, source="openai_compatible_probe"
            ),
            "native_metrics": capability(
                metrics_state, details=metrics_details, source="openai_compatible_probe"
            ),
        }
        capability_map: dict[str, CapabilityValue] = {
            "chat_completion": capability(
                CapabilitySupport.SUPPORTED
                if streaming_state is CapabilitySupport.SUPPORTED
                else CapabilitySupport.UNKNOWN,
                details="Portable chat completions are used when the endpoint accepts the request.",
                source="openai_compatible_probe",
            ),
            "streaming": capability(
                streaming_state, details=streaming_details, source="openai_compatible_probe"
            ),
            "cancellation": capability(
                CapabilitySupport.SUPPORTED,
                details="Closing the client stream aborts its local request connection.",
                source="openai_compatible_adapter",
            ),
            "usage_counts": capability(
                CapabilitySupport.UNKNOWN,
                details="Usage is reported only when the endpoint includes token counts in its "
                "response.",
                source="openai_compatible_adapter",
            ),
            "health": capability(health, source="openai_compatible_probe"),
            "model_inventory": capability(model_inventory, source="openai_compatible_probe"),
        }
        return self._probe_result(
            status=status,
            reachable=reachable,
            health=health,
            models=models,
            checks=checks,
            diagnostics=diagnostics,
            capability_map=capability_map,
        )

    def _probe_result(
        self,
        *,
        status: ProbeStatus,
        reachable: CapabilitySupport,
        health: CapabilitySupport,
        models: list[AdapterModel],
        checks: dict[str, CapabilityValue],
        diagnostics: list[AdapterDiagnostic],
        capability_map: dict[str, CapabilityValue],
    ) -> ProbeResult:
        return ProbeResult(
            adapter_id=OPENAI_COMPATIBLE_ADAPTER_ID,
            adapter_version=OPENAI_COMPATIBLE_ADAPTER_VERSION,
            status=status,
            reachable=capability(reachable, source="openai_compatible_probe"),
            health=capability(health, source="openai_compatible_probe"),
            engine_version=None,
            models=models,
            checks=checks,
            capabilities=AdapterCapabilities(
                adapter_id=OPENAI_COMPATIBLE_ADAPTER_ID,
                adapter_version=OPENAI_COMPATIBLE_ADAPTER_VERSION,
                capabilities=capability_map,
            ),
            diagnostics=diagnostics,
            probed_at=datetime.now(UTC),
        )

    async def list_models(self, config: AdapterConfiguration) -> list[AdapterModel]:
        headers = await self._authorization_headers(config)
        try:
            models, status = await self._get_models(config, headers)
        except httpx2.TimeoutException as error:
            raise AdapterOperationUnsupported("The models route timed out.") from error
        except httpx2.RequestError as error:
            raise AdapterOperationUnsupported("The models route could not be reached.") from error
        if status in {401, 403}:
            raise AdapterOperationUnsupported("The endpoint rejected authorization.")
        if status != 200 or models is None:
            raise AdapterOperationUnsupported(
                "The endpoint does not expose a compatible models list."
            )
        return models

    async def prepare_request(
        self,
        config: AdapterConfiguration,
        request: GenerationRequest,
    ) -> PreparedRequest:
        validation = await self.validate_config(config)
        if not validation.valid:
            raise ValueError(validation.diagnostics[0].message)
        if not request.model_ref.strip():
            raise ValueError("A model ID is required for chat completion.")
        unsupported = set(request.parameters) - _CHAT_PARAMETER_KEYS
        if unsupported:
            raise ValueError(
                "The workload contains sampling parameters unsupported by chat completions."
            )
        extensions = _setting(config, "request_extensions", {})
        if not isinstance(extensions, dict):
            extensions = {}
        if set(extensions).intersection(
            {"model", "messages", "stream", "authorization", "api_key"}
        ):
            raise ValueError("The endpoint request extensions override a reserved field.")
        prepared = PreparedRequest(
            request_id=request.request_id,
            model_ref=request.model_ref,
            prompt=request.prompt,
            transport="openai_chat_completions",
            request_metadata={"parameters": request.parameters},
        )
        self._prepared[prepared.request_id] = _RequestContext(
            config=config,
            parameters=dict(request.parameters),
        )
        return prepared

    def stream_request(self, request: PreparedRequest) -> AsyncIterator[AdapterStreamEvent]:
        async def events() -> AsyncIterator[AdapterStreamEvent]:
            context = self._prepared.get(request.request_id)
            if context is None:
                yield AdapterStreamEvent(
                    request_id=request.request_id,
                    sequence=0,
                    event_type=StreamEventType.ERROR,
                    error_code="request_context_missing",
                    error_message="The prepared endpoint request is no longer available.",
                )
                return
            if request.request_id in self._cancelled:
                self._prepared.pop(request.request_id, None)
                self._cancelled.discard(request.request_id)
                yield AdapterStreamEvent(
                    request_id=request.request_id,
                    sequence=0,
                    event_type=StreamEventType.CANCELLED,
                    error_code="aborted",
                )
                return

            sequence = 0
            output_started = False

            def make_event(
                event_type: StreamEventType,
                *,
                text: str | None = None,
                usage: UsageCounts | None = None,
                error_code: str | None = None,
                error_message: str | None = None,
            ) -> AdapterStreamEvent:
                nonlocal sequence
                result = AdapterStreamEvent(
                    request_id=request.request_id,
                    sequence=sequence,
                    event_type=event_type,
                    text=text,
                    usage=usage,
                    error_code=error_code,
                    error_message=error_message,
                )
                sequence += 1
                return result

            def output_event(text: str) -> AdapterStreamEvent:
                nonlocal output_started
                event_type = (
                    StreamEventType.OUTPUT_CHUNK if output_started else StreamEventType.FIRST_OUTPUT
                )
                output_started = True
                return make_event(event_type, text=text)

            config = context.config
            mode = _setting(config, "streaming_mode", "auto")
            mode = mode if isinstance(mode, str) else "auto"
            max_retries = _setting(config, "max_retries", 0)
            max_retries = (
                max_retries
                if isinstance(max_retries, int) and not isinstance(max_retries, bool)
                else 0
            )
            retry_backoff = _setting(config, "retry_backoff_seconds", 0.25)
            retry_backoff = _bounded_float(
                retry_backoff,
                0.25,
                minimum=0,
                maximum=2,
            )
            extension = _extension(config)
            request_extensions = extension.get("request_extensions", {})
            include_usage = extension.get("include_usage", False) is True
            payload: dict[str, JsonValue] = {
                "model": request.model_ref,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": mode != "disabled",
            }
            payload.update(context.parameters)
            if isinstance(request_extensions, dict):
                payload.update(request_extensions)
            if mode != "disabled" and include_usage:
                payload["stream_options"] = {"include_usage": True}

            try:
                headers = await self._authorization_headers(config)
                client = await self._get_client()
                attempt = 0
                fallback_to_nonstream = mode == "disabled"
                while True:
                    payload["stream"] = not fallback_to_nonstream
                    if fallback_to_nonstream:
                        payload.pop("stream_options", None)
                    try:
                        async with client.stream(
                            "POST",
                            _route_url(_base_url(config), "chat/completions"),
                            headers=headers,
                            json=payload,
                            timeout=self._timeout(config),
                        ) as response:
                            self._active_responses[request.request_id] = response
                            if request.request_id in self._cancelled:
                                yield make_event(StreamEventType.CANCELLED, error_code="aborted")
                                return
                            if not 200 <= response.status_code < 300:
                                if (
                                    not fallback_to_nonstream
                                    and mode == "auto"
                                    and await _response_rejects_streaming(response)
                                ):
                                    fallback_to_nonstream = True
                                    continue
                                code, message = _http_error(response)
                                yield make_event(
                                    StreamEventType.ERROR,
                                    error_code=code,
                                    error_message=message,
                                )
                                return

                            content_type = response.headers.get("content-type", "").casefold()
                            if not fallback_to_nonstream and "text/event-stream" in content_type:
                                data_lines: list[str] = []
                                data_chars = 0
                                saw_done = False
                                async for line in response.aiter_lines():
                                    if request.request_id in self._cancelled:
                                        yield make_event(
                                            StreamEventType.CANCELLED, error_code="aborted"
                                        )
                                        return
                                    if line.startswith(":"):
                                        continue
                                    if line.startswith("data:"):
                                        data = line[5:].lstrip()
                                        data_chars += len(data)
                                        if data_chars > _MAX_SSE_EVENT_CHARS:
                                            yield make_event(
                                                StreamEventType.ERROR,
                                                error_code="stream_event_too_large",
                                                error_message=(
                                                    "The endpoint stream event exceeded the "
                                                    "adapter size limit."
                                                ),
                                            )
                                            return
                                        data_lines.append(data)
                                        continue
                                    if line or not data_lines:
                                        continue
                                    raw_event = "\n".join(data_lines)
                                    data_lines.clear()
                                    data_chars = 0
                                    if raw_event == "[DONE]":
                                        saw_done = True
                                        break
                                    try:
                                        chunk = json.loads(raw_event)
                                    except json.JSONDecodeError:
                                        yield make_event(
                                            StreamEventType.ERROR,
                                            error_code="malformed_stream_event",
                                            error_message=(
                                                "The endpoint returned a malformed streaming event."
                                            ),
                                        )
                                        return
                                    if isinstance(chunk, dict) and "error" in chunk:
                                        yield make_event(
                                            StreamEventType.ERROR,
                                            error_code="endpoint_stream_error",
                                            error_message="The endpoint reported an error in its "
                                            "stream.",
                                        )
                                        return
                                    text, usage, valid = _stream_delta(chunk)
                                    if not valid:
                                        yield make_event(
                                            StreamEventType.ERROR,
                                            error_code="malformed_stream_event",
                                            error_message=(
                                                "The endpoint returned an unsupported streaming "
                                                "event shape."
                                            ),
                                        )
                                        return
                                    if text:
                                        yield output_event(text)
                                    if usage is not None:
                                        yield make_event(StreamEventType.USAGE, usage=usage)
                                if request.request_id in self._cancelled:
                                    yield make_event(
                                        StreamEventType.CANCELLED, error_code="aborted"
                                    )
                                    return
                                if saw_done:
                                    yield make_event(StreamEventType.COMPLETED)
                                else:
                                    yield make_event(
                                        StreamEventType.DISCONNECTED,
                                        error_code="incomplete_stream",
                                        error_message=(
                                            "The endpoint closed the stream before its completion "
                                            "marker."
                                        ),
                                    )
                                return

                            if mode == "required" and not fallback_to_nonstream:
                                yield make_event(
                                    StreamEventType.ERROR,
                                    error_code="streaming_unsupported",
                                    error_message=(
                                        "The endpoint returned a non-streaming response, but "
                                        "streaming is required."
                                    ),
                                )
                                return

                            try:
                                raw_body = await _read_bounded(response, _MAX_COMPLETION_BYTES)
                                if raw_body is None:
                                    yield make_event(
                                        StreamEventType.ERROR,
                                        error_code="completion_response_too_large",
                                        error_message=(
                                            "The endpoint response exceeded the adapter size limit."
                                        ),
                                    )
                                    return
                                result = json.loads(raw_body)
                            except (ValueError, json.JSONDecodeError):
                                yield make_event(
                                    StreamEventType.ERROR,
                                    error_code="malformed_completion_response",
                                    error_message=(
                                        "The endpoint returned an invalid chat completion response."
                                    ),
                                )
                                return
                            text = _completion_text(result)
                            if text is None:
                                yield make_event(
                                    StreamEventType.ERROR,
                                    error_code="malformed_completion_response",
                                    error_message=(
                                        "The endpoint response did not contain chat completion "
                                        "text."
                                    ),
                                )
                                return
                            if text:
                                yield output_event(text)
                            usage = _usage(result)
                            if usage is not None:
                                yield make_event(StreamEventType.USAGE, usage=usage)
                            if request.request_id in self._cancelled:
                                yield make_event(StreamEventType.CANCELLED, error_code="aborted")
                                return
                            yield make_event(StreamEventType.COMPLETED)
                            return
                    except httpx2.ConnectTimeout:
                        if attempt < max_retries and not output_started:
                            attempt += 1
                            await asyncio.sleep(retry_backoff)
                            continue
                        yield make_event(
                            StreamEventType.TIMEOUT,
                            error_code="endpoint_connect_timeout",
                            error_message=(
                                "The endpoint connection timed out before a response arrived."
                            ),
                        )
                        return
                    except httpx2.TimeoutException:
                        if request.request_id in self._cancelled:
                            yield make_event(StreamEventType.CANCELLED, error_code="aborted")
                        else:
                            yield make_event(
                                StreamEventType.TIMEOUT,
                                error_code="endpoint_timeout",
                                error_message=(
                                    "The endpoint did not respond before the configured timeout."
                                ),
                            )
                        return
                    except httpx2.ConnectError:
                        if attempt < max_retries and not output_started:
                            attempt += 1
                            await asyncio.sleep(retry_backoff)
                            continue
                        yield make_event(
                            StreamEventType.DISCONNECTED,
                            error_code="endpoint_unreachable",
                            error_message=(
                                "The endpoint connection closed before a response arrived."
                            ),
                        )
                        return
                    except httpx2.RequestError:
                        if request.request_id in self._cancelled:
                            yield make_event(StreamEventType.CANCELLED, error_code="aborted")
                        else:
                            yield make_event(
                                StreamEventType.DISCONNECTED,
                                error_code="endpoint_disconnected",
                                error_message="The endpoint connection ended unexpectedly.",
                            )
                        return
                    finally:
                        self._active_responses.pop(request.request_id, None)
            except CredentialUnavailableError:
                yield make_event(
                    StreamEventType.ERROR,
                    error_code="credential_unavailable",
                    error_message=(
                        "The endpoint credential reference could not be resolved from the OS "
                        "credential store."
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                yield make_event(
                    StreamEventType.ERROR,
                    error_code="endpoint_request_failed",
                    error_message="The endpoint request could not be completed.",
                )
            finally:
                self._active_responses.pop(request.request_id, None)
                self._prepared.pop(request.request_id, None)
                self._cancelled.discard(request.request_id)

        return events()

    async def abort_request(self, request_id: UUID) -> AbortResult:
        if request_id not in self._prepared:
            return AbortResult(supported=True, accepted=False)
        self._cancelled.add(request_id)
        response = self._active_responses.get(request_id)
        if response is None:
            return AbortResult(supported=True, accepted=True)
        try:
            await response.aclose()
        except httpx2.HTTPError:
            return AbortResult(supported=True, accepted=True)
        return AbortResult(supported=True, accepted=True)

    async def read_metrics(self, config: AdapterConfiguration) -> MetricsResult:
        del config
        return MetricsResult(
            available=False,
            diagnostic=_diagnostic(
                "native_metrics_unavailable",
                "The generic adapter does not normalize vendor-specific metrics or units.",
            ),
        )

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        endpoint = urlsplit(_base_url(config))
        model_id = self._configured_model(config)
        settings: dict[str, JsonValue] = {
            "integration_mode": config.integration_mode.value,
            "endpoint_host": endpoint.hostname or "unknown",
            "endpoint_path": endpoint.path,
            "streaming_mode": _setting(config, "streaming_mode", "auto"),
            "timeout_seconds": _setting(config, "timeout_seconds", 30),
            "max_retries": _setting(config, "max_retries", 0),
            "credential_configured": "api_key" in config.secret_refs,
        }
        if model_id:
            settings["model_id"] = model_id
        return RuntimeFingerprint(
            adapter_id=OPENAI_COMPATIBLE_ADAPTER_ID,
            adapter_version=OPENAI_COMPATIBLE_ADAPTER_VERSION,
            engine_version=None,
            runtime_name="OpenAI-compatible API",
            runtime_version=None,
            settings={_EXTENSION_NAMESPACE: settings},
        )

    async def close(self) -> None:
        self._prepared.clear()
        self._cancelled.clear()
        self._active_responses.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._closed = True
