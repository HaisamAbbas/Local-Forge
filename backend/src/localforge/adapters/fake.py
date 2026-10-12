"""Deterministic in-process adapter used by ordinary checks and local demos."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

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
    StreamEventType,
    UsageCounts,
    capability,
)
from localforge.core.models import (
    CapabilitySupport,
    CapabilityValue,
    EngineIntegrationMode,
    JsonValue,
    MetricSource,
)

FAKE_ADAPTER_ID = "localforge.fake"
FAKE_ADAPTER_VERSION = "1.0.0"
FAKE_CAPABILITIES = frozenset(
    {
        "chat_completion",
        "raw_completion",
        "streaming",
        "cancellation",
        "usage_counts",
        "health",
        "model_inventory",
        "native_metrics",
        "server_ttft",
        "server_tpot",
        "server_itl",
        "cache_utilization",
        "kv_cache_block_count",
        "prefix_caching",
        "cache_hit_count",
        "continuous_batching",
        "server_slot_count",
        "quantization_metadata",
        "embeddings",
        "reranking",
        "image_input",
        "video_input",
        "audio_input",
        "tool_calling",
        "multi_gpu",
        "distributed_serving",
        "managed_process",
        "process_logs",
        "platform.windows",
        "platform.linux",
        "platform.macos",
    }
)


class FakeStreamFailure(StrEnum):
    ERROR = "error"
    DISCONNECTED = "disconnected"
    TIMEOUT = "timeout"


@dataclass
class FakeEngineScenario:
    """Controls timing and outcomes without wall-clock sleeps in ordinary tests."""

    chunks: tuple[str, ...] = ("deterministic", " fake", " response")
    first_token_gate: asyncio.Event | None = None
    chunk_gates: dict[int, asyncio.Event] = field(default_factory=dict)
    failure: FakeStreamFailure | None = None
    failure_after_chunks: int = 0
    missing_usage: bool = False
    native_metrics: bool = False
    probe_gate: asyncio.Event | None = None
    probe_started: asyncio.Event = field(default_factory=asyncio.Event)
    probe_failure: str | None = None
    probe_failure_code: str = "fake_probe_failed"
    model_inventory_failure: str | None = None
    model_metadata: dict[str, JsonValue] = field(default_factory=dict)
    streaming_probe_failure: str | None = None
    stream_started: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if self.failure_after_chunks < 0 or self.failure_after_chunks > len(self.chunks):
            raise ValueError("failure_after_chunks must be within the configured chunk range")


class AdapterClosedError(RuntimeError):
    """Raised when an adapter is used after close."""


class AdapterOperationUnsupported(RuntimeError):
    """Raised when an adapter operation is not supported for this target."""


class AdapterProcessOwnershipError(RuntimeError):
    """Raised when stop is asked to target a process not owned by this adapter."""


class FakeEngineAdapter:
    """A model-free adapter that emits repeatable probe, stream, and metric results."""

    def __init__(
        self,
        scenario: FakeEngineScenario | None = None,
        *,
        adapter_version: str = FAKE_ADAPTER_VERSION,
        protocol_version: int = 1,
        extra_capability_claims: dict[str, CapabilityValue] | None = None,
    ) -> None:
        self.scenario = scenario or FakeEngineScenario()
        self._adapter_version = adapter_version
        self._protocol_version = protocol_version
        self._extra_capability_claims = extra_capability_claims or {}
        self._active_requests: dict[UUID, asyncio.Event] = {}
        self._owned_processes: dict[UUID, AdapterProcessHandle] = {}
        self._requests_total = 0
        self._closed = False

    @property
    def active_request_ids(self) -> frozenset[UUID]:
        return frozenset(self._active_requests)

    @property
    def owned_process_handles(self) -> tuple[AdapterProcessHandle, ...]:
        return tuple(self._owned_processes.values())

    def describe(self) -> AdapterManifest:
        return AdapterManifest(
            protocol_version=self._protocol_version,
            adapter_id=FAKE_ADAPTER_ID,
            adapter_version=self._adapter_version,
            display_name="Deterministic Fake Engine",
            description="A local fixture for exercising LOCALFORGE workflows without a model.",
            category=AdapterCategory.TEST_FIXTURE,
            integration_modes=[
                EngineIntegrationMode.ATTACHED_ENDPOINT,
                EngineIntegrationMode.MANAGED_PROCESS,
            ],
            operating_systems=list(AdapterPlatform),
            declared_capabilities=set(FAKE_CAPABILITIES),
            supported_transports=["in_process_fake"],
            native_metrics=["fake.requests_total"],
            config_schema={
                "type": "object",
                "properties": {
                    "display_name": {"type": "string"},
                    "endpoint_url": {"type": "string"},
                    "health_route": {"type": "string"},
                    "models_route": {"type": "string"},
                },
                "additionalProperties": False,
            },
            known_limitations=[
                "Does not load or evaluate a language model.",
                "Timings describe the fixture path and are not inference performance results.",
            ],
            documentation_url=None,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise AdapterClosedError("fake adapter is closed")

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        self._ensure_open()
        diagnostics: list[AdapterDiagnostic] = []
        unknown_keys = set(config.values) - {
            "display_name",
            "endpoint_url",
            "health_route",
            "models_route",
        }
        if unknown_keys:
            unknown_key_list = ", ".join(sorted(unknown_keys))
            diagnostics.append(
                AdapterDiagnostic(
                    code="unknown_config_field",
                    message=f"Unsupported fake adapter settings: {unknown_key_list}.",
                    severity=DiagnosticSeverity.ERROR,
                )
            )
        return ConfigValidationResult(valid=not diagnostics, diagnostics=diagnostics)

    def _probe_capabilities(self) -> dict[str, CapabilityValue]:
        unknown = CapabilitySupport.UNKNOWN
        capabilities: dict[str, CapabilityValue] = {
            key: capability(unknown, source="fake_probe") for key in FAKE_CAPABILITIES
        }
        for key in (
            "chat_completion",
            "streaming",
            "cancellation",
            "health",
            "model_inventory",
            "managed_process",
            "platform.windows",
            "platform.linux",
            "platform.macos",
        ):
            capabilities[key] = capability(CapabilitySupport.SUPPORTED, source="fake_probe")
        capabilities["health"] = capability(
            CapabilitySupport.UNSUPPORTED
            if self.scenario.probe_failure
            else CapabilitySupport.SUPPORTED,
            source="fake_probe",
        )
        capabilities["model_inventory"] = capability(
            CapabilitySupport.UNKNOWN
            if self.scenario.model_inventory_failure
            else CapabilitySupport.SUPPORTED,
            details=self.scenario.model_inventory_failure,
            source="fake_probe",
        )
        capabilities["streaming"] = capability(
            CapabilitySupport.UNKNOWN
            if self.scenario.streaming_probe_failure
            else CapabilitySupport.SUPPORTED,
            details=self.scenario.streaming_probe_failure,
            source="fake_probe",
        )
        capabilities["usage_counts"] = capability(
            CapabilitySupport.UNKNOWN
            if self.scenario.missing_usage
            else CapabilitySupport.SUPPORTED,
            details="Usage counts are omitted by this scenario."
            if self.scenario.missing_usage
            else None,
            source="fake_probe",
        )
        metric_state = (
            CapabilitySupport.SUPPORTED
            if self.scenario.native_metrics
            else CapabilitySupport.UNSUPPORTED
        )
        capabilities["native_metrics"] = capability(metric_state, source="fake_probe")
        server_metric_state = (
            CapabilitySupport.SUPPORTED
            if self.scenario.native_metrics
            else CapabilitySupport.UNKNOWN
        )
        for key in ("server_ttft", "server_tpot", "server_itl"):
            capabilities[key] = capability(server_metric_state, source="fake_probe")
        capabilities.update(self._extra_capability_claims)
        return capabilities

    async def probe(self, config: AdapterConfiguration) -> ProbeResult:
        self._ensure_open()
        self.scenario.probe_started.set()
        if self.scenario.probe_gate is not None:
            await self.scenario.probe_gate.wait()
        failed = self.scenario.probe_failure is not None
        models: list[AdapterModel] = []
        diagnostics: list[AdapterDiagnostic] = []
        if not failed:
            try:
                models = await self.list_models(config)
            except AdapterOperationUnsupported as error:
                diagnostics.append(
                    AdapterDiagnostic(
                        code="model_inventory_unavailable",
                        message=str(error),
                        severity=DiagnosticSeverity.WARNING,
                    )
                )
        status = ProbeStatus.FAILED if failed else ProbeStatus.SUCCEEDED
        message = self.scenario.probe_failure or "Fake endpoint is available."
        if failed:
            diagnostics.append(
                AdapterDiagnostic(
                    code=self.scenario.probe_failure_code,
                    message=message,
                    severity=DiagnosticSeverity.ERROR,
                    retryable=True,
                )
            )
        capability_map = self._probe_capabilities()
        authentication_failed = self.scenario.probe_failure_code in {"unauthorized", "forbidden"}
        reachability_state = (
            CapabilitySupport.SUPPORTED
            if not failed or authentication_failed
            else CapabilitySupport.UNSUPPORTED
        )
        health_state = CapabilitySupport.UNSUPPORTED if failed else CapabilitySupport.SUPPORTED
        checks = {
            "endpoint_reachability": capability(reachability_state, source="fake_probe"),
            "health": capability(health_state, source="fake_probe"),
            "model_inventory": capability(
                CapabilitySupport.UNKNOWN
                if failed or self.scenario.model_inventory_failure
                else CapabilitySupport.SUPPORTED,
                details=self.scenario.model_inventory_failure,
                source="fake_probe",
            ),
            "streaming": capability(
                CapabilitySupport.UNKNOWN
                if failed or self.scenario.streaming_probe_failure
                else CapabilitySupport.SUPPORTED,
                details=self.scenario.streaming_probe_failure,
                source="fake_probe",
            ),
            "native_metrics": capability(
                CapabilitySupport.UNKNOWN
                if failed
                else CapabilitySupport.SUPPORTED
                if self.scenario.native_metrics
                else CapabilitySupport.UNSUPPORTED,
                source="fake_probe",
            ),
        }
        capabilities = AdapterCapabilities(
            adapter_id=FAKE_ADAPTER_ID,
            adapter_version=self._adapter_version,
            capabilities=capability_map,
        )
        return ProbeResult(
            adapter_id=FAKE_ADAPTER_ID,
            adapter_version=self._adapter_version,
            status=status,
            reachable=capability(reachability_state, details=message, source="fake_probe"),
            health=capability(health_state, details=message, source="fake_probe"),
            engine_version="fake-1",
            models=models,
            checks=checks,
            capabilities=capabilities,
            diagnostics=diagnostics,
        )

    async def start(self, config: AdapterConfiguration) -> AdapterProcessHandle:
        self._ensure_open()
        if config.integration_mode is not EngineIntegrationMode.MANAGED_PROCESS:
            raise AdapterOperationUnsupported("fake start requires managed_process mode")
        handle = AdapterProcessHandle(adapter_id=FAKE_ADAPTER_ID)
        self._owned_processes[handle.process_id] = handle
        return handle

    async def stop(self, process: AdapterProcessHandle) -> None:
        self._ensure_open()
        owned = self._owned_processes.get(process.process_id)
        if process.adapter_id != FAKE_ADAPTER_ID or owned != process:
            raise AdapterProcessOwnershipError(
                "refusing to stop a process not recorded as owned by this adapter instance"
            )
        del self._owned_processes[process.process_id]

    async def list_models(self, config: AdapterConfiguration) -> list[AdapterModel]:
        self._ensure_open()
        if self.scenario.model_inventory_failure is not None:
            raise AdapterOperationUnsupported(self.scenario.model_inventory_failure)
        display_name = config.values.get("display_name", "LocalForge Test Model")
        if not isinstance(display_name, str):
            display_name = "LocalForge Test Model"
        return [
            AdapterModel(
                model_ref="fake://deterministic-model",
                display_name=display_name,
                revision="fixture-1",
                metadata={"fixture": True, "weights_loaded": False} | self.scenario.model_metadata,
            )
        ]

    async def prepare_request(
        self, config: AdapterConfiguration, request: GenerationRequest
    ) -> PreparedRequest:
        self._ensure_open()
        validation = await self.validate_config(config)
        if not validation.valid:
            raise ValueError(validation.diagnostics[0].message)
        return PreparedRequest(
            model_ref=request.model_ref,
            prompt=request.prompt,
            transport="in_process_fake",
            request_metadata={"parameters": request.parameters},
        )

    async def _cancelled_while_waiting(
        self, gate: asyncio.Event | None, cancelled: asyncio.Event
    ) -> bool:
        if cancelled.is_set():
            return True
        if gate is None:
            return False
        gate_task = asyncio.create_task(gate.wait())
        cancel_task = asyncio.create_task(cancelled.wait())
        tasks = {gate_task, cancel_task}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            return cancelled.is_set()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def stream_request(self, request: PreparedRequest) -> AsyncIterator[AdapterStreamEvent]:
        self._ensure_open()

        async def events() -> AsyncIterator[AdapterStreamEvent]:
            cancelled = asyncio.Event()
            self._active_requests[request.request_id] = cancelled
            self._requests_total += 1
            self.scenario.stream_started.set()
            sequence = 0

            def event(
                event_type: StreamEventType,
                *,
                text: str | None = None,
                usage: UsageCounts | None = None,
                error_code: str | None = None,
                error_message: str | None = None,
            ) -> AdapterStreamEvent:
                nonlocal sequence
                current = AdapterStreamEvent(
                    request_id=request.request_id,
                    sequence=sequence,
                    event_type=event_type,
                    text=text,
                    usage=usage,
                    error_code=error_code,
                    error_message=error_message,
                )
                sequence += 1
                return current

            def failure_event() -> AdapterStreamEvent:
                failure_events = {
                    FakeStreamFailure.ERROR: (
                        StreamEventType.ERROR,
                        "fake_stream_error",
                        "The deterministic fake stream failed.",
                    ),
                    FakeStreamFailure.DISCONNECTED: (
                        StreamEventType.DISCONNECTED,
                        "fake_disconnect",
                        "The deterministic fake stream disconnected.",
                    ),
                    FakeStreamFailure.TIMEOUT: (
                        StreamEventType.TIMEOUT,
                        "fake_timeout",
                        "The deterministic fake stream timed out.",
                    ),
                }
                failure = self.scenario.failure
                if failure is None or failure not in failure_events:
                    raise ValueError(f"unknown fake stream failure: {failure}")
                event_type, error_code, error_message = failure_events[failure]
                return event(
                    event_type,
                    error_code=error_code,
                    error_message=error_message,
                )

            try:
                for index, chunk in enumerate(self.scenario.chunks):
                    if (
                        self.scenario.failure is not None
                        and index == self.scenario.failure_after_chunks
                    ):
                        yield failure_event()
                        return

                    gate = (
                        self.scenario.first_token_gate
                        if index == 0
                        else self.scenario.chunk_gates.get(index)
                    )
                    if await self._cancelled_while_waiting(gate, cancelled):
                        yield event(StreamEventType.CANCELLED, error_code="aborted")
                        return
                    if index == 0:
                        yield event(StreamEventType.FIRST_OUTPUT, text=chunk)
                    else:
                        yield event(StreamEventType.OUTPUT_CHUNK, text=chunk)

                if self.scenario.failure is not None and self.scenario.failure_after_chunks == len(
                    self.scenario.chunks
                ):
                    yield failure_event()
                    return
                if cancelled.is_set():
                    yield event(StreamEventType.CANCELLED, error_code="aborted")
                    return
                if not self.scenario.missing_usage:
                    yield event(
                        StreamEventType.USAGE,
                        usage=UsageCounts(input_tokens=4, output_tokens=4, exact=True),
                    )
                yield event(StreamEventType.COMPLETED)
            finally:
                self._active_requests.pop(request.request_id, None)

        return events()

    async def abort_request(self, request_id: UUID) -> AbortResult:
        self._ensure_open()
        cancelled = self._active_requests.get(request_id)
        if cancelled is None:
            return AbortResult(supported=True, accepted=False, message="request is not active")
        cancelled.set()
        return AbortResult(supported=True, accepted=True, message="request cancellation accepted")

    async def read_metrics(self, config: AdapterConfiguration) -> MetricsResult:
        self._ensure_open()
        if not self.scenario.native_metrics:
            return MetricsResult(
                available=False,
                diagnostic=AdapterDiagnostic(
                    code="metrics_unavailable",
                    message="This fake scenario does not provide native engine metrics.",
                    severity=DiagnosticSeverity.INFO,
                ),
            )
        return MetricsResult(
            available=True,
            metrics=[
                AdapterMetric(
                    metric_name="fake.requests_total",
                    value=self._requests_total,
                    unit="requests",
                    source=MetricSource.ENGINE_NATIVE,
                    kind="counter",
                    labels={"adapter": FAKE_ADAPTER_ID},
                )
            ],
        )

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        self._ensure_open()
        return RuntimeFingerprint(
            adapter_id=FAKE_ADAPTER_ID,
            adapter_version=self._adapter_version,
            engine_version="fake-1",
            runtime_name="LOCALFORGE deterministic fixture",
            runtime_version="1",
            settings={
                "integration_mode": config.integration_mode.value,
                "configured_key_count": len(config.values),
            },
        )

    async def close(self) -> None:
        if self._closed:
            return
        for cancelled in self._active_requests.values():
            cancelled.set()
        self._active_requests.clear()
        self._owned_processes.clear()
        self._closed = True
