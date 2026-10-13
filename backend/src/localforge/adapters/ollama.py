"""Attach-only adapter for a user's already-running local Ollama service."""

import asyncio
import json
import math
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlunsplit
from uuid import UUID

import httpx2

from localforge.adapters.contracts import (
    AbortResult,
    AdapterCapabilities,
    AdapterCategory,
    AdapterConfiguration,
    AdapterDiagnostic,
    AdapterManifest,
    AdapterMetric,
    AdapterModel,
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
from localforge.core.models import CapabilitySupport, EngineIntegrationMode, JsonValue, MetricSource
from localforge.core.urls import is_loopback_host, parse_engine_endpoint

OLLAMA_ADAPTER_ID = "localforge.ollama"
OLLAMA_ADAPTER_VERSION = "1.0.0"
_EXTENSION_NAMESPACE = "ollama"
_EXTENSION_KEYS = {"timeout_seconds", "streaming_mode"}
_REQUEST_PARAMETERS = {
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "stop",
    "stop_sequences",
    "max_tokens",
}
_DURATION_FIELDS = {
    "total_duration": "ollama.total_duration",
    "load_duration": "ollama.load_duration",
    "prompt_eval_duration": "ollama.prompt_eval_duration",
    "eval_duration": "ollama.eval_duration",
}
_MAX_JSON_BYTES = 2_097_152
_MAX_CHAT_BYTES = 16_777_216
_MAX_NDJSON_LINE_CHARS = 1_048_576
_INVENTORY_CACHE_SECONDS = 5.0


class AdapterOperationUnsupported(RuntimeError):
    """Raised when a server operation is unavailable in this attach-only adapter."""


def _diagnostic(
    code: str,
    message: str,
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO,
    *,
    retryable: bool = False,
) -> AdapterDiagnostic:
    return AdapterDiagnostic(code=code, message=message, severity=severity, retryable=retryable)


def _extension(config: AdapterConfiguration) -> Mapping[str, JsonValue]:
    namespaces = config.values.get("engine_extensions")
    if not isinstance(namespaces, dict):
        return {}
    values = namespaces.get(_EXTENSION_NAMESPACE)
    return values if isinstance(values, dict) else {}


def _endpoint(config: AdapterConfiguration) -> str:
    value = config.values.get("endpoint_url")
    return value.strip().rstrip("/") if isinstance(value, str) else ""


def _base_url(config: AdapterConfiguration) -> str:
    parsed = parse_engine_endpoint(_endpoint(config))
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _number(value: object, *, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _timeout(config: AdapterConfiguration) -> float:
    value = _extension(config).get("timeout_seconds", 30)
    return float(cast(int | float, value)) if _number(value, minimum=0.1, maximum=300) else 30.0


def _safe_text(value: object, maximum: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:maximum] if cleaned else None


def _positive_integer(value: object, *, maximum: int = 2**63 - 1) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    return None


def _model_inventory(payload: object) -> tuple[list[AdapterModel] | None, bool]:
    """Return local models and whether remote-backed entries were omitted."""

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return None, False
    models: list[AdapterModel] = []
    omitted_remote = False
    for item in payload["models"][:1000]:
        if not isinstance(item, dict):
            continue
        remote_fields = (item.get("remote_model"), item.get("remote_host"))
        if any(value is not None and value != "" for value in remote_fields):
            omitted_remote = True
            continue
        model_ref = _safe_text(item.get("name") or item.get("model"), 500)
        if model_ref is None:
            continue
        metadata: dict[str, JsonValue] = {
            "runner": "unknown",
            "runner_state": "unknown",
        }
        digest = _safe_text(item.get("digest"), 200)
        if digest is not None:
            metadata["digest"] = digest
        size = _positive_integer(item.get("size"))
        if size is not None:
            metadata["size_bytes"] = size
        details = item.get("details")
        if isinstance(details, dict):
            for key in ("format", "family", "parameter_size", "quantization_level"):
                value = _safe_text(details.get(key), 120)
                if value is not None:
                    metadata[key] = value
            families = details.get("families")
            if isinstance(families, list):
                metadata["families"] = [
                    family[:120]
                    for family in families[:32]
                    if isinstance(family, str) and family.strip()
                ]
        models.append(
            AdapterModel(
                model_ref=model_ref,
                display_name=model_ref[:240],
                revision=digest,
                metadata=metadata,
            )
        )
    return models, omitted_remote


def _usage(payload: Mapping[str, object]) -> UsageCounts | None:
    input_tokens = _positive_integer(payload.get("prompt_eval_count"))
    output_tokens = _positive_integer(payload.get("eval_count"))
    if input_tokens is None and output_tokens is None:
        return None
    return UsageCounts(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        exact=input_tokens is not None and output_tokens is not None,
    )


def _timing_metrics(payload: Mapping[str, object]) -> list[AdapterMetric]:
    observed_at = datetime.now(UTC)
    return [
        AdapterMetric(
            metric_name=metric_name,
            value=float(value),
            unit="ns",
            source=MetricSource.ENGINE_NATIVE,
            kind="gauge",
            observed_at=observed_at,
        )
        for field, metric_name in _DURATION_FIELDS.items()
        if (value := _positive_integer(payload.get(field))) is not None
    ]


def _chat_text(payload: Mapping[str, object]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


class OllamaEngineAdapter:
    """Local Ollama REST adapter; never manages a service or downloads/deletes models."""

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx2.AsyncClient | None = None
        self._prepared: dict[UUID, tuple[AdapterConfiguration, dict[str, JsonValue]]] = {}
        self._inventory_cache: dict[str, tuple[float, list[AdapterModel]]] = {}
        self._inventory_lock = asyncio.Lock()
        self._closed = False

    def describe(self) -> AdapterManifest:
        return AdapterManifest(
            protocol_version=1,
            adapter_id=OLLAMA_ADAPTER_ID,
            adapter_version=OLLAMA_ADAPTER_VERSION,
            display_name="Ollama",
            description=(
                "Attach to an existing local Ollama server and benchmark only a verified model "
                "already present on this machine. Ollama is the server and model-management "
                "layer; its underlying runner is recorded as unknown unless verified."
            ),
            category=AdapterCategory.NATIVE_API,
            integration_modes=[EngineIntegrationMode.ATTACHED_ENDPOINT],
            declared_capabilities={
                "attach_only",
                "health",
                "model_inventory",
                "version_discovery",
                "chat_completion",
                "streaming",
                "usage_counts",
                "server_reported_durations",
            },
            supported_transports=["http", "ollama_ndjson"],
            native_metrics=list(_DURATION_FIELDS.values()),
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
                                    "timeout_seconds": {
                                        "type": "number",
                                        "minimum": 0.1,
                                        "maximum": 300,
                                    },
                                    "streaming_mode": {"enum": ["auto", "required", "disabled"]},
                                },
                            }
                        },
                    },
                },
            },
            known_limitations=[
                "Attaches only to an already running Ollama service on this machine.",
                "LOCALFORGE never pulls or deletes models and never starts or stops Ollama.",
                "Remote-backed model entries are excluded; only verified local inventory can run.",
                "Ollama does not expose documented generation cancellation; closing the connection "
                "may not stop server-side generation.",
                "The Ollama server version is distinct from its underlying runner, which remains "
                "unknown unless Ollama reports verifiable runner details.",
            ],
            documentation_url="https://docs.ollama.com/api",
        )

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        diagnostics: list[AdapterDiagnostic] = []
        if config.integration_mode is not EngineIntegrationMode.ATTACHED_ENDPOINT:
            diagnostics.append(
                _diagnostic(
                    "integration_mode_unsupported",
                    "Ollama profiles attach to an already running local server.",
                    DiagnosticSeverity.ERROR,
                )
            )
        try:
            parsed = parse_engine_endpoint(_endpoint(config))
            if not is_loopback_host(parsed.hostname or "") or parsed.path not in {"", "/"}:
                raise ValueError
        except ValueError:
            diagnostics.append(
                _diagnostic(
                    "endpoint_must_be_local_root",
                    "Enter the HTTP or HTTPS root URL of an Ollama server on this machine.",
                    DiagnosticSeverity.ERROR,
                )
            )
        extension = _extension(config)
        if set(extension) - _EXTENSION_KEYS:
            diagnostics.append(
                _diagnostic(
                    "extension_fields_unsupported",
                    "The Ollama profile contains an unsupported setting.",
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
        mode = extension.get("streaming_mode", "auto")
        if not isinstance(mode, str) or mode not in {"auto", "required", "disabled"}:
            diagnostics.append(
                _diagnostic(
                    "streaming_mode_invalid",
                    "Choose automatic, required, or disabled streaming.",
                    DiagnosticSeverity.ERROR,
                )
            )
        if config.secret_refs:
            diagnostics.append(
                _diagnostic(
                    "credentials_unsupported",
                    "Local Ollama connections do not use saved credentials.",
                    DiagnosticSeverity.ERROR,
                )
            )
        errors = [item for item in diagnostics if item.severity is DiagnosticSeverity.ERROR]
        return ConfigValidationResult(valid=not errors, diagnostics=diagnostics)

    async def _get_client(self) -> httpx2.AsyncClient:
        if self._closed:
            raise RuntimeError("Ollama adapter is closed.")
        if self._client is None:
            self._client = httpx2.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            )
        return self._client

    async def _bounded_body(self, response: httpx2.Response, limit: int) -> bytes | None:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                return None
        return bytes(body)

    async def _get_json(
        self, config: AdapterConfiguration, path: str
    ) -> tuple[int | None, dict[str, object] | None, str | None]:
        try:
            client = await self._get_client()
            async with client.stream(
                "GET",
                f"{_base_url(config)}{path}",
                timeout=httpx2.Timeout(_timeout(config)),
            ) as response:
                status = response.status_code
                if not 200 <= status < 300:
                    return status, None, None
                body = await self._bounded_body(response, _MAX_JSON_BYTES)
                if body is None:
                    return status, None, "response_too_large"
                parsed = json.loads(body)
                if not isinstance(parsed, dict):
                    return status, None, "response_invalid"
                return status, cast(dict[str, object], parsed), None
        except httpx2.TimeoutException:
            return None, None, "request_timeout"
        except httpx2.RequestError:
            return None, None, "endpoint_unreachable"
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, None, "response_invalid"

    async def _inventory(
        self, config: AdapterConfiguration, *, refresh: bool = False
    ) -> tuple[list[AdapterModel] | None, bool, str | None]:
        endpoint = _base_url(config)
        async with self._inventory_lock:
            cached = self._inventory_cache.get(endpoint)
            if not refresh and cached is not None and cached[0] > asyncio.get_running_loop().time():
                return list(cached[1]), False, None
            status, payload, error = await self._get_json(config, "/api/tags")
            if status is None:
                return None, False, error or "endpoint_unreachable"
            if not 200 <= status < 300:
                return None, False, f"http_{status}"
            models, omitted_remote = _model_inventory(payload)
            if models is None:
                return None, False, error or "response_invalid"
            self._inventory_cache[endpoint] = (
                asyncio.get_running_loop().time() + _INVENTORY_CACHE_SECONDS,
                list(models),
            )
            return models, omitted_remote, None

    async def _version(self, config: AdapterConfiguration) -> tuple[str | None, str | None]:
        status, payload, error = await self._get_json(config, "/api/version")
        if status is None:
            return None, error
        if not 200 <= status < 300 or payload is None:
            return None, f"http_{status}"
        return _safe_text(payload.get("version"), 120), None

    async def probe(self, config: AdapterConfiguration) -> ProbeResult:
        validation = await self.validate_config(config)
        if not validation.valid:
            message = next(
                (
                    item.message
                    for item in validation.diagnostics
                    if item.severity is DiagnosticSeverity.ERROR
                ),
                "The Ollama profile configuration is invalid.",
            )
            return self._failed_probe(
                diagnostics=[
                    _diagnostic("configuration_invalid", message, DiagnosticSeverity.ERROR)
                ]
            )

        client = await self._get_client()
        diagnostics: list[AdapterDiagnostic] = []
        checks = {}
        health_ok = False
        try:
            response = await client.head(
                f"{_base_url(config)}/", timeout=httpx2.Timeout(_timeout(config))
            )
            health_ok = 200 <= response.status_code < 300
            reachable_state = CapabilitySupport.SUPPORTED
            health_state = (
                CapabilitySupport.SUPPORTED if health_ok else CapabilitySupport.UNSUPPORTED
            )
            health_details = (
                "Ollama HEAD / returned a successful health response."
                if health_ok
                else f"Ollama HEAD / returned HTTP {response.status_code}."
            )
        except httpx2.TimeoutException:
            reachable_state = CapabilitySupport.UNKNOWN
            health_state = CapabilitySupport.UNKNOWN
            health_details = "Ollama health could not be checked before the request timed out."
            diagnostics.append(
                _diagnostic(
                    "request_timeout", health_details, DiagnosticSeverity.WARNING, retryable=True
                )
            )
        except httpx2.RequestError:
            reachable_state = CapabilitySupport.UNSUPPORTED
            health_state = CapabilitySupport.UNKNOWN
            health_details = "The local Ollama endpoint could not be reached."
            diagnostics.append(
                _diagnostic(
                    "endpoint_unreachable",
                    health_details,
                    DiagnosticSeverity.WARNING,
                    retryable=True,
                )
            )

        checks["ollama_health"] = capability(
            health_state,
            details=health_details,
            source="ollama_head_root",
        )
        version, version_error = await self._version(config)
        checks["version_discovery"] = capability(
            CapabilitySupport.SUPPORTED if version else CapabilitySupport.UNKNOWN,
            details=(
                "Ollama reported its server version."
                if version
                else "The Ollama server version could not be verified."
            ),
            source="ollama_api_version",
        )
        if version is None:
            diagnostics.append(
                _diagnostic(
                    "version_unknown",
                    "The Ollama server version could not be verified.",
                    DiagnosticSeverity.WARNING,
                    retryable=version_error == "request_timeout",
                )
            )

        models, omitted_remote, inventory_error = await self._inventory(config, refresh=True)
        inventory_state = (
            CapabilitySupport.SUPPORTED if models is not None else CapabilitySupport.UNKNOWN
        )
        checks["model_inventory"] = capability(
            inventory_state,
            details=(
                f"Ollama listed {len(models)} local model(s)."
                if models is not None
                else "Ollama model inventory could not be read; health probing is independent."
            ),
            source="ollama_api_tags",
        )
        if models is None:
            diagnostics.append(
                _diagnostic(
                    "model_inventory_unavailable",
                    "Ollama health was checked, but its local model inventory is unavailable.",
                    DiagnosticSeverity.WARNING,
                    retryable=inventory_error == "request_timeout",
                )
            )
        if omitted_remote:
            diagnostics.append(
                _diagnostic(
                    "remote_models_omitted",
                    "Remote-backed Ollama entries were omitted. LOCALFORGE benchmarks only "
                    "verified local models.",
                    DiagnosticSeverity.WARNING,
                )
            )

        observed_capabilities = {
            "attach_only": capability(
                CapabilitySupport.SUPPORTED,
                details="This adapter only attaches to the local Ollama service.",
                source="ollama_adapter",
            ),
            "health": checks["ollama_health"],
            "model_inventory": checks["model_inventory"],
            "version_discovery": checks["version_discovery"],
            "chat_completion": capability(
                CapabilitySupport.UNKNOWN,
                details=(
                    "Chat completion is available through Ollama /api/chat; probe does not "
                    "generate text."
                ),
                source="ollama_api_contract",
            ),
            "streaming": capability(
                CapabilitySupport.UNKNOWN,
                details="Ollama /api/chat streaming is verified only when a request runs.",
                source="ollama_api_contract",
            ),
            "usage_counts": capability(
                CapabilitySupport.UNKNOWN,
                details="Token counts are recorded when Ollama returns them after generation.",
                source="ollama_api_chat_response",
            ),
            "server_reported_durations": capability(
                CapabilitySupport.UNKNOWN,
                details=(
                    "Server timing values are recorded when Ollama returns them after generation."
                ),
                source="ollama_api_chat_response",
            ),
        }
        return ProbeResult(
            adapter_id=OLLAMA_ADAPTER_ID,
            adapter_version=OLLAMA_ADAPTER_VERSION,
            status=ProbeStatus.SUCCEEDED
            if reachable_state is CapabilitySupport.SUPPORTED and health_ok
            else ProbeStatus.FAILED,
            reachable=capability(reachable_state, source="ollama_probe"),
            health=checks["ollama_health"],
            engine_version=version,
            models=models or [],
            checks=checks,
            capabilities=AdapterCapabilities(
                adapter_id=OLLAMA_ADAPTER_ID,
                adapter_version=OLLAMA_ADAPTER_VERSION,
                capabilities=observed_capabilities,
            ),
            diagnostics=diagnostics,
        )

    def _failed_probe(self, *, diagnostics: list[AdapterDiagnostic]) -> ProbeResult:
        return ProbeResult(
            adapter_id=OLLAMA_ADAPTER_ID,
            adapter_version=OLLAMA_ADAPTER_VERSION,
            status=ProbeStatus.FAILED,
            reachable=capability(CapabilitySupport.UNKNOWN, source="ollama_probe"),
            health=capability(CapabilitySupport.UNKNOWN, source="ollama_probe"),
            checks={"configuration": capability(CapabilitySupport.UNSUPPORTED)},
            capabilities=AdapterCapabilities(
                adapter_id=OLLAMA_ADAPTER_ID,
                adapter_version=OLLAMA_ADAPTER_VERSION,
                capabilities={},
            ),
            diagnostics=diagnostics,
        )

    async def start(self, config: AdapterConfiguration) -> AdapterProcessHandle:
        del config
        raise AdapterOperationUnsupported("LOCALFORGE never starts an Ollama service.")

    async def stop(self, process: AdapterProcessHandle) -> None:
        del process
        raise AdapterOperationUnsupported("LOCALFORGE never stops or terminates an Ollama process.")

    async def list_models(self, config: AdapterConfiguration) -> list[AdapterModel]:
        models, _omitted_remote, error = await self._inventory(config, refresh=True)
        if models is None:
            raise AdapterOperationUnsupported(
                "Ollama's local model inventory is unavailable. Probe the attached server and "
                "try again."
            ) from (RuntimeError(error) if error else None)
        return models

    async def prepare_request(
        self, config: AdapterConfiguration, request: GenerationRequest
    ) -> PreparedRequest:
        validation = await self.validate_config(config)
        if not validation.valid:
            message = next(
                (
                    item.message
                    for item in validation.diagnostics
                    if item.severity is DiagnosticSeverity.ERROR
                ),
                "The Ollama profile configuration is invalid.",
            )
            raise ValueError(message)
        if not request.model_ref.strip():
            raise ValueError("A locally installed Ollama model is required for chat completion.")
        if set(request.parameters) - _REQUEST_PARAMETERS:
            raise ValueError("The workload contains a sampling option not supported by Ollama.")
        for key, minimum, maximum in (
            ("temperature", 0.0, 3.4e38),
            ("top_p", 0.0, 1.0),
        ):
            value = request.parameters.get(key)
            if value is not None and not _number(value, minimum=minimum, maximum=maximum):
                raise ValueError(f"Ollama sampling option '{key}' is outside its valid range.")
        for key, maximum in (
            ("top_k", 2**63 - 1),
            ("seed", 2**63 - 1),
            ("max_tokens", 2**63 - 1),
        ):
            value = request.parameters.get(key)
            minimum = 1 if key in {"top_k", "max_tokens"} else 0
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"Ollama sampling option '{key}' is invalid.")
        for key in ("stop", "stop_sequences"):
            stop = request.parameters.get(key)
            if stop is not None and (
                isinstance(stop, str)
                and len(stop) > 1000
                or isinstance(stop, list)
                and (
                    len(stop) > 64
                    or any(not isinstance(item, str) or len(item) > 1000 for item in stop)
                )
                or not isinstance(stop, (str, list))
            ):
                raise ValueError("Ollama stop sequences must be text values under the size limit.")
        models, _omitted_remote, error = await self._inventory(config)
        if models is None:
            raise AdapterOperationUnsupported(
                "The local Ollama model inventory could not be verified. Probe again before "
                "running."
            ) from (RuntimeError(error) if error else None)
        if request.model_ref not in {model.model_ref for model in models}:
            raise AdapterOperationUnsupported(
                "This model is not verified as locally installed in Ollama. Probe the server and "
                "select a local model."
            )
        prepared = PreparedRequest(
            model_ref=request.model_ref,
            prompt=request.prompt,
            transport="ollama_chat",
            request_metadata={"parameters": request.parameters},
        )
        self._prepared[prepared.request_id] = (config, dict(request.parameters))
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
                    error_message="The prepared Ollama request is no longer available.",
                )
                return
            config, parameters = context
            sequence = 0
            output_started = False

            def make_event(
                event_type: StreamEventType,
                *,
                text: str | None = None,
                usage: UsageCounts | None = None,
                native_metrics: list[AdapterMetric] | None = None,
                error_code: str | None = None,
                error_message: str | None = None,
            ) -> AdapterStreamEvent:
                nonlocal sequence
                event = AdapterStreamEvent(
                    request_id=request.request_id,
                    sequence=sequence,
                    event_type=event_type,
                    text=text,
                    usage=usage,
                    native_metrics=native_metrics or [],
                    error_code=error_code,
                    error_message=error_message,
                )
                sequence += 1
                return event

            def output(text: str) -> AdapterStreamEvent:
                nonlocal output_started
                event_type = (
                    StreamEventType.OUTPUT_CHUNK if output_started else StreamEventType.FIRST_OUTPUT
                )
                output_started = True
                return make_event(event_type, text=text)

            payload = self._chat_payload(request, parameters, config)
            try:
                client = await self._get_client()
                async with client.stream(
                    "POST",
                    f"{_base_url(config)}/api/chat",
                    json=payload,
                    timeout=httpx2.Timeout(_timeout(config)),
                ) as response:
                    if not 200 <= response.status_code < 300:
                        yield make_event(
                            StreamEventType.ERROR,
                            error_code="ollama_chat_rejected",
                            error_message=(
                                "Ollama rejected the chat request with HTTP "
                                f"{response.status_code}."
                            ),
                        )
                        return
                    if payload["stream"] is False:
                        raw = await self._bounded_body(response, _MAX_CHAT_BYTES)
                        if raw is None:
                            yield make_event(
                                StreamEventType.ERROR,
                                error_code="response_too_large",
                                error_message=(
                                    "Ollama's chat response exceeded the adapter size limit."
                                ),
                            )
                            return
                        try:
                            result = json.loads(raw)
                        except (ValueError, json.JSONDecodeError):
                            yield make_event(
                                StreamEventType.ERROR,
                                error_code="malformed_chat_response",
                                error_message="Ollama returned an invalid chat response.",
                            )
                            return
                        if not isinstance(result, dict) or result.get("done") is not True:
                            yield make_event(
                                StreamEventType.ERROR,
                                error_code="malformed_chat_response",
                                error_message="Ollama returned an incomplete chat response.",
                            )
                            return
                        text = _chat_text(result)
                        if text is None:
                            yield make_event(
                                StreamEventType.ERROR,
                                error_code="malformed_chat_response",
                                error_message=(
                                    "Ollama's chat response did not contain message text."
                                ),
                            )
                            return
                        yield output(text)
                        usage = _usage(result)
                        metrics = _timing_metrics(result)
                        if usage is not None or metrics:
                            yield make_event(
                                StreamEventType.USAGE,
                                usage=usage,
                                native_metrics=metrics,
                            )
                        yield make_event(StreamEventType.COMPLETED)
                        return

                    total_chars = 0
                    saw_done = False
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        total_chars += len(line)
                        if len(line) > _MAX_NDJSON_LINE_CHARS or total_chars > _MAX_CHAT_BYTES:
                            yield make_event(
                                StreamEventType.ERROR,
                                error_code="stream_response_too_large",
                                error_message=(
                                    "Ollama's streaming response exceeded the adapter size limit."
                                ),
                            )
                            return
                        try:
                            chunk = json.loads(line)
                        except (ValueError, json.JSONDecodeError):
                            yield make_event(
                                StreamEventType.ERROR,
                                error_code="malformed_stream_event",
                                error_message="Ollama returned a malformed streaming event.",
                            )
                            return
                        if not isinstance(chunk, dict) or "error" in chunk:
                            yield make_event(
                                StreamEventType.ERROR,
                                error_code="ollama_stream_error",
                                error_message=(
                                    "Ollama reported an error while generating the response."
                                ),
                            )
                            return
                        text = _chat_text(chunk)
                        if text is not None and text:
                            yield output(text)
                        if chunk.get("done") is True:
                            saw_done = True
                            usage = _usage(chunk)
                            metrics = _timing_metrics(chunk)
                            if usage is not None or metrics:
                                yield make_event(
                                    StreamEventType.USAGE,
                                    usage=usage,
                                    native_metrics=metrics,
                                )
                            break
                    if not saw_done:
                        yield make_event(
                            StreamEventType.DISCONNECTED,
                            error_code="incomplete_stream",
                            error_message=(
                                "Ollama closed the response before its completion marker."
                            ),
                        )
                        return
                    if not output_started:
                        yield output("")
                    yield make_event(StreamEventType.COMPLETED)
            except httpx2.TimeoutException:
                yield make_event(
                    StreamEventType.TIMEOUT,
                    error_code="ollama_timeout",
                    error_message=(
                        "Ollama did not finish the request before the configured timeout."
                    ),
                )
            except httpx2.RequestError:
                yield make_event(
                    StreamEventType.DISCONNECTED,
                    error_code="ollama_disconnected",
                    error_message="The connection to Ollama closed before the request completed.",
                )
            finally:
                self._prepared.pop(request.request_id, None)

        return events()

    @staticmethod
    def _chat_payload(
        request: PreparedRequest,
        parameters: Mapping[str, JsonValue],
        config: AdapterConfiguration,
    ) -> dict[str, object]:
        mode = _extension(config).get("streaming_mode", "auto")
        stream = mode != "disabled"
        options: dict[str, JsonValue] = {}
        for key in ("temperature", "top_p", "top_k", "seed"):
            if key in parameters:
                options[key] = parameters[key]
        max_tokens = parameters.get("max_tokens")
        if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > 0:
            options["num_predict"] = max_tokens
        stops: list[JsonValue] = []
        for key in ("stop", "stop_sequences"):
            stop = parameters.get(key)
            if isinstance(stop, str):
                stops.append(stop)
            elif isinstance(stop, list):
                stops.extend(item for item in stop if isinstance(item, str))
        if stops:
            options["stop"] = stops
        return {
            "model": request.model_ref,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": stream,
            **({"options": options} if options else {}),
        }

    async def abort_request(self, request_id: UUID) -> AbortResult:
        del request_id
        return AbortResult(
            supported=False,
            accepted=False,
            message=(
                "Ollama has no documented cancellation endpoint; only the local connection can "
                "close."
            ),
        )

    async def read_metrics(self, config: AdapterConfiguration) -> MetricsResult:
        del config
        return MetricsResult(
            available=False,
            diagnostic=_diagnostic(
                "aggregate_metrics_unavailable",
                "Ollama reports per-request timings, but this adapter has no aggregate metrics "
                "endpoint.",
                DiagnosticSeverity.INFO,
            ),
        )

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        version, _error = await self._version(config)
        return RuntimeFingerprint(
            adapter_id=OLLAMA_ADAPTER_ID,
            adapter_version=OLLAMA_ADAPTER_VERSION,
            engine_version=version,
            runtime_name="Ollama server",
            runtime_version=version,
            settings={"server_layer": "Ollama", "underlying_runner": "unknown"},
        )

    async def close(self) -> None:
        self._closed = True
        self._prepared.clear()
        self._inventory_cache.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
