"""Versioned schemas at the inference adapter boundary."""

import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, field_validator

from localforge.core.models import (
    CapabilitySupport,
    CapabilityValue,
    EngineIntegrationMode,
    JsonValue,
    MetricSource,
    SchemaModel,
    utc_now,
)

_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")


class AdapterCategory(StrEnum):
    NATIVE_API = "native_api"
    OPENAI_COMPATIBLE = "openai_compatible"
    MANAGED_LAUNCHER = "managed_launcher"
    LIBRARY_PIPELINE = "library_pipeline"
    SERVING_STACK = "serving_stack"
    TEST_FIXTURE = "test_fixture"


class AdapterPlatform(StrEnum):
    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"


class AdapterManifest(SchemaModel):
    """Adapter metadata and the capabilities it is permitted to report."""

    manifest_version: Literal[1] = 1
    protocol_version: int
    adapter_id: str = Field(min_length=1, max_length=120)
    adapter_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    category: AdapterCategory
    integration_modes: list[EngineIntegrationMode] = Field(min_length=1)
    operating_systems: list[AdapterPlatform] = Field(default_factory=list)
    declared_capabilities: set[str] = Field(default_factory=set)
    supported_transports: list[str] = Field(default_factory=list)
    native_metrics: list[str] = Field(default_factory=list)
    config_schema: dict[str, JsonValue] = Field(default_factory=dict)
    known_limitations: list[str] = Field(default_factory=list)
    documentation_url: str | None = None

    @field_validator("adapter_id")
    @classmethod
    def adapter_id_must_be_stable(cls, value: str) -> str:
        if not _ADAPTER_ID.fullmatch(value):
            raise ValueError("adapter_id must be lowercase and use stable identifier characters")
        return value


class AdapterConfiguration(SchemaModel):
    integration_mode: EngineIntegrationMode
    values: dict[str, JsonValue] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AdapterDiagnostic(SchemaModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1000)
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO
    retryable: bool = False


class ConfigValidationResult(SchemaModel):
    valid: bool
    diagnostics: list[AdapterDiagnostic] = Field(default_factory=list)


class AdapterModel(SchemaModel):
    model_ref: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=240)
    revision: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ProbeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AdapterCapabilities(SchemaModel):
    adapter_id: str
    adapter_version: str
    observed_at: datetime = Field(default_factory=utc_now)
    capabilities: dict[str, CapabilityValue] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class ProbeResult(SchemaModel):
    adapter_id: str
    adapter_version: str
    status: ProbeStatus
    reachable: CapabilityValue = Field(default_factory=CapabilityValue)
    health: CapabilityValue = Field(default_factory=CapabilityValue)
    engine_version: str | None = None
    models: list[AdapterModel] = Field(default_factory=list)
    checks: dict[str, CapabilityValue] = Field(default_factory=dict)
    capabilities: AdapterCapabilities
    diagnostics: list[AdapterDiagnostic] = Field(default_factory=list)
    probed_at: datetime = Field(default_factory=utc_now)

    @field_validator("probed_at")
    @classmethod
    def probed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class GenerationRequest(SchemaModel):
    """Ephemeral prompt input. The prompt is never logged by the adapter contract."""

    model_ref: str = Field(min_length=1, max_length=500)
    prompt: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=False)


class PreparedRequest(SchemaModel):
    request_id: UUID = Field(default_factory=uuid4)
    model_ref: str
    prompt: str
    transport: str
    request_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=False)


class StreamEventType(StrEnum):
    FIRST_OUTPUT = "first_output"
    OUTPUT_CHUNK = "output_chunk"
    USAGE = "usage"
    COMPLETED = "completed"
    ERROR = "error"
    DISCONNECTED = "disconnected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class UsageCounts(SchemaModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    exact: bool = False


class AdapterStreamEvent(SchemaModel):
    request_id: UUID
    sequence: int = Field(ge=0)
    event_type: StreamEventType
    text: str | None = None
    usage: UsageCounts | None = None
    native_metrics: list["AdapterMetric"] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=False)


class AbortResult(SchemaModel):
    supported: bool
    accepted: bool
    message: str | None = None


class AdapterProcessHandle(SchemaModel):
    process_id: UUID = Field(default_factory=uuid4)
    adapter_id: str
    ownership_token: UUID = Field(default_factory=uuid4)


class AdapterMetric(SchemaModel):
    metric_name: str = Field(min_length=1, max_length=120)
    value: float
    unit: str = Field(min_length=1, max_length=80)
    source: MetricSource
    kind: Literal["gauge", "counter"] = "gauge"
    labels: dict[str, str] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("value")
    @classmethod
    def metric_value_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value


class MetricsResult(SchemaModel):
    available: bool
    metrics: list[AdapterMetric] = Field(default_factory=list)
    diagnostic: AdapterDiagnostic | None = None


AdapterStreamEvent.model_rebuild()


class RuntimeFingerprint(SchemaModel):
    adapter_id: str
    adapter_version: str
    engine_version: str | None = None
    runtime_name: str | None = None
    runtime_version: str | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=utc_now)

    @field_validator("collected_at")
    @classmethod
    def collected_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


def validate_capability_claims(
    manifest: AdapterManifest, capabilities: AdapterCapabilities
) -> None:
    if capabilities.adapter_id != manifest.adapter_id:
        raise ValueError("capability result adapter_id does not match its manifest")
    if capabilities.adapter_version != manifest.adapter_version:
        raise ValueError("capability result adapter_version does not match its manifest")
    undeclared = set(capabilities.capabilities) - manifest.declared_capabilities
    if undeclared:
        names = ", ".join(sorted(undeclared))
        raise ValueError(f"adapter claimed undeclared capabilities: {names}")


def capability(
    state: CapabilitySupport,
    *,
    details: str | None = None,
    source: str | None = None,
) -> CapabilityValue:
    """Construct a state without coercing UNKNOWN to a boolean."""

    return CapabilityValue(state=state, details=details, source=source)
