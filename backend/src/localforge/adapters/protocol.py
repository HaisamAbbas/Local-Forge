"""Async adapter protocol implemented by every inference engine integration."""

from collections.abc import AsyncIterator
from typing import Protocol
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
)


class EngineAdapter(Protocol):
    """Engine boundary; implementations use async I/O and keep provider details private."""

    def describe(self) -> AdapterManifest:
        """Return the adapter's versioned manifest."""

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        """Validate a target configuration without contacting its endpoint."""

    async def probe(self, config: AdapterConfiguration) -> ProbeResult:
        """Probe target reachability, health, version, inventory, and capabilities."""

    async def start(self, config: AdapterConfiguration) -> AdapterProcessHandle:
        """Start an owned process when the selected integration mode permits it."""

    async def stop(self, process: AdapterProcessHandle) -> None:
        """Stop only a process handle created and retained by this adapter instance."""

    async def list_models(self, config: AdapterConfiguration) -> list[AdapterModel]:
        """Return model inventory when available."""

    async def prepare_request(
        self, config: AdapterConfiguration, request: GenerationRequest
    ) -> PreparedRequest:
        """Normalize a generation request for this target."""

    def stream_request(self, request: PreparedRequest) -> AsyncIterator[AdapterStreamEvent]:
        """Stream normalized output, usage, completion, and failure events."""

    async def abort_request(self, request_id: UUID) -> AbortResult:
        """Abort an active request when supported."""

    async def read_metrics(self, config: AdapterConfiguration) -> MetricsResult:
        """Read optional native counters and gauges."""

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        """Return versions and settings needed to reproduce a run."""

    async def close(self) -> None:
        """Release adapter-owned sessions and process resources."""
