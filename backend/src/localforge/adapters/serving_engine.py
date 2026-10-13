"""Shared attach-only OpenAI Chat Completions bridge for serving engines."""

import hashlib
import json
import re
from collections.abc import AsyncIterator, Mapping
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
    capability,
)
from localforge.adapters.openai_compatible import (
    AdapterOperationUnsupported,
    CredentialResolver,
    CredentialUnavailableError,
    OpenAICompatibleEngineAdapter,
    _usage,
)
from localforge.adapters.prometheus import parse_engine_prometheus
from localforge.core.models import (
    CapabilitySupport,
    CapabilityValue,
    EngineIntegrationMode,
    JsonValue,
)
from localforge.core.urls import parse_engine_endpoint

_ROUTE_MARKER = re.compile(r"[?#\\\r\n]")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,99}")
_MAX_JSON_BYTES = 65_536
_MAX_METRICS_BYTES = 4_194_304
_MAX_USAGE_PROBE_BYTES = 65_536


def _diagnostic(
    code: str,
    message: str,
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO,
    *,
    retryable: bool = False,
) -> AdapterDiagnostic:
    return AdapterDiagnostic(
        code=code,
        message=message,
        severity=severity,
        retryable=retryable,
    )


def _extension(config: AdapterConfiguration, namespace: str) -> Mapping[str, JsonValue]:
    extensions = config.values.get("engine_extensions")
    if not isinstance(extensions, dict):
        return {}
    values = extensions.get(namespace)
    return values if isinstance(values, dict) else {}


