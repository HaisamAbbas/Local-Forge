"""Attach-only LocalAI wrapper adapter with privacy-filtered backend discovery."""

import json
import re
from typing import cast
from urllib.parse import quote

import httpx2

from localforge.adapters.contracts import (
    AdapterConfiguration,
    AdapterDiagnostic,
    AdapterManifest,
    ConfigValidationResult,
    DiagnosticSeverity,
    RuntimeFingerprint,
    RuntimeStackLayer,
)
from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    _extension,
    _safe_route,
    default_credential_resolver,
)
from localforge.core.models import JsonValue

LOCALAI_ADAPTER_ID = "localforge.localai"
LOCALAI_ADAPTER_VERSION = "1.0.0"
_BACKEND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,119}$")


class LocalAIEngineAdapter(OpenAIServingEngineAdapter):
    """Identify LocalAI separately from a selected backend exposed by its model config."""

    adapter_id = LOCALAI_ADAPTER_ID
    adapter_version = LOCALAI_ADAPTER_VERSION
    namespace = "localai"
    display_name = "LocalAI serving endpoint"
    description = (
        "Attach to LocalAI's OpenAI-compatible API. A model backend is recorded only when the "
        "selected model configuration explicitly reports it."
    )
    metrics_prefix = "api_call"
    default_health_route = None
    default_version_route = "/version"
    default_model_info_route = None
    default_include_usage = False
    docs_url = "https://localai.io/"
    default_port = 8080
    tested_versions = ("4.11.0 (local API fixture)",)
    _extension_keys = OpenAIServingEngineAdapter._extension_keys | {"discover_backend"}

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            credential_resolver=credential_resolver or default_credential_resolver(),
            transport=transport,
        )

    def describe(self) -> AdapterManifest:
        manifest = super().describe()
        schema = dict(manifest.config_schema)
        properties = cast(dict[str, JsonValue], schema["properties"])
        extensions = cast(dict[str, JsonValue], properties["engine_extensions"])
        extension_properties = cast(dict[str, JsonValue], extensions["properties"])
        localai_extension = cast(dict[str, JsonValue], extension_properties[self.namespace])
        localai_properties = cast(dict[str, JsonValue], localai_extension["properties"])
        localai_properties["discover_backend"] = {"type": "boolean", "default": True}
        localai_extension["properties"] = localai_properties
        extension_properties[self.namespace] = localai_extension
        extensions["properties"] = extension_properties
        properties["engine_extensions"] = extensions
        schema["properties"] = properties
        return manifest.model_copy(update={"config_schema": schema})

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        result = await super().validate_config(config)
        options = _extension(config, self.namespace)
        discover_backend = options.get("discover_backend")
        if discover_backend is not None and not isinstance(discover_backend, bool):
            diagnostic = AdapterDiagnostic(
                code="backend_discovery_invalid",
                message="LocalAI discover_backend must be a boolean.",
                severity=DiagnosticSeverity.ERROR,
            )
            return ConfigValidationResult(
                valid=False,
                diagnostics=[*result.diagnostics, diagnostic],
            )
        return result

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        fingerprint = await super().collect_fingerprint(config)
        options = _extension(config, self.namespace)
        selected_model = options.get("model_id")
        discover_backend = options.get("discover_backend", True)
        backend: str | None = None
        discovery_status = "not_requested"

        if discover_backend is True and isinstance(selected_model, str) and selected_model.strip():
            route = f"/api/models/config-json/{quote(selected_model.strip(), safe='')}"
            if _safe_route(route):
                status, body, _error = await self._get_payload(
                    config,
                    route,
                    max_bytes=65_536,
                )
                discovery_status = "unavailable"
                if status == 200 and body is not None:
                    try:
                        payload = json.loads(body)
                    except (ValueError, json.JSONDecodeError):
                        discovery_status = "malformed"
                    else:
                        candidate = payload.get("backend") if isinstance(payload, dict) else None
                        if isinstance(candidate, str) and _BACKEND_NAME.fullmatch(candidate):
                            backend = candidate
                            discovery_status = "verified"
                        elif isinstance(payload, dict):
                            discovery_status = "unknown"
                elif status in {401, 403}:
                    discovery_status = "unauthorized"
            else:
                discovery_status = "invalid_model_id"
        elif discover_backend is True:
            discovery_status = "no_selected_model"

        layers = [
            RuntimeStackLayer(
                layer_type="wrapper",
                name="LocalAI",
                version=fingerprint.runtime_version,
                source="engine_reported" if fingerprint.runtime_version else "verified_config",
            )
        ]
        if backend is not None:
            layers.append(
                RuntimeStackLayer(
                    layer_type="backend",
                    name=backend,
                    source="verified_config",
                )
            )

        settings = dict(fingerprint.settings)
        localai_settings = settings.get(self.namespace)
        if isinstance(localai_settings, dict):
            updated = dict(localai_settings)
            updated["selected_backend"] = backend
            updated["backend_discovery"] = discovery_status
            settings[self.namespace] = cast(JsonValue, updated)
        return fingerprint.model_copy(update={"stack_layers": layers, "settings": settings})
