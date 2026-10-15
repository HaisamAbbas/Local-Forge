"""Attach to or explicitly manage a local llama.cpp HTTP server."""

import asyncio
import json
import math
import os
import re
import socket
import sys
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
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
    capability,
)
from localforge.adapters.openai_compatible import OpenAICompatibleEngineAdapter
from localforge.core.models import (
    CapabilitySupport,
    CapabilityValue,
    EngineIntegrationMode,
    JsonValue,
    MetricSource,
)
from localforge.core.urls import parse_engine_endpoint

LLAMA_CPP_ADAPTER_ID = "localforge.llama_cpp"
LLAMA_CPP_ADAPTER_VERSION = "1.0.0"
LLAMA_CPP_TESTED_VERSION = "b11580"
LLAMA_CPP_TESTED_COMMIT = "4413a192410fad1f054e9216114087a3e01527a0"
_EXTENSION_NAMESPACE = "llama_cpp"
_EXTENSION_KEYS = {
    "model_id",
    "timeout_seconds",
    "startup_timeout_seconds",
    "executable_path",
    "model_path",
    "working_directory",
    "context_size",
    "parallelism",
    "threads",
    "batch_size",
    "ubatch_size",
    "gpu_layers",
}
_CONFIG_KEYS = {"endpoint_url", "health_route", "models_route", "engine_extensions"}
_MAX_BODY_BYTES = 1_048_576
_STOP_TIMEOUT_SECONDS = 5.0
_PROMETHEUS_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[+-]?(?:Inf|NaN))"
    r"(?:\s+\d+)?$"
)
_PROMETHEUS_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')

# Names, types, and units are copied from the pinned llama.cpp Prometheus documentation.
_METRIC_DEFINITIONS: dict[str, tuple[str, str]] = {
    "llamacpp:prompt_tokens_total": ("counter", "tokens"),
    "llamacpp:prompt_seconds_total": ("counter", "seconds"),
    "llamacpp:prompt_tokens_seconds": ("gauge", "tokens/second"),
    "llamacpp:tokens_predicted_total": ("counter", "tokens"),
    "llamacpp:tokens_predicted_seconds_total": ("counter", "seconds"),
    "llamacpp:predicted_tokens_seconds": ("gauge", "tokens/second"),
    "llamacpp:requests_processing": ("gauge", "requests"),
    "llamacpp:requests_deferred": ("gauge", "requests"),
    "llamacpp:n_tokens_max": ("counter", "tokens"),
    "llamacpp:n_decode_total": ("counter", "decode_calls"),
    "llamacpp:n_busy_slots_per_decode": ("gauge", "slots/decode"),
    "llamacpp:spec_decode_num_draft_tokens_total": ("counter", "tokens"),
    "llamacpp:spec_decode_num_accepted_tokens_total": ("counter", "tokens"),
    "llamacpp:spec_decode_num_drafts_total": ("counter", "drafts"),
    "llamacpp:spec_decode_num_accepted_tokens_per_pos_total": ("counter", "tokens"),
}
_MANAGED_KEYS = {
    "executable_path",
    "model_path",
    "working_directory",
    "context_size",
    "parallelism",
    "threads",
    "batch_size",
    "ubatch_size",
    "gpu_layers",
    "startup_timeout_seconds",
}


@dataclass
class _OwnedProcess:
    handle: AdapterProcessHandle
    process: asyncio.subprocess.Process
    os_pid: int


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