def _safe_route(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 500:
        return False
    decoded = unquote(value)
    if _ROUTE_MARKER.search(decoded) or "://" in decoded:
        return False
    return all(segment not in {".", ".."} for segment in decoded.replace("\\", "/").split("/"))


def _route_url(base_url: str, route: str) -> str:
    parts = urlsplit(base_url)
    path = route if route.startswith("/") else f"{parts.path.rstrip('/')}/{route}"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _api_base_url(base_url: str) -> str:
    parts = urlsplit(base_url.rstrip("/"))
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _safe_model_info(payload: object) -> dict[str, JsonValue] | None:
    if not isinstance(payload, dict):
        return None
    safe: dict[str, JsonValue] = {}
    for key in (
        "served_model_name",
        "weight_version",
        "load_format",
        "reasoning_parser",
        "tool_call_parser",
        "disaggregation_mode",
        "model_type",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value and len(value) <= 240:
            safe[key] = value
    for key in ("is_generation", "has_image_understanding", "has_audio_understanding"):
        value = payload.get(key)
        if isinstance(value, bool):
            safe[key] = value
    architectures = payload.get("architectures")
    if isinstance(architectures, list):
        values = [item[:120] for item in architectures if isinstance(item, str) and item]
        if values:
            safe["architectures"] = cast(JsonValue, values[:20])
    return safe


def _version_from_payload(payload: object) -> str | None:
    value: object = None
    if isinstance(payload, str):
        value = payload
    elif isinstance(payload, dict):
        for name in ("version", "server_version", "vllm_version", "sglang_version"):
            if isinstance(payload.get(name), str):
                value = payload[name]
                break
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _VERSION.fullmatch(candidate) else None


class OpenAIServingEngineAdapter:
    """Connector plumbing that keeps engine identity and metrics separate from API shape."""

    adapter_id: str
    adapter_version: str
    namespace: str
    display_name: str
    description: str
    metrics_prefix: str
    default_health_route: str
    default_version_route: str | None
    default_model_info_route: str | None
    default_include_usage: bool
    docs_url: str
    default_port: int
    unsafe_routes: frozenset[str] = frozenset()

    _extension_keys = {
        "model_id",
        "timeout_seconds",
        "streaming_mode",
        "include_usage",
        "health_route",
        "version_route",
        "metrics_route",
        "metrics_enabled",
        "model_info_route",
    }

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._chat = OpenAICompatibleEngineAdapter(
            credential_resolver=credential_resolver,
            transport=transport,
        )

    def describe(self) -> AdapterManifest:
        return AdapterManifest(
            protocol_version=1,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            display_name=self.display_name,
            description=self.description,
            category=AdapterCategory.OPENAI_COMPATIBLE,
            integration_modes=[EngineIntegrationMode.ATTACHED_ENDPOINT],
            declared_capabilities={
                "chat_completion",
                "streaming",
                "cancellation",
                "usage_counts",
                "health",
                "model_inventory",
                "version_discovery",
                "native_metrics",
                "runtime_fingerprint",
            },
            supported_transports=["http", "https", "server_sent_events", "prometheus_text"],
            native_metrics=[f"{self.metrics_prefix}*"],
            config_schema={
                "type": "object",
                "properties": {
                    "endpoint_url": {"type": "string", "format": "uri"},
                    "engine_extensions": {
                        "type": "object",
                        "properties": {
                            self.namespace: {
                                "type": "object",
                                "properties": {
                                    "model_id": {"type": "string"},
                                    "timeout_seconds": {
                                        "type": "number",
                                        "minimum": 0.1,
                                        "maximum": 300,
                                    },
                                    "streaming_mode": {"enum": ["auto", "required", "disabled"]},
                                    "include_usage": {"type": "boolean"},
                                    "health_route": {"type": "string"},
                                    "version_route": {"type": "string"},
                                    "metrics_route": {"type": "string"},
                                    "metrics_enabled": {"type": "boolean"},
                                    "model_info_route": {"type": "string"},
                                },
                            }
                        },
                    },
                },
            },
            known_limitations=[
                "Attach mode only; LOCALFORGE does not install or launch this serving engine.",
                "Only text Chat Completions requests are normalized; engine-specific features "
                "remain available only through native metrics or engine-side configuration.",
                "Native Prometheus samples retain their engine names, labels, types, and reported "
                "definitions; they are not mapped to client-measured metrics.",
                "Server version or device details remain unknown when the selected engine does not "
                "expose them through a safe endpoint.",
            ],
            documentation_url=self.docs_url,
        )

    def _setting(
        self,
        config: AdapterConfiguration,
        key: str,
        default: JsonValue,
    ) -> JsonValue:
        return _extension(config, self.namespace).get(key, default)

    def _route_setting(
        self,
        config: AdapterConfiguration,
        key: str,
        default: str | None,
    ) -> str | None:
        value = self._setting(config, key, default)
        if not isinstance(value, str) or not value:
            return None
        normalized = unquote(value).strip("/").casefold()
        if normalized in self.unsafe_routes:
            return None
        return value

    def _root_url(self, config: AdapterConfiguration) -> str:
        value = config.values.get("endpoint_url")
        return value.strip().rstrip("/") if isinstance(value, str) else ""

    def _chat_config(self, config: AdapterConfiguration) -> AdapterConfiguration:
        extension: dict[str, JsonValue] = {
            "timeout_seconds": self._setting(config, "timeout_seconds", 30),
            "streaming_mode": self._setting(config, "streaming_mode", "auto"),
            "include_usage": self._setting(config, "include_usage", self.default_include_usage),
        }
        model_id = self._setting(config, "model_id", "")
        if isinstance(model_id, str) and model_id.strip():
            extension["model_id"] = model_id.strip()
        models_route = config.values.get("models_route", "models")
        if (
            isinstance(models_route, str)
            and unquote(models_route).strip("/").casefold() in self.unsafe_routes
        ):
            models_route = "models"
        values: dict[str, JsonValue] = {
            "endpoint_url": _api_base_url(self._root_url(config)),
            "health_route": self._route_setting(config, "health_route", self.default_health_route),
            "models_route": models_route,
            "engine_extensions": {"openai_compatible": extension},
        }
        return AdapterConfiguration(
            integration_mode=config.integration_mode,
            values=values,
            secret_refs=dict(config.secret_refs),
        )

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        diagnostics: list[AdapterDiagnostic] = []
        if config.integration_mode is not EngineIntegrationMode.ATTACHED_ENDPOINT:
            diagnostics.append(
                _diagnostic(
                    "integration_mode_unsupported",
                    f"{self.display_name} supports attached endpoints only.",
                    DiagnosticSeverity.ERROR,
                )
            )
        try:
            parse_engine_endpoint(self._root_url(config))
        except ValueError:
            diagnostics.append(
                _diagnostic(
                    "endpoint_url_invalid",
                    "Enter a valid HTTP loopback URL or HTTPS endpoint URL without embedded "
                    "credentials, query, or fragment.",
                    DiagnosticSeverity.ERROR,
                )
            )

        extension = _extension(config, self.namespace)
        unknown = set(extension) - self._extension_keys
        if unknown:
            diagnostics.append(
                _diagnostic(
                    "extension_fields_unsupported",
                    f"The {self.display_name} settings contain an unsupported field.",
                    DiagnosticSeverity.ERROR,
                )
            )
        configured_routes = {
            key: extension.get(key)
            for key in ("health_route", "version_route", "metrics_route", "model_info_route")
        }
        configured_routes["models_route"] = config.values.get("models_route")
        unsafe_route = next(
            (
                name
                for name, value in configured_routes.items()
                if isinstance(value, str)
                and unquote(value).strip("/").casefold() in self.unsafe_routes
            ),
            None,
        )
        if unsafe_route is not None:
            diagnostics.append(
                _diagnostic(
                    "route_unsafe",
                    f"The configured {unsafe_route} route can expose engine launch arguments or "
                    "credentials and is not allowed.",
                    DiagnosticSeverity.ERROR,
                )
            )
        invalid_route_type = next(
            (
                name
                for name, value in configured_routes.items()
                if value is not None and value != "" and not isinstance(value, str)
            ),
            None,
        )
        if invalid_route_type is not None:
            diagnostics.append(
                _diagnostic(
                    "route_invalid",
                    f"The configured {invalid_route_type} route must be a local path string.",
                    DiagnosticSeverity.ERROR,
                )
            )
        timeout = self._setting(config, "timeout_seconds", 30)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0.1 <= timeout <= 300
        ):
            diagnostics.append(
                _diagnostic(
                    "timeout_invalid",
                    "Request timeout must be between 0.1 and 300 seconds.",
                    DiagnosticSeverity.ERROR,
                )
            )
        streaming_mode = self._setting(config, "streaming_mode", "auto")
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
        include_usage = self._setting(config, "include_usage", self.default_include_usage)
        if not isinstance(include_usage, bool):
            diagnostics.append(
                _diagnostic(
                    "include_usage_invalid",
                    "Streaming usage option must be a boolean.",
                    DiagnosticSeverity.ERROR,
                )
            )
        metrics_enabled = self._setting(config, "metrics_enabled", True)
        if not isinstance(metrics_enabled, bool):
            diagnostics.append(
                _diagnostic(
                    "metrics_enabled_invalid",
                    "Native metrics enabled must be a boolean.",
                    DiagnosticSeverity.ERROR,
                )
            )
        model_id = self._setting(config, "model_id", "")
        if not isinstance(model_id, str) or len(model_id) > 500:
            diagnostics.append(
                _diagnostic(
                    "model_id_invalid",
                    "The configured model ID must be a string no longer than 500 characters.",
                    DiagnosticSeverity.ERROR,
                )
            )
        for route in (
            self._route_setting(config, "health_route", self.default_health_route),
            self._route_setting(config, "version_route", self.default_version_route),
            self._route_setting(config, "metrics_route", "/metrics"),
            self._route_setting(config, "model_info_route", self.default_model_info_route),
            config.values.get("models_route"),
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

        inner = await self._chat.validate_config(self._chat_config(config))
        diagnostics.extend(inner.diagnostics)
        return ConfigValidationResult(
            valid=not any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics),
            diagnostics=diagnostics,
        )

    async def start(self, config: AdapterConfiguration) -> AdapterProcessHandle:
        del config
        raise AdapterOperationUnsupported(
            f"{self.display_name} attaches to an existing endpoint and never starts its process."
        )

    async def stop(self, process: AdapterProcessHandle) -> None:
        del process
        raise AdapterOperationUnsupported(
            f"{self.display_name} does not own or stop endpoint processes."
        )

    async def list_models(self, config: AdapterConfiguration) -> list[AdapterModel]:
        return await self._chat.list_models(self._chat_config(config))

    async def prepare_request(
        self,
        config: AdapterConfiguration,
        request: GenerationRequest,
    ) -> PreparedRequest:
        return await self._chat.prepare_request(self._chat_config(config), request)

    def stream_request(self, request: PreparedRequest) -> AsyncIterator[AdapterStreamEvent]:
        return self._chat.stream_request(request)

    async def abort_request(self, request_id: UUID) -> AbortResult:
        return await self._chat.abort_request(request_id)

    async def _authorization_headers(self, config: AdapterConfiguration) -> dict[str, str]:
        return await self._chat._authorization_headers(self._chat_config(config))

    async def _get_payload(
        self,
        config: AdapterConfiguration,
        route: str,
        *,
        max_bytes: int,
    ) -> tuple[int | None, bytes | None, str | None]:
        try:
            headers = await self._authorization_headers(config)
            client = await self._chat._get_client()
            async with client.stream(
                "GET",
                _route_url(self._root_url(config), route),
                headers=headers,
                timeout=self._chat._timeout(self._chat_config(config)),
            ) as response:
                status = response.status_code
                if status != 200:
                    return status, None, None
                content_length = response.headers.get("content-length")
                if (
                    content_length
                    and content_length.isdecimal()
                    and int(content_length) > max_bytes
                ):
                    return status, None, "The endpoint response exceeded its size limit."
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        return status, None, "The endpoint response exceeded its size limit."
            return status, bytes(body), None
        except CredentialUnavailableError:
            return None, None, "The endpoint credential reference is unavailable."
        except httpx2.TimeoutException:
            return None, None, "The endpoint did not respond before the configured timeout."
        except httpx2.RequestError:
            return None, None, "The optional endpoint route could not be reached."

    async def _discover_version(
        self, config: AdapterConfiguration
    ) -> tuple[str | None, CapabilitySupport, str]:
        route = self._route_setting(config, "version_route", self.default_version_route)
        if route is None:
            return (
                None,
                CapabilitySupport.UNKNOWN,
                "No safe server-version route is available for this connector.",
            )
        status, body, error = await self._get_payload(config, route, max_bytes=_MAX_JSON_BYTES)
        if error is not None:
            return None, CapabilitySupport.UNKNOWN, error
        if status in {401, 403}:
            return (
                None,
                CapabilitySupport.UNKNOWN,
                "The server-version route requires authorization.",
            )
        if status != 200 or body is None:
            return None, CapabilitySupport.UNKNOWN, "The server-version route was unavailable."
        try:
            payload = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return None, CapabilitySupport.UNKNOWN, "The server-version response was malformed."
        version = _version_from_payload(payload)
        if version is None:
            return (
                None,
                CapabilitySupport.UNKNOWN,
                "The server did not report a valid version value.",
            )
        return version, CapabilitySupport.SUPPORTED, f"The server reported version {version}."

    async def _model_info(
        self, config: AdapterConfiguration
    ) -> tuple[dict[str, JsonValue] | None, AdapterDiagnostic | None]:
        route = self._route_setting(config, "model_info_route", self.default_model_info_route)
        if route is None:
            return None, None
        status, body, error = await self._get_payload(config, route, max_bytes=_MAX_JSON_BYTES)
        if error is not None:
            return None, _diagnostic("runtime_metadata_unavailable", error)
        if status in {401, 403}:
            return None, _diagnostic(
                "runtime_metadata_unauthorized",
                "The optional model metadata route rejected authorization.",
                DiagnosticSeverity.WARNING,
            )
        if status != 200 or body is None:
            return None, _diagnostic(
                "runtime_metadata_unavailable",
                "The optional model metadata route was not available.",
                DiagnosticSeverity.INFO,
            )
        try:
            payload = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            payload = None
        safe = _safe_model_info(payload)
        if safe is None:
            return None, _diagnostic(
                "runtime_metadata_malformed",
                "The optional model metadata response was not a JSON object.",
                DiagnosticSeverity.WARNING,
            )
        return safe, None

    async def _probe_usage_counts(
        self,
        config: AdapterConfiguration,
        model_id: str | None,
    ) -> CapabilityValue:
        version = "unreported"
        if self._setting(config, "include_usage", self.default_include_usage) is not True:
            return capability(
                CapabilitySupport.UNKNOWN,
                details="Streaming usage probing is disabled for this profile.",
                source=f"{self.adapter_id}:{version}:usage_probe",
            )
        if not model_id:
            return capability(
                CapabilitySupport.UNKNOWN,
                details="A model ID is required before streaming usage can be probed.",
                source=f"{self.adapter_id}:{version}:usage_probe",
            )

        payload: dict[str, JsonValue] = {
            "model": model_id,
            "messages": [{"role": "user", "content": "LOCALFORGE usage check. Reply OK."}],
            "max_tokens": 1,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            headers = await self._authorization_headers(config)
            client = await self._chat._get_client()
            async with client.stream(
                "POST",
                _route_url(_api_base_url(self._root_url(config)), "chat/completions"),
                headers=headers,
                json=payload,
                timeout=self._chat._timeout(self._chat_config(config)),
            ) as response:
                if response.status_code in {400, 422}:
                    return capability(
                        CapabilitySupport.UNSUPPORTED,
                        details=("The server rejected the streaming usage option for this model."),
                        source=f"{self.adapter_id}:{version}:usage_probe",
                    )
                if not 200 <= response.status_code < 300:
                    return capability(
                        CapabilitySupport.UNKNOWN,
                        details=(
                            "The optional streaming usage probe did not complete successfully."
                        ),
                        source=f"{self.adapter_id}:{version}:usage_probe",
                    )

                content_type = response.headers.get("content-type", "").casefold()
                if "text/event-stream" not in content_type:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _MAX_USAGE_PROBE_BYTES:
                            return capability(
                                CapabilitySupport.UNKNOWN,
                                details=(
                                    "The streaming usage probe response exceeded its size limit."
                                ),
                                source=f"{self.adapter_id}:{version}:usage_probe",
                            )
                    try:
                        parsed = json.loads(body)
                    except (ValueError, json.JSONDecodeError):
                        return capability(
                            CapabilitySupport.UNKNOWN,
                            details="The usage probe received a malformed JSON response.",
                            source=f"{self.adapter_id}:{version}:usage_probe",
                        )
                    state = (
                        CapabilitySupport.SUPPORTED
                        if _usage(parsed) is not None
                        else CapabilitySupport.UNSUPPORTED
                    )
                    return capability(
                        state,
                        details=(
                            "The server returned token usage counts."
                            if state is CapabilitySupport.SUPPORTED
                            else "The server response did not include token usage counts."
                        ),
                        source=f"{self.adapter_id}:{version}:usage_probe",
                    )

                data_lines: list[str] = []
                bytes_seen = 0
                completed = False
                async for line in response.aiter_lines():
                    bytes_seen += len(line.encode("utf-8"))
                    if bytes_seen > _MAX_USAGE_PROBE_BYTES:
                        return capability(
                            CapabilitySupport.UNKNOWN,
                            details="The streaming usage probe response exceeded its size limit.",
                            source=f"{self.adapter_id}:{version}:usage_probe",
                        )
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        data = "\n".join(data_lines)
                        data_lines.clear()
                        if data == "[DONE]":
                            completed = True
                            break
                        try:
                            event = json.loads(data)
                        except (ValueError, json.JSONDecodeError):
                            return capability(
                                CapabilitySupport.UNKNOWN,
                                details="The usage probe received a malformed stream event.",
                                source=f"{self.adapter_id}:{version}:usage_probe",
                            )
                        if _usage(event) is not None:
                            return capability(
                                CapabilitySupport.SUPPORTED,
                                details="The server returned token usage counts in its stream.",
                                source=f"{self.adapter_id}:{version}:usage_probe",
                            )
                if completed:
                    return capability(
                        CapabilitySupport.UNSUPPORTED,
                        details="The completed stream did not include token usage counts.",
                        source=f"{self.adapter_id}:{version}:usage_probe",
                    )
                return capability(
                    CapabilitySupport.UNKNOWN,
                    details="The stream ended before usage support could be determined.",
                    source=f"{self.adapter_id}:{version}:usage_probe",
                )
        except CredentialUnavailableError:
            details = "The endpoint credential reference is unavailable."
        except httpx2.TimeoutException:
            details = "The optional streaming usage probe timed out."
        except httpx2.RequestError:
            details = "The optional streaming usage probe could not reach the endpoint."
        return capability(
            CapabilitySupport.UNKNOWN,
            details=details,
            source=f"{self.adapter_id}:{version}:usage_probe",
        )

    async def read_metrics(self, config: AdapterConfiguration) -> MetricsResult:
        if self._setting(config, "metrics_enabled", True) is not True:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "native_metrics_disabled",
                    "Optional serving-engine metrics are disabled for this profile.",
                ),
            )
        route = self._route_setting(config, "metrics_route", "/metrics")
        if route is None:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "native_metrics_route_missing",
                    "No serving-engine metrics route is configured.",
                ),
            )
        status, body, error = await self._get_payload(
            config,
            route,
            max_bytes=_MAX_METRICS_BYTES,
        )
        if error is not None:
            retryable = "timeout" in error.casefold() or "could not be reached" in error.casefold()
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "native_metrics_unavailable",
                    error,
                    DiagnosticSeverity.INFO,
                    retryable=retryable,
                ),
            )
        if status in {401, 403}:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "native_metrics_unauthorized",
                    "The optional metrics route rejected authorization.",
                ),
            )
        if status == 404:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "native_metrics_not_enabled",
                    "The engine did not expose its optional Prometheus metrics route.",
                ),
            )
        if status != 200 or body is None:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "native_metrics_unavailable",
                    f"The optional metrics route returned HTTP {status}.",
                    retryable=status is not None and status >= 500,
                ),
            )
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "native_metrics_malformed",
                    "The Prometheus response was not valid UTF-8 text.",
                    DiagnosticSeverity.WARNING,
                ),
            )
        parsed = parse_engine_prometheus(text, metric_prefix=self.metrics_prefix)
        diagnostics: list[AdapterDiagnostic] = []
        if parsed.truncated_samples:
            diagnostics.append(
                _diagnostic(
                    "native_metrics_sample_limit",
                    f"Skipped {parsed.truncated_samples} additional {self.display_name} sample "
                    "line(s) after the parser limit was reached.",
                    DiagnosticSeverity.WARNING,
                )
            )
        if parsed.malformed_lines:
            diagnostics.append(
                _diagnostic(
                    "native_metrics_samples_skipped",
                    f"Skipped {parsed.malformed_lines} malformed or non-finite metric line(s).",
                    DiagnosticSeverity.WARNING,
                )
            )
        if parsed.missing_definitions:
            diagnostics.append(
                _diagnostic(
                    "native_metric_definition_missing",
                    f"{parsed.missing_definitions} metric sample(s) had no Prometheus HELP "
                    "definition; their units remain native and unnormalized.",
                    DiagnosticSeverity.WARNING,
                )
            )
        if parsed.unsupported_samples:
            diagnostics.append(
                _diagnostic(
                    "native_metrics_type_unsupported",
                    f"Skipped {parsed.unsupported_samples} summary or untyped metric sample(s) "
                    "because LOCALFORGE does not flatten those Prometheus types into a scalar.",
                    DiagnosticSeverity.WARNING,
                )
            )
        if not parsed.metrics:
            diagnostic = (
                diagnostics[0]
                if diagnostics
                else _diagnostic(
                    "native_metrics_empty",
                    f"The endpoint returned no {self.display_name} metric samples.",
                )
            )
            return MetricsResult(available=False, diagnostic=diagnostic)
        return MetricsResult(
            available=True,
            metrics=parsed.metrics,
            diagnostic=diagnostics[0] if diagnostics else None,
        )

    def _scope_capability(
        self,
        value: CapabilityValue,
        *,
        engine_version: str | None,
    ) -> CapabilityValue:
        version = engine_version or "unreported"
        detail = value.details or "Capability state was observed by the connector probe."
        detail = f"{detail} Observation is scoped to {self.display_name} {version}."
        return capability(
            value.state,
            details=detail,
            source=f"{self.adapter_id}:{version}:live_probe",
        )

    async def probe(self, config: AdapterConfiguration) -> ProbeResult:
        validation = await self.validate_config(config)
        if not validation.valid:
            details = "Probe skipped because the connector configuration is invalid."
            return ProbeResult(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                status=ProbeStatus.FAILED,
                reachable=capability(CapabilitySupport.UNKNOWN, details=details),
                health=capability(CapabilitySupport.UNKNOWN, details=details),
                checks={
                    "configuration": capability(
                        CapabilitySupport.UNKNOWN,
                        details=details,
                        source=f"{self.adapter_id}:configuration_validation",
                    )
                },
                capabilities=AdapterCapabilities(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    capabilities={
                        name: capability(CapabilitySupport.UNKNOWN)
                        for name in self.describe().declared_capabilities
                    },
                ),
                diagnostics=validation.diagnostics,
            )

        chat_probe = await self._chat.probe(self._chat_config(config))

        version, version_state, version_details = await self._discover_version(config)
        model_info, model_info_diagnostic = await self._model_info(config)
        metrics = await self.read_metrics(config)
        diagnostics = list(chat_probe.diagnostics)
        if version is None:
            if (
                self.default_version_route is None
                and self._route_setting(config, "version_route", None) is None
            ):
                diagnostics.append(
                    _diagnostic(
                        "server_version_not_safely_exposed",
                        f"{self.display_name} has no configured safe version route. "
                        "The connector does not call /server_info because that endpoint can "
                        "return server credentials.",
                        DiagnosticSeverity.WARNING,
                    )
                )
            else:
                diagnostics.append(
                    _diagnostic(
                        "server_version_unavailable",
                        version_details,
                        DiagnosticSeverity.INFO,
                    )
                )
        if model_info_diagnostic is not None:
            diagnostics.append(model_info_diagnostic)
        if metrics.diagnostic is not None:
            diagnostics.append(metrics.diagnostic)

        models = chat_probe.models
        if model_info:
            enriched: list[AdapterModel] = []
            for model in models:
                metadata = dict(model.metadata)
                metadata[self.namespace] = cast(JsonValue, model_info)
                enriched.append(model.model_copy(update={"metadata": metadata}))
            models = enriched

        usage_model = self._setting(config, "model_id", "")
        if not isinstance(usage_model, str) or not usage_model.strip():
            usage_model = models[0].model_ref if models else None
        usage_probe = (
            await self._probe_usage_counts(config, usage_model)
            if chat_probe.status is ProbeStatus.SUCCEEDED
            else capability(
                CapabilitySupport.UNKNOWN,
                details="Usage probing was skipped because the endpoint probe did not succeed.",
                source=f"{self.adapter_id}:unreported:usage_probe",
            )
        )
        usage_probe = self._scope_capability(usage_probe, engine_version=version)

        observed_capabilities = {
            name: self._scope_capability(value, engine_version=version)
            for name, value in chat_probe.capabilities.capabilities.items()
            if name in self.describe().declared_capabilities
        }
        observed_capabilities["usage_counts"] = usage_probe
        observed_capabilities["version_discovery"] = capability(
            version_state,
            details=version_details,
            source=f"{self.adapter_id}:{version or 'unknown'}:version_probe",
        )
        metrics_state = (
            CapabilitySupport.SUPPORTED
            if metrics.available
            else CapabilitySupport.UNKNOWN
            if metrics.diagnostic and metrics.diagnostic.retryable
            else CapabilitySupport.UNSUPPORTED
        )
        observed_capabilities["native_metrics"] = capability(
            metrics_state,
            details=(
                f"{len(metrics.metrics)} raw {self.display_name} Prometheus sample(s) were read."
                if metrics.available
                else (metrics.diagnostic.message if metrics.diagnostic else "Metrics unavailable.")
            ),
            source=f"{self.adapter_id}:{version or 'unknown'}:metrics_probe",
        )
        observed_capabilities["runtime_fingerprint"] = capability(
            CapabilitySupport.SUPPORTED,
            details=(
                "A privacy-filtered fingerprint records endpoint settings, model IDs, version, "
                "and allowlisted model metadata."
            ),
            source=f"{self.adapter_id}:{version or 'unknown'}:fingerprint",
        )

        checks = dict(chat_probe.checks)
        checks["usage_counts"] = usage_probe
        checks["version_discovery"] = observed_capabilities["version_discovery"]
        checks["native_metrics"] = observed_capabilities["native_metrics"]
        checks["runtime_metadata"] = capability(
            CapabilitySupport.SUPPORTED if model_info else CapabilitySupport.UNKNOWN,
            details=(
                "Allowlisted model metadata was read from the engine."
                if model_info
                else "The optional engine model metadata route was not available."
            ),
            source=f"{self.adapter_id}:{version or 'unknown'}:model_info",
        )
        return ProbeResult(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            status=chat_probe.status,
            reachable=chat_probe.reachable,
            health=chat_probe.health,
            engine_version=version,
            models=models,
            checks=checks,
            capabilities=AdapterCapabilities(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                capabilities=observed_capabilities,
            ),
            diagnostics=diagnostics,
        )

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        version, _state, _details = await self._discover_version(config)
        model_info, _diagnostic_result = await self._model_info(config)
        try:
            models = await self.list_models(config)
        except (
            httpx2.HTTPError,
            AdapterOperationUnsupported,
            CredentialUnavailableError,
            ValueError,
        ):
            models = []

        endpoint = urlsplit(self._root_url(config))
        model_ids = sorted({model.model_ref for model in models})[:256]
        safe_options: dict[str, JsonValue] = {
            "timeout_seconds": self._setting(config, "timeout_seconds", 30),
            "streaming_mode": self._setting(config, "streaming_mode", "auto"),
            "include_usage": self._setting(config, "include_usage", self.default_include_usage),
            "metrics_enabled": self._setting(config, "metrics_enabled", True),
            "credential_configured": "api_key" in config.secret_refs,
        }
        model_id = self._setting(config, "model_id", "")
        if isinstance(model_id, str) and model_id.strip():
            safe_options["selected_model_id"] = model_id.strip()
        fingerprint_source = {
            "adapter": self.adapter_id,
            "version": version,
            "models": model_ids,
            "configuration": safe_options,
            "engine_model_info": model_info or {},
        }
        serialized = json.dumps(fingerprint_source, sort_keys=True, separators=(",", ":"))
        safe_options["model_ids"] = cast(JsonValue, model_ids)
        safe_options["model_runtime_metadata"] = cast(JsonValue, model_info or {})
        safe_options["configuration_fingerprint_sha256"] = hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()
        return RuntimeFingerprint(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            engine_version=version,
            runtime_name=self.display_name,
            runtime_version=version,
            settings={
                self.namespace: {
                    "endpoint_host": endpoint.hostname or "unknown",
                    "endpoint_path": endpoint.path,
                    "configuration": cast(JsonValue, safe_options),
                    "version_source": (
                        "engine_reported" if version is not None else "not_safely_available"
                    ),
                }
            },
        )

    async def close(self) -> None:
        await self._chat.close()


class DefaultCredentialResolver:
    """Callable wrapper used to keep engine adapter constructors self-contained."""

    def __call__(self, reference: str) -> str | None:
        from localforge.adapters.openai_compatible import _read_os_credential

        return _read_os_credential(reference)


def default_credential_resolver() -> CredentialResolver:
    return DefaultCredentialResolver()
