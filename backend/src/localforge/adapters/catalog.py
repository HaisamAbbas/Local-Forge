"""Adapter registry and the shared invocation path used by all connectors."""

from collections.abc import AsyncIterator
from uuid import UUID

from localforge.adapters.contracts import (
    AbortResult,
    AdapterConfiguration,
    AdapterManifest,
    AdapterModel,
    AdapterProcessHandle,
    AdapterStreamEvent,
    ConfigValidationResult,
    GenerationRequest,
    MetricsResult,
    PreparedRequest,
    ProbeResult,
    RuntimeFingerprint,
    validate_capability_claims,
)
from localforge.adapters.protocol import EngineAdapter
from localforge.adapters.runner import AdapterCallRunner
from localforge.core.models import EngineIntegrationMode

ADAPTER_PROTOCOL_VERSION = 1


class AdapterNotFoundError(LookupError):
    """Raised when a requested adapter is not present in the local catalog."""


class AdapterVersionError(RuntimeError):
    """Raised when a connector speaks an unsupported adapter protocol version."""


class AdapterCapabilityClaimError(ValueError):
    """Raised when an adapter reports an undeclared capability or identity."""


class AdapterCatalog:
    """Registry and guarded operations shared by fake and real adapters."""

    def __init__(self, runner: AdapterCallRunner | None = None) -> None:
        self._adapters: dict[str, EngineAdapter] = {}
        self._manifests: dict[str, AdapterManifest] = {}
        self._runner = runner or AdapterCallRunner()

    def register(self, adapter: EngineAdapter) -> AdapterManifest:
        manifest = adapter.describe()
        if manifest.protocol_version != ADAPTER_PROTOCOL_VERSION:
            raise AdapterVersionError(
                f"Adapter '{manifest.adapter_id}' uses protocol version "
                f"{manifest.protocol_version}; this LOCALFORGE build supports "
                f"version {ADAPTER_PROTOCOL_VERSION}. Install a compatible adapter."
            )
        if manifest.adapter_id in self._adapters:
            raise ValueError(f"adapter '{manifest.adapter_id}' is already registered")
        self._adapters[manifest.adapter_id] = adapter
        self._manifests[manifest.adapter_id] = manifest
        return manifest

    def list_manifests(self) -> list[AdapterManifest]:
        """Return registered adapters in stable id order for the catalog UI/API."""

        return [self._manifests[key] for key in sorted(self._manifests)]

    def get(self, adapter_id: str) -> EngineAdapter:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise AdapterNotFoundError(
                f"Adapter '{adapter_id}' is not installed or registered in LOCALFORGE."
            )
        return adapter

    def manifest_for(self, adapter_id: str) -> AdapterManifest:
        self.get(adapter_id)
        return self._manifests[adapter_id]

    def _validate_mode(self, adapter_id: str, config: AdapterConfiguration) -> None:
        manifest = self.manifest_for(adapter_id)
        if config.integration_mode not in manifest.integration_modes:
            raise ValueError(
                f"Adapter '{adapter_id}' does not support integration mode "
                f"'{config.integration_mode.value}'."
            )

    async def validate_config(
        self, adapter_id: str, config: AdapterConfiguration
    ) -> ConfigValidationResult:
        self._validate_mode(adapter_id, config)
        return await self._runner.invoke(self.get(adapter_id).validate_config, config)

    async def probe(self, adapter_id: str, config: AdapterConfiguration) -> ProbeResult:
        self._validate_mode(adapter_id, config)
        manifest = self.manifest_for(adapter_id)
        result = await self._runner.invoke(self.get(adapter_id).probe, config)
        if (
            result.adapter_id != manifest.adapter_id
            or result.adapter_version != manifest.adapter_version
        ):
            raise AdapterCapabilityClaimError("probe result identity does not match its manifest")
        try:
            validate_capability_claims(manifest, result.capabilities)
        except ValueError as error:
            raise AdapterCapabilityClaimError(str(error)) from error
        return result

    async def start(self, adapter_id: str, config: AdapterConfiguration) -> AdapterProcessHandle:
        self._validate_mode(adapter_id, config)
        manifest = self.manifest_for(adapter_id)
        if config.integration_mode is not EngineIntegrationMode.MANAGED_PROCESS:
            raise ValueError("adapter start requires a managed_process integration mode")
        if "managed_process" not in manifest.declared_capabilities:
            raise ValueError(f"Adapter '{adapter_id}' does not declare managed process support.")
        return await self._runner.invoke(self.get(adapter_id).start, config)

    async def stop(self, adapter_id: str, process: AdapterProcessHandle) -> None:
        adapter = self.get(adapter_id)
        if process.adapter_id != adapter_id:
            raise ValueError("process handle belongs to a different adapter")
        await self._runner.invoke(adapter.stop, process)

    async def list_models(
        self, adapter_id: str, config: AdapterConfiguration
    ) -> list[AdapterModel]:
        self._validate_mode(adapter_id, config)
        return await self._runner.invoke(self.get(adapter_id).list_models, config)

    async def prepare_request(
        self,
        adapter_id: str,
        config: AdapterConfiguration,
        request: GenerationRequest,
    ) -> PreparedRequest:
        self._validate_mode(adapter_id, config)
        return await self._runner.invoke(self.get(adapter_id).prepare_request, config, request)

    def stream_request(
        self, adapter_id: str, request: PreparedRequest
    ) -> AsyncIterator[AdapterStreamEvent]:
        return self._runner.stream(self.get(adapter_id).stream_request, request)

    async def read_metrics(self, adapter_id: str, config: AdapterConfiguration) -> MetricsResult:
        self._validate_mode(adapter_id, config)
        manifest = self.manifest_for(adapter_id)
        result = await self._runner.invoke(self.get(adapter_id).read_metrics, config)
        if result.available and "native_metrics" not in manifest.declared_capabilities:
            raise AdapterCapabilityClaimError(
                f"Adapter '{adapter_id}' returned native metrics it did not declare."
            )
        undeclared_metrics = {metric.metric_name for metric in result.metrics} - set(
            manifest.native_metrics
        )
        if undeclared_metrics:
            names = ", ".join(sorted(undeclared_metrics))
            raise AdapterCapabilityClaimError(
                f"Adapter '{adapter_id}' returned undeclared native metrics: {names}."
            )
        return result

    async def collect_fingerprint(
        self, adapter_id: str, config: AdapterConfiguration
    ) -> RuntimeFingerprint:
        self._validate_mode(adapter_id, config)
        return await self._runner.invoke(self.get(adapter_id).collect_fingerprint, config)

    async def abort_request(self, adapter_id: str, request_id: UUID) -> AbortResult:
        manifest = self.manifest_for(adapter_id)
        result = await self._runner.invoke(self.get(adapter_id).abort_request, request_id)
        if result.supported and "cancellation" not in manifest.declared_capabilities:
            raise AdapterCapabilityClaimError(
                f"Adapter '{adapter_id}' accepted cancellation it did not declare."
            )
        return result

    async def close(self) -> None:
        """Close every adapter, then cancel any outstanding adapter call tasks."""

        errors: list[Exception] = []
        for adapter_id in sorted(self._adapters):
            try:
                await self._runner.invoke(self._adapters[adapter_id].close)
            except Exception as error:
                errors.append(error)
        await self._runner.close()
        if errors:
            raise ExceptionGroup("One or more adapters failed to close cleanly", errors)


def default_adapter_catalog() -> AdapterCatalog:
    """Build the ordinary local catalog without network or model dependencies."""

    from localforge.adapters.fake import FakeEngineAdapter

    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter())
    return catalog