def _extension(config: AdapterConfiguration) -> Mapping[str, JsonValue]:
    namespaces = config.values.get("engine_extensions")
    if not isinstance(namespaces, dict):
        return {}
    values = namespaces.get(_EXTENSION_NAMESPACE)
    return values if isinstance(values, dict) else {}


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: object, *, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _positive_integer(value: object, *, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= maximum


def _api_base(endpoint_url: str) -> str:
    parsed = parse_engine_endpoint(endpoint_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _root_route(endpoint_url: str, route: str) -> str:
    parsed = parse_engine_endpoint(endpoint_url)
    prefix = parsed.path.rstrip("/")
    if prefix.endswith("/v1"):
        prefix = prefix[:-3]
    path = f"{prefix}/{route.lstrip('/')}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _model_from_props(props: Mapping[str, object]) -> AdapterModel | None:
    model_path = _text(props.get("model_path"))
    if model_path is None or len(model_path) > 500:
        return None
    display_name = model_path.replace("\\", "/").rsplit("/", 1)[-1][:240]
    if not display_name:
        display_name = "llama.cpp model"
    metadata: dict[str, JsonValue] = {"source": "llama_cpp_props", "model_path": model_path}
    build_info = _text(props.get("build_info"))
    if build_info:
        metadata["build_info"] = build_info
    settings = props.get("default_generation_settings")
    if isinstance(settings, dict):
        context_size = settings.get("n_ctx")
        if _positive_integer(context_size, maximum=2**31 - 1):
            metadata["context_size"] = context_size
    total_slots = props.get("total_slots")
    if _positive_integer(total_slots, maximum=2**31 - 1):
        metadata["parallelism"] = cast(int, total_slots)
    return AdapterModel(model_ref=model_path, display_name=display_name, metadata=metadata)


def _metrics_from_prometheus(body: str) -> tuple[list[AdapterMetric], int]:
    declared_types: dict[str, str] = {}
    metrics: list[AdapterMetric] = []
    rejected = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line.split()
            if len(parts) == 4 and parts[1] == "TYPE":
                declared_types[parts[2]] = parts[3].casefold()
            continue
        match = _PROMETHEUS_SAMPLE.fullmatch(line)
        if match is None:
            rejected += 1
            continue
        name = match.group("name")
        definition = _METRIC_DEFINITIONS.get(name)
        if definition is None:
            continue
        expected_kind, unit = definition
        upstream_kind = declared_types.get(name)
        if upstream_kind is not None and upstream_kind != expected_kind:
            rejected += 1
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            rejected += 1
            continue
        if not math.isfinite(value) or (expected_kind == "counter" and value < 0):
            rejected += 1
            continue
        labels = _parse_prometheus_labels(match.group("labels"))
        if labels is None:
            rejected += 1
            continue
        metrics.append(
            AdapterMetric(
                metric_name=name,
                value=value,
                unit=unit,
                source=MetricSource.ENGINE_NATIVE,
                kind=cast(Literal["counter", "gauge"], expected_kind),
                labels=labels,
            )
        )
    return metrics, rejected


def _parse_prometheus_labels(label_text: str | None) -> dict[str, str] | None:
    if not label_text:
        return {}
    labels: dict[str, str] = {}
    offset = 0
    while offset < len(label_text):
        match = _PROMETHEUS_LABEL.match(label_text, offset)
        if match is None:
            return None
        name, encoded_value = match.groups()
        if name in labels:
            return None
        value: list[str] = []
        index = 0
        while index < len(encoded_value):
            character = encoded_value[index]
            index += 1
            if character != "\\":
                value.append(character)
                continue
            if index == len(encoded_value):
                return None
            escaped = encoded_value[index]
            index += 1
            if escaped == "n":
                value.append("\n")
            elif escaped in {'"', "\\"}:
                value.append(escaped)
            else:
                return None
        labels[name] = "".join(value)
        offset = match.end()
        if offset == len(label_text):
            return labels
        if label_text[offset] != ",":
            return None
        offset += 1
    return None


class LlamaCppEngineAdapter:
    """Adapter for llama-server's OpenAI API plus its native inspection endpoints."""

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._client: httpx2.AsyncClient | None = None
        self._openai = OpenAICompatibleEngineAdapter(transport=transport)
        self._owned_processes: dict[UUID, _OwnedProcess] = {}
        self._closed = False

    def describe(self) -> AdapterManifest:
        return AdapterManifest(
            protocol_version=1,
            adapter_id=LLAMA_CPP_ADAPTER_ID,
            adapter_version=LLAMA_CPP_ADAPTER_VERSION,
            display_name="llama.cpp / llama-server",
            description=(
                f"Attach to a local llama-server or launch an explicitly selected binary. "
                f"Adapter behavior is versioned against {LLAMA_CPP_TESTED_VERSION} "
                f"({LLAMA_CPP_TESTED_COMMIT[:7]})."
            ),
            category=AdapterCategory.SERVING_STACK,
            integration_modes=[
                EngineIntegrationMode.ATTACHED_ENDPOINT,
                EngineIntegrationMode.MANAGED_PROCESS,
            ],
            version_tested=[
                f"{LLAMA_CPP_TESTED_VERSION} (local API fixture)",
                "b11609 (live, Windows CPU, attached and managed)",
            ],
            operating_systems=[
                AdapterPlatform.WINDOWS,
                AdapterPlatform.LINUX,
                AdapterPlatform.MACOS,
            ],
            declared_capabilities={
                "chat_completion",
                "streaming",
                "cancellation",
                "usage_counts",
                "health",
                "model_inventory",
                "native_metrics",
                "managed_process",
            },
            supported_transports=["http", "https", "server_sent_events"],
            native_metrics=sorted(_METRIC_DEFINITIONS),
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
                                    "timeout_seconds": {
                                        "type": "number",
                                        "minimum": 0.1,
                                        "maximum": 300,
                                    },
                                    "startup_timeout_seconds": {
                                        "type": "number",
                                        "minimum": 0.1,
                                        "maximum": 300,
                                    },
                                    "executable_path": {"type": "string"},
                                    "model_path": {"type": "string", "pattern": r"(?i)\.gguf$"},
                                    "working_directory": {"type": "string"},
                                    "context_size": {"type": "integer", "minimum": 1},
                                    "parallelism": {"type": "integer", "minimum": 1},
                                    "threads": {"type": "integer", "minimum": 1},
                                    "batch_size": {"type": "integer", "minimum": 1},
                                    "ubatch_size": {"type": "integer", "minimum": 1},
                                    "gpu_layers": {"type": ["integer", "string"]},
                                },
                                "additionalProperties": False,
                            }
                        },
                    },
                },
                "additionalProperties": False,
            },
            known_limitations=[
                "Only the llama-server build b11580 at commit "
                f"{LLAMA_CPP_TESTED_COMMIT} is version-verified by this adapter.",
                "GGUF model files must already exist locally; LocalForge never downloads them.",
                "GPU support depends on the backend compiled into the selected llama-server "
                "binary. LocalForge does not install or infer CUDA, Metal, Vulkan, or other "
                "backend dependencies.",
                "Managed launch binds only to 127.0.0.1 and requires explicit executable, "
                "model, and working-directory paths.",
            ],
            documentation_url=(
                "https://github.com/ggml-org/llama.cpp/blob/"
                f"{LLAMA_CPP_TESTED_COMMIT}/tools/server/README.md"
            ),
        )

    def _extension(self, config: AdapterConfiguration) -> Mapping[str, JsonValue]:
        return _extension(config)

    def _endpoint(self, config: AdapterConfiguration) -> str:
        value = config.values.get("endpoint_url")
        return value.strip().rstrip("/") if isinstance(value, str) else ""

    def _timeout(self, config: AdapterConfiguration) -> float:
        value = self._extension(config).get("timeout_seconds", 30)
        return (
            float(value)
            if isinstance(value, (int, float)) and _number(value, minimum=0.1, maximum=300)
            else 30.0
        )

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        diagnostics: list[AdapterDiagnostic] = []
        unknown_config = set(config.values) - _CONFIG_KEYS
        if unknown_config:
            diagnostics.append(
                _diagnostic(
                    "config_fields_unsupported",
                    "llama.cpp settings contain an unsupported top-level field.",
                    DiagnosticSeverity.ERROR,
                )
            )
        namespaces = config.values.get("engine_extensions", {})
        if not isinstance(namespaces, dict):
            namespaces = {}
            diagnostics.append(
                _diagnostic(
                    "extensions_invalid",
                    "Engine extension settings must be an object.",
                    DiagnosticSeverity.ERROR,
                )
            )
        if set(namespaces) - {_EXTENSION_NAMESPACE}:
            diagnostics.append(
                _diagnostic(
                    "extension_namespaces_unsupported",
                    "Only llama.cpp adapter settings are accepted for this profile.",
                    DiagnosticSeverity.ERROR,
                )
            )
        extension = namespaces.get(_EXTENSION_NAMESPACE, {})
        if not isinstance(extension, dict):
            extension = {}
            diagnostics.append(
                _diagnostic(
                    "extension_invalid",
                    "llama.cpp settings must be an object.",
                    DiagnosticSeverity.ERROR,
                )
            )
        unknown_extension = set(extension) - _EXTENSION_KEYS
        if unknown_extension:
            diagnostics.append(
                _diagnostic(
                    "extension_fields_unsupported",
                    "llama.cpp settings contain a field this adapter does not validate.",
                    DiagnosticSeverity.ERROR,
                )
            )

        endpoint = self._endpoint(config)
        try:
            parsed = parse_engine_endpoint(endpoint)
        except ValueError:
            parsed = None
            diagnostics.append(
                _diagnostic(
                    "endpoint_url_invalid",
                    "Enter a valid HTTP loopback URL or HTTPS endpoint URL without credentials, "
                    "query, or fragment.",
                    DiagnosticSeverity.ERROR,
                )
            )
        if config.integration_mode not in {
            EngineIntegrationMode.ATTACHED_ENDPOINT,
            EngineIntegrationMode.MANAGED_PROCESS,
        }:
            diagnostics.append(
                _diagnostic(
                    "integration_mode_unsupported",
                    "llama.cpp supports attached endpoints and managed processes only.",
                    DiagnosticSeverity.ERROR,
                )
            )
        if config.integration_mode is EngineIntegrationMode.MANAGED_PROCESS:
            if parsed is not None:
                if parsed.scheme != "http":
                    diagnostics.append(
                        _diagnostic(
                            "managed_endpoint_scheme_unsupported",
                            "Managed llama-server profiles use local HTTP on 127.0.0.1.",
                            DiagnosticSeverity.ERROR,
                        )
                    )
                if parsed.hostname is None or parsed.hostname.casefold() != "127.0.0.1":
                    diagnostics.append(
                        _diagnostic(
                            "managed_bind_must_be_loopback",
                            "Managed llama-server profiles bind to 127.0.0.1 only.",
                            DiagnosticSeverity.ERROR,
                        )
                    )
                if parsed.path.rstrip("/") not in {"", "/v1"}:
                    diagnostics.append(
                        _diagnostic(
                            "managed_endpoint_path_unsupported",
                            "Managed profiles use the local root endpoint or its /v1 API path.",
                            DiagnosticSeverity.ERROR,
                        )
                    )
                if parsed.port == 0:
                    diagnostics.append(
                        _diagnostic(
                            "managed_port_invalid",
                            "Choose a loopback port from 1 to 65535.",
                            DiagnosticSeverity.ERROR,
                        )
                    )
            if not (
                sys.platform.startswith("win")
                or sys.platform.startswith("linux")
                or sys.platform == "darwin"
            ):
                diagnostics.append(
                    _diagnostic(
                        "managed_platform_unsupported",
                        "Managed llama-server launch is available on Windows, Linux, and macOS.",
                        DiagnosticSeverity.ERROR,
                    )
                )
            self._validate_path(
                extension,
                "executable_path",
                "Select an existing llama-server executable.",
                diagnostics,
                executable=True,
            )
            self._validate_path(
                extension,
                "model_path",
                "Select an existing local .gguf model file.",
                diagnostics,
                suffix=".gguf",
            )
            # Without an explicit working directory, llama-server runs from its own folder.
            if _text(extension.get("working_directory")):
                self._validate_path(
                    extension,
                    "working_directory",
                    "Select an existing working directory for llama-server.",
                    diagnostics,
                    directory=True,
                )
            gpu_layers = extension.get("gpu_layers")
            if gpu_layers is not None and not (
                (
                    isinstance(gpu_layers, int)
                    and not isinstance(gpu_layers, bool)
                    and 0 <= gpu_layers <= 100_000
                )
                or (isinstance(gpu_layers, str) and gpu_layers.casefold() in {"auto", "all"})
            ):
                diagnostics.append(
                    _diagnostic(
                        "gpu_layers_invalid",
                        "GPU layers must be a non-negative integer, auto, or all.",
                        DiagnosticSeverity.ERROR,
                    )
                )
            startup_timeout = extension.get("startup_timeout_seconds", 120)
            if not _number(startup_timeout, minimum=0.1, maximum=300):
                diagnostics.append(
                    _diagnostic(
                        "startup_timeout_invalid",
                        "Startup timeout must be between 0.1 and 300 seconds.",
                        DiagnosticSeverity.ERROR,
                    )
                )
        elif _MANAGED_KEYS.intersection(extension):
            diagnostics.append(
                _diagnostic(
                    "managed_settings_require_managed_mode",
                    "Executable, GGUF, and launch settings require managed-process mode.",
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
        model_id = extension.get("model_id")
        if model_id is not None and (
            not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 500
        ):
            diagnostics.append(
                _diagnostic(
                    "model_id_invalid",
                    "Model ID must be a non-empty string of at most 500 characters.",
                    DiagnosticSeverity.ERROR,
                )
            )
        for key, maximum in (
            ("context_size", 1_048_576),
            ("parallelism", 1024),
            ("threads", 1024),
            ("batch_size", 1_000_000),
            ("ubatch_size", 1_000_000),
        ):
            value = extension.get(key)
            if value is not None and not _positive_integer(value, maximum=maximum):
                diagnostics.append(
                    _diagnostic(
                        f"{key}_invalid",
                        f"{key.replace('_', ' ').capitalize()} must be an integer from 1 to "
                        f"{maximum}.",
                        DiagnosticSeverity.ERROR,
                    )
                )
        batch = extension.get("batch_size")
        ubatch = extension.get("ubatch_size")
        if isinstance(batch, int) and isinstance(ubatch, int) and ubatch > batch:
            diagnostics.append(
                _diagnostic(
                    "ubatch_size_exceeds_batch_size",
                    "Physical batch size cannot exceed logical batch size.",
                    DiagnosticSeverity.ERROR,
                )
            )
        errors = [item for item in diagnostics if item.severity is DiagnosticSeverity.ERROR]
        return ConfigValidationResult(valid=not errors, diagnostics=diagnostics)

    @staticmethod
    def _validate_path(
        extension: Mapping[str, JsonValue],
        key: str,
        message: str,
        diagnostics: list[AdapterDiagnostic],
        *,
        executable: bool = False,
        directory: bool = False,
        suffix: str | None = None,
    ) -> None:
        value = extension.get(key)
        path = Path(value) if isinstance(value, str) and value.strip() else None
        is_valid = False
        if path is not None and path.is_absolute():
            if executable:
                is_valid = path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))
            elif directory:
                is_valid = path.is_dir()
            elif suffix is not None:
                is_valid = path.is_file() and path.suffix.casefold() == suffix
        if not is_valid:
            diagnostics.append(
                _diagnostic(
                    f"{key}_invalid",
                    message,
                    DiagnosticSeverity.ERROR,
                )
            )

    def _openai_config(self, config: AdapterConfiguration) -> AdapterConfiguration:
        extension = self._extension(config)
        generic_extension: dict[str, JsonValue] = {
            "timeout_seconds": extension.get("timeout_seconds", 30),
            "max_retries": 0,
            "retry_backoff_seconds": 0.25,
            "streaming_mode": "auto",
            "include_usage": True,
        }
        model_id = _text(extension.get("model_id"))
        if model_id:
            generic_extension["model_id"] = model_id
        return AdapterConfiguration(
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            values={
                "endpoint_url": _api_base(self._endpoint(config)),
                "health_route": "/health",
                "engine_extensions": {"openai_compatible": generic_extension},
            },
            secret_refs=config.secret_refs,
        )

    async def _get_client(self) -> httpx2.AsyncClient:
        if self._closed:
            raise RuntimeError("llama.cpp adapter is closed.")
        if self._client is None:
            self._client = httpx2.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            )
        return self._client

    async def _get_json(
        self,
        config: AdapterConfiguration,
        route: str,
    ) -> tuple[int | None, dict[str, object] | None, str | None]:
        try:
            client = await self._get_client()
            async with client.stream(
                "GET",
                _root_route(self._endpoint(config), route),
                timeout=self._timeout(config),
            ) as response:
                status = response.status_code
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_BODY_BYTES:
                        return status, None, "response_too_large"
                if not 200 <= status < 300:
                    return status, None, None
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    return status, None, "response_invalid"
                return status, cast(dict[str, object], payload), None
        except httpx2.TimeoutException:
            return None, None, "request_timeout"
        except httpx2.RequestError:
            return None, None, "endpoint_unreachable"
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None, None, "response_invalid"

    async def probe(self, config: AdapterConfiguration) -> ProbeResult:
        validation = await self.validate_config(config)
        if not validation.valid:
            message = next(
                (
                    item.message
                    for item in validation.diagnostics
                    if item.severity is DiagnosticSeverity.ERROR
                ),
                "The llama.cpp profile configuration is invalid.",
            )
            return self._failed_probe(
                checks={"configuration": capability(CapabilitySupport.UNSUPPORTED)},
                diagnostics=[
                    _diagnostic("configuration_invalid", message, DiagnosticSeverity.ERROR)
                ],
            )

        generic = await self._openai.probe(self._openai_config(config))
        diagnostics = list(generic.diagnostics)
        checks = dict(generic.checks)
        health_status, health_body, health_error = await self._get_json(config, "/health")
        props_status, props, props_error = await self._get_json(config, "/props")

        if health_status == 200:
            health_state = CapabilitySupport.SUPPORTED
            health_details = "llama-server /health returned HTTP 200."
        elif health_status == 503:
            health_state = CapabilitySupport.UNSUPPORTED
            health_details = "llama-server is reachable but is still loading its model."
            diagnostics.append(
                _diagnostic(
                    "llama_server_loading",
                    health_details,
                    DiagnosticSeverity.WARNING,
                    retryable=True,
                )
            )
        elif health_status is not None:
            health_state = CapabilitySupport.UNSUPPORTED
            health_details = f"llama-server /health returned HTTP {health_status}."
            diagnostics.append(
                _diagnostic(
                    "llama_health_unavailable",
                    health_details,
                    DiagnosticSeverity.WARNING,
                )
            )
        else:
            health_state = CapabilitySupport.UNKNOWN
            health_details = "llama-server /health could not be checked."
            diagnostics.append(
                _diagnostic(
                    health_error or "llama_health_unavailable",
                    health_details,
                    DiagnosticSeverity.WARNING,
                    retryable=health_error == "request_timeout",
                )
            )
        checks["llama_health"] = capability(
            health_state,
            details=health_details,
            source="llama_cpp_health",
        )

        props_state = CapabilitySupport.UNKNOWN
        model_identity = CapabilitySupport.UNKNOWN
        engine_version: str | None = None
        props_model: AdapterModel | None = None
        if props_status == 200 and props is not None:
            props_state = CapabilitySupport.SUPPORTED
            props_model = _model_from_props(props)
            model_identity = (
                CapabilitySupport.SUPPORTED
                if props_model is not None
                else CapabilitySupport.UNKNOWN
            )
            engine_version = _text(props.get("build_info"))
            if not engine_version:
                diagnostics.append(
                    _diagnostic(
                        "llama_version_unavailable",
                        "The server properties did not report build_info.",
                        DiagnosticSeverity.INFO,
                    )
                )
            elif not self._matches_tested_version(engine_version):
                diagnostics.append(
                    _diagnostic(
                        "llama_tested_version_mismatch",
                        f"This adapter was verified against {LLAMA_CPP_TESTED_VERSION} "
                        f"({LLAMA_CPP_TESTED_COMMIT[:7]}); the attached server reports "
                        f"{engine_version}.",
                        DiagnosticSeverity.WARNING,
                    )
                )
        elif props_status is not None:
            props_state = CapabilitySupport.UNSUPPORTED
            diagnostics.append(
                _diagnostic(
                    "llama_props_unavailable",
                    f"llama-server /props returned HTTP {props_status}.",
                    DiagnosticSeverity.INFO,
                )
            )
        else:
            diagnostics.append(
                _diagnostic(
                    props_error or "llama_props_unavailable",
                    "llama-server /props could not be checked independently.",
                    DiagnosticSeverity.INFO,
                    retryable=props_error == "request_timeout",
                )
            )
        checks["server_properties"] = capability(
            props_state,
            details="Read-only llama-server /props endpoint.",
            source="llama_cpp_props",
        )
        checks["model_identity"] = capability(
            model_identity,
            details="Model identity is read from /props.model_path when available.",
            source="llama_cpp_props",
        )
        if engine_version:
            version_state = (
                CapabilitySupport.SUPPORTED
                if self._matches_tested_version(engine_version)
                else CapabilitySupport.UNSUPPORTED
            )
            checks["tested_server_version"] = capability(
                version_state,
                details=engine_version,
                source="llama_cpp_props.build_info",
            )
        else:
            checks["tested_server_version"] = capability(
                CapabilitySupport.UNKNOWN,
                details=f"Expected {LLAMA_CPP_TESTED_VERSION} or commit {LLAMA_CPP_TESTED_COMMIT}.",
                source="llama_cpp_props.build_info",
            )

        metrics = await self.read_metrics(config)
        metrics_state = (
            CapabilitySupport.SUPPORTED if metrics.available else CapabilitySupport.UNSUPPORTED
        )
        checks["native_metrics"] = capability(
            metrics_state,
            details=(
                "The documented llama.cpp Prometheus metrics were read."
                if metrics.available
                else metrics.diagnostic.message
                if metrics.diagnostic
                else "Metrics are unavailable."
            ),
            source="llama_cpp_metrics",
        )
        if metrics.diagnostic is not None and metrics.diagnostic.code != "llama_metrics_disabled":
            diagnostics.append(metrics.diagnostic)

        models = list(generic.models)
        if not models and props_model is not None:
            models = [props_model]
        capabilities = dict(generic.capabilities.capabilities)
        capabilities["health"] = capability(
            health_state,
            details=health_details,
            source="llama_cpp_health",
        )
        if (
            models
            and capabilities.get("model_inventory", capability(CapabilitySupport.UNKNOWN)).state
            is CapabilitySupport.UNKNOWN
        ):
            capabilities["model_inventory"] = capability(
                CapabilitySupport.SUPPORTED,
                details="A model identity was reported by llama-server /props.",
                source="llama_cpp_props",
            )
        capabilities["native_metrics"] = capability(
            metrics_state,
            source="llama_cpp_metrics",
        )
        reachable = (
            CapabilitySupport.SUPPORTED
            if generic.reachable.state is CapabilitySupport.SUPPORTED
            or health_status is not None
            or props_status is not None
            else CapabilitySupport.UNSUPPORTED
        )
        return ProbeResult(
            adapter_id=LLAMA_CPP_ADAPTER_ID,
            adapter_version=LLAMA_CPP_ADAPTER_VERSION,
            status=ProbeStatus.SUCCEEDED
            if reachable is CapabilitySupport.SUPPORTED
            else ProbeStatus.FAILED,
            reachable=capability(reachable, source="llama_cpp_probe"),
            health=capability(health_state, details=health_details, source="llama_cpp_health"),
            engine_version=engine_version,
            models=models,
            checks=checks,
            capabilities=AdapterCapabilities(
                adapter_id=LLAMA_CPP_ADAPTER_ID,
                adapter_version=LLAMA_CPP_ADAPTER_VERSION,
                capabilities=capabilities,
            ),
            diagnostics=diagnostics,
        )

    @staticmethod
    def _matches_tested_version(build_info: str) -> bool:
        normalized = build_info.strip().casefold()
        if normalized == LLAMA_CPP_TESTED_VERSION:
            return True
        expected_prefix = f"{LLAMA_CPP_TESTED_VERSION}-{LLAMA_CPP_TESTED_COMMIT[:7]}"
        expected_full = f"{LLAMA_CPP_TESTED_VERSION}-{LLAMA_CPP_TESTED_COMMIT}"
        return normalized in {expected_prefix, expected_full}

    def _failed_probe(
        self,
        *,
        checks: dict[str, CapabilityValue],
        diagnostics: list[AdapterDiagnostic],
    ) -> ProbeResult:
        capabilities = {
            key: capability(CapabilitySupport.UNKNOWN, source="llama_cpp_probe")
            for key in (
                "chat_completion",
                "streaming",
                "cancellation",
                "usage_counts",
                "health",
                "model_inventory",
                "native_metrics",
            )
        }
        return ProbeResult(
            adapter_id=LLAMA_CPP_ADAPTER_ID,
            adapter_version=LLAMA_CPP_ADAPTER_VERSION,
            status=ProbeStatus.FAILED,
            reachable=capability(CapabilitySupport.UNKNOWN, source="llama_cpp_probe"),
            health=capability(CapabilitySupport.UNKNOWN, source="llama_cpp_probe"),
            checks=checks,
            capabilities=AdapterCapabilities(
                adapter_id=LLAMA_CPP_ADAPTER_ID,
                adapter_version=LLAMA_CPP_ADAPTER_VERSION,
                capabilities=capabilities,
            ),
            diagnostics=diagnostics,
        )

    async def list_models(self, config: AdapterConfiguration) -> list[AdapterModel]:
        validation = await self.validate_config(config)
        if not validation.valid:
            raise ValueError("The llama.cpp profile configuration is invalid.")
        try:
            models = await self._openai.list_models(self._openai_config(config))
        except Exception:
            models = []
        if models:
            return models
        _status, props, _error = await self._get_json(config, "/props")
        model = _model_from_props(props) if props is not None else None
        return [model] if model is not None else []

    async def prepare_request(
        self,
        config: AdapterConfiguration,
        request: GenerationRequest,
    ) -> PreparedRequest:
        validation = await self.validate_config(config)
        if not validation.valid:
            raise ValueError("The llama.cpp profile configuration is invalid.")
        return await self._openai.prepare_request(self._openai_config(config), request)

    def stream_request(self, request: PreparedRequest) -> AsyncIterator[AdapterStreamEvent]:
        return self._openai.stream_request(request)

    async def abort_request(self, request_id: UUID) -> AbortResult:
        return await self._openai.abort_request(request_id)

    async def read_metrics(self, config: AdapterConfiguration) -> MetricsResult:
        validation = await self.validate_config(config)
        if not validation.valid:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "llama_metrics_configuration_invalid",
                    "The llama.cpp profile configuration is invalid.",
                    DiagnosticSeverity.WARNING,
                ),
            )
        endpoint = self._endpoint(config)
        model_id = _text(self._extension(config).get("model_id"))
        params = {"model": model_id} if model_id else None
        try:
            client = await self._get_client()
            async with client.stream(
                "GET",
                _root_route(endpoint, "/metrics"),
                params=params,
                timeout=self._timeout(config),
            ) as response:
                if response.status_code in {404, 405, 501}:
                    return MetricsResult(
                        available=False,
                        diagnostic=_diagnostic(
                            "llama_metrics_disabled",
                            "llama-server metrics are optional and are disabled unless the server "
                            "was started with --metrics.",
                            DiagnosticSeverity.INFO,
                        ),
                    )
                if response.status_code == 400 and not model_id:
                    return MetricsResult(
                        available=False,
                        diagnostic=_diagnostic(
                            "llama_metrics_model_required",
                            "This llama-server metrics endpoint requires a model ID.",
                            DiagnosticSeverity.INFO,
                        ),
                    )
                if not 200 <= response.status_code < 300:
                    return MetricsResult(
                        available=False,
                        diagnostic=_diagnostic(
                            "llama_metrics_http_error",
                            f"llama-server /metrics returned HTTP {response.status_code}.",
                            DiagnosticSeverity.WARNING,
                            retryable=response.status_code >= 500,
                        ),
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_BODY_BYTES:
                        return MetricsResult(
                            available=False,
                            diagnostic=_diagnostic(
                                "llama_metrics_too_large",
                                "llama-server /metrics exceeded the 1 MiB response limit.",
                                DiagnosticSeverity.WARNING,
                            ),
                        )
        except httpx2.TimeoutException:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "llama_metrics_timeout",
                    "llama-server /metrics timed out.",
                    DiagnosticSeverity.WARNING,
                    retryable=True,
                ),
            )
        except httpx2.RequestError:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "llama_metrics_unreachable",
                    "llama-server /metrics could not be reached.",
                    DiagnosticSeverity.INFO,
                    retryable=True,
                ),
            )
        try:
            text_body = body.decode("utf-8")
        except UnicodeDecodeError:
            return MetricsResult(
                available=False,
                diagnostic=_diagnostic(
                    "llama_metrics_encoding_invalid",
                    "llama-server /metrics was not valid UTF-8 Prometheus text.",
                    DiagnosticSeverity.WARNING,
                ),
            )
        metrics, rejected = _metrics_from_prometheus(text_body)
        diagnostic = None
        if rejected:
            diagnostic = _diagnostic(
                "llama_metrics_samples_skipped",
                f"{rejected} malformed, non-finite, or type-mismatched metric sample(s) "
                "were skipped.",
                DiagnosticSeverity.WARNING,
            )
        elif not metrics:
            diagnostic = _diagnostic(
                "llama_metrics_no_documented_samples",
                "The metrics endpoint responded, but no metrics documented for the tested "
                "llama.cpp release were present.",
                DiagnosticSeverity.INFO,
            )
        return MetricsResult(available=True, metrics=metrics, diagnostic=diagnostic)

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        validation = await self.validate_config(config)
        if not validation.valid:
            raise ValueError("The llama.cpp profile configuration is invalid.")
        endpoint = parse_engine_endpoint(self._endpoint(config))
        extension = self._extension(config)
        settings: dict[str, JsonValue] = {
            "integration_mode": config.integration_mode.value,
            "endpoint_host": endpoint.hostname or "unknown",
            "endpoint_path": endpoint.path,
            "tested_release": LLAMA_CPP_TESTED_VERSION,
            "tested_commit": LLAMA_CPP_TESTED_COMMIT,
        }
        fingerprint_keys = {
            "model_id",
            "timeout_seconds",
            "startup_timeout_seconds",
            "context_size",
            "parallelism",
            "threads",
            "batch_size",
            "ubatch_size",
            "gpu_layers",
        }
        for key in sorted(fingerprint_keys):
            value = extension.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                settings[key] = value
        _status, props, _error = await self._get_json(config, "/props")
        engine_version = _text(props.get("build_info")) if props else None
        return RuntimeFingerprint(
            adapter_id=LLAMA_CPP_ADAPTER_ID,
            adapter_version=LLAMA_CPP_ADAPTER_VERSION,
            engine_version=engine_version,
            runtime_name="llama.cpp / llama-server",
            runtime_version=engine_version,
            settings={_EXTENSION_NAMESPACE: settings},
        )

    async def _spawn(
        self,
        arguments: list[str],
        working_directory: Path,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *arguments,
            cwd=str(working_directory),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    def _managed_arguments(self, config: AdapterConfiguration) -> tuple[list[str], Path, int]:
        extension = self._extension(config)
        parsed = parse_engine_endpoint(self._endpoint(config))
        executable = Path(cast(str, extension["executable_path"]))
        model = Path(cast(str, extension["model_path"]))
        configured_directory = _text(extension.get("working_directory"))
        working_directory = (
            Path(configured_directory) if configured_directory else executable.parent
        )
        port = parsed.port or 9931
        arguments = [
            str(executable),
            "--model",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--metrics",
        ]
        option_map = (
            ("context_size", "--ctx-size"),
            ("parallelism", "--parallel"),
            ("threads", "--threads"),
            ("batch_size", "--batch-size"),
            ("ubatch_size", "--ubatch-size"),
        )
        for key, option in option_map:
            value = extension.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                arguments.extend((option, str(value)))
        gpu_layers = extension.get("gpu_layers")
        if isinstance(gpu_layers, (int, str)) and not isinstance(gpu_layers, bool):
            arguments.extend(("--gpu-layers", str(gpu_layers)))
        # Name the served model after its file so the full local path never becomes the model id.
        model_id = _text(extension.get("model_id")) or model.stem
        arguments.extend(("--alias", model_id))
        return arguments, working_directory, port

    async def start(self, config: AdapterConfiguration) -> AdapterProcessHandle:
        if config.integration_mode is not EngineIntegrationMode.MANAGED_PROCESS:
            raise ValueError(
                "llama-server start requires an explicitly selected managed process mode."
            )
        validation = await self.validate_config(config)
        if not validation.valid:
            message = next(
                (
                    item.message
                    for item in validation.diagnostics
                    if item.severity is DiagnosticSeverity.ERROR
                ),
                "The managed llama-server profile is invalid.",
            )
            raise ValueError(message)
        arguments, working_directory, port = self._managed_arguments(config)
        if not self._port_available(port):
            raise OSError(f"The selected loopback port {port} is already in use.")
        try:
            process = await self._spawn(arguments, working_directory)
        except FileNotFoundError as error:
            raise FileNotFoundError(
                "The explicitly selected llama-server executable was not found."
            ) from error
        if process.pid is None or process.pid <= 0:
            process.terminate()
            await process.wait()
            raise RuntimeError("The managed llama-server process did not expose an OS process ID.")
        handle = AdapterProcessHandle(adapter_id=LLAMA_CPP_ADAPTER_ID)
        owned = _OwnedProcess(handle=handle, process=process, os_pid=process.pid)
        self._owned_processes[handle.process_id] = owned
        extension = self._extension(config)
        startup_timeout = extension.get("startup_timeout_seconds", 120)
        timeout = (
            float(startup_timeout)
            if isinstance(startup_timeout, (int, float))
            and _number(startup_timeout, minimum=0.1, maximum=300)
            else 120.0
        )
        try:
            ready = await self._wait_until_ready(process, port, timeout)
        except BaseException:
            self._owned_processes.pop(handle.process_id, None)
            await asyncio.shield(self._terminate_owned(owned))
            raise
        if not ready:
            self._owned_processes.pop(handle.process_id, None)
            await self._terminate_owned(owned)
            raise TimeoutError(
                f"llama-server did not become healthy on 127.0.0.1:{port} "
                f"within {timeout:g} seconds."
            )
        return handle

    @staticmethod
    def _port_available(port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
                probe_socket.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

    async def _wait_until_ready(
        self,
        process: asyncio.subprocess.Process,
        port: int,
        timeout: float,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        url = f"http://127.0.0.1:{port}/health"
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None or process.pid is None:
                return False
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                client = await self._get_client()
                async with client.stream(
                    "GET",
                    url,
                    timeout=min(1.0, remaining),
                ) as response:
                    if response.status_code == 200:
                        return True
                    if response.status_code not in {503}:
                        return False
            except (httpx2.TimeoutException, httpx2.RequestError):
                pass
            await asyncio.sleep(min(0.2, max(0.0, remaining)))
        return False

    async def stop(self, process: AdapterProcessHandle) -> None:
        owned = self._owned_processes.get(process.process_id)
        if (
            owned is None
            or owned.handle.ownership_token != process.ownership_token
            or owned.handle.adapter_id != process.adapter_id
            or process.adapter_id != LLAMA_CPP_ADAPTER_ID
            or owned.process.pid != owned.os_pid
        ):
            raise RuntimeError(
                "The process handle is not recorded as owned by this adapter instance."
            )
        await self._terminate_owned(owned)
        self._owned_processes.pop(process.process_id, None)

    @staticmethod
    async def _terminate_owned(owned: _OwnedProcess) -> None:
        process = owned.process
        if process.pid != owned.os_pid:
            raise RuntimeError("The owned llama-server process identity changed unexpectedly.")
        if process.returncode is not None:
            await process.wait()
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=_STOP_TIMEOUT_SECONDS)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def close(self) -> None:
        errors: list[Exception] = []
        for process_id, owned in list(self._owned_processes.items()):
            try:
                await self._terminate_owned(owned)
            except Exception as error:
                errors.append(error)
            finally:
                self._owned_processes.pop(process_id, None)
        try:
            await self._openai.close()
        except Exception as error:
            errors.append(error)
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as error:
                errors.append(error)
            self._client = None
        self._closed = True
        if errors:
            raise ExceptionGroup("llama.cpp adapter failed to close cleanly", errors)
