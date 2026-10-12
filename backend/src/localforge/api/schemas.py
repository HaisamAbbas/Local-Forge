"""Request and response schemas for local machine and engine profile routes."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from localforge.adapters.contracts import ProbeResult
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkRun,
    CapabilityValue,
    Dataset,
    EngineIntegrationMode,
    EngineProfileStatus,
    HardwareSnapshot,
    JsonValue,
    MachineProfile,
    MetricSample,
    PromptSizingMethod,
    RequestSample,
    RunEventRecord,
    SchemaModel,
    TrafficMode,
    WorkloadInputMode,
    WorkloadKind,
    WorkloadPreset,
)
from localforge.core.urls import parse_engine_endpoint

ConcurrencyLevel = Annotated[int, Field(ge=1, le=100_000)]


class LocalSessionResponse(SchemaModel):
    token: str
    header_name: str = "X-LOCALFORGE-SESSION"


class MachineProfileCreate(SchemaModel):
    label: str = Field(min_length=1, max_length=120)
    hardware: HardwareSnapshot | None = None


class MachineProfilePatch(SchemaModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    hardware: HardwareSnapshot | None = None


class EngineProfileEndpointSchema(SchemaModel):
    endpoint_url: str | None = None

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_must_be_safe(cls, value: str | None) -> str | None:
        if value is not None:
            parse_engine_endpoint(value)
        return value


class EngineProfileCreate(EngineProfileEndpointSchema):
    label: str = Field(min_length=1, max_length=120)
    adapter_id: str = Field(min_length=1, max_length=120)
    engine_display_name: str | None = Field(default=None, max_length=160)
    integration_mode: EngineIntegrationMode
    health_route: str | None = Field(default=None, max_length=500)
    models_route: str | None = Field(default=None, max_length=500)
    machine_profile_id: UUID | None = None
    selected_model_ref: str | None = Field(default=None, max_length=500)
    user_notes: str | None = Field(default=None, max_length=2000)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    engine_extensions: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)


class EngineProfilePatch(EngineProfileEndpointSchema):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    engine_display_name: str | None = Field(default=None, max_length=160)
    integration_mode: EngineIntegrationMode | None = None
    health_route: str | None = Field(default=None, max_length=500)
    models_route: str | None = Field(default=None, max_length=500)
    machine_profile_id: UUID | None = None
    selected_model_ref: str | None = Field(default=None, max_length=500)
    user_notes: str | None = Field(default=None, max_length=2000)
    config: dict[str, JsonValue] | None = None
    secret_refs: dict[str, str] | None = None
    engine_extensions: dict[str, dict[str, JsonValue]] | None = None


class EngineProfileView(SchemaModel):
    id: UUID
    label: str
    adapter_id: str
    engine_display_name: str | None
    adapter_version: str | None
    integration_mode: EngineIntegrationMode
    status: EngineProfileStatus
    endpoint_url: str | None
    is_remote: bool
    remote_access_enabled: bool
    engine_version: str | None
    last_probed_at: datetime | None
    capability_snapshot_id: UUID | None
    capabilities: dict[str, CapabilityValue] | None
    machine_profile_id: UUID | None
    selected_model_ref: str | None
    user_notes: str | None
    configured_fields: list[str]
    credential_configured: bool
    created_at: datetime
    updated_at: datetime
    schema_version: int


class EngineProbeResponse(SchemaModel):
    profile: EngineProfileView
    result: ProbeResult


class ProbeRequest(SchemaModel):
    confirm_remote_egress: bool = False


class MachineProfileView(SchemaModel):
    profile: MachineProfile
    detected: bool


class RemoteEgressDisclosure(SchemaModel):
    code: str = "remote_confirmation_required"
    host: str
    message: str
    request_will_be_sent: bool = False


class WorkloadCreate(SchemaModel):
    name: str = Field(min_length=1, max_length=160)
    kind: WorkloadKind
    input_mode: WorkloadInputMode = WorkloadInputMode.SYNTHETIC
    dataset_id: UUID | None = None
    preset: WorkloadPreset | None = None
    request_count: int | None = Field(default=100, ge=1, le=1_000_000)
    duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    concurrency: int = Field(default=1, ge=1, le=100_000)
    concurrency_levels: list[ConcurrencyLevel] = Field(default_factory=list, max_length=32)
    traffic_mode: TrafficMode = TrafficMode.CLOSED_LOOP
    arrival_rate_per_second: float | None = Field(default=None, gt=0, le=100_000)
    warmup_count: int = Field(default=0, ge=0, le=100_000)
    target_input_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    target_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    max_output_tokens: int = Field(default=256, ge=1, le=1_000_000)
    prompt_sizing_method: PromptSizingMethod = PromptSizingMethod.CHARACTERS
    random_seed: int = Field(default=0, ge=0, le=2**63 - 1)
    sampling_config: dict[str, JsonValue] = Field(default_factory=dict)


class WorkloadPresetCreate(SchemaModel):
    name: str = Field(min_length=1, max_length=160)
    preset: WorkloadPreset
    request_count: int | None = Field(default=100, ge=1, le=1_000_000)
    duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    concurrency: int = Field(default=1, ge=1, le=100_000)
    concurrency_levels: list[ConcurrencyLevel] = Field(default_factory=list, max_length=32)
    arrival_rate_per_second: float | None = Field(default=None, gt=0, le=100_000)
    warmup_count: int = Field(default=0, ge=0, le=100_000)
    max_output_tokens: int = Field(default=256, ge=1, le=1_000_000)
    random_seed: int = Field(default=0, ge=0, le=2**63 - 1)
    sampling_config: dict[str, JsonValue] = Field(default_factory=dict)


class DatasetPreview(SchemaModel):
    row_number: int = Field(ge=1)
    prompt_preview: str
    prompt_size: int | None = Field(default=None, ge=0)
    expected_answer_present: bool


class DatasetImportResponse(SchemaModel):
    dataset: Dataset
    preview: list[DatasetPreview]
    parsed_rows: int = Field(ge=0)
    selected_rows: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    rows_excluded_by_limit: int = Field(ge=0)
    count_method: str


class BenchmarkPlanCreate(SchemaModel):
    name: str = Field(min_length=1, max_length=160)
    engine_profile_id: UUID
    machine_profile_id: UUID | None = None
    model_artifact_id: UUID | None = None
    workload_id: UUID
    retain_prompt_output: bool = False


class PreflightIssue(SchemaModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1000)


class BenchmarkPreflight(SchemaModel):
    valid: bool
    target: dict[str, JsonValue]
    workload: dict[str, JsonValue] | None = None
    supported_options: list[str] = Field(default_factory=list)
    unknown_options: list[str] = Field(default_factory=list)
    unsupported_options: list[str] = Field(default_factory=list)
    missing_prerequisites: list[str] = Field(default_factory=list)
    estimated_duration_seconds: float | None = None
    estimated_request_volume: int | None = None
    resource_contention: list[str] = Field(default_factory=list)
    remote_data_egress: RemoteEgressDisclosure | None = None
    blockers: list[PreflightIssue] = Field(default_factory=list)
    warnings: list[PreflightIssue] = Field(default_factory=list)


class BenchmarkPlanResponse(SchemaModel):
    plan: BenchmarkPlan
    preflight: BenchmarkPreflight


class RunStartRequest(SchemaModel):
    plan_id: UUID
    override_contention: bool = False


class RunStartResponse(SchemaModel):
    run: BenchmarkRun
    conflicting_resources: list[str] = Field(default_factory=list)


class RunEventPage(SchemaModel):
    events: list[RunEventRecord] = Field(default_factory=list)
    next_sequence_number: int | None = None
    terminal: bool = False


class RunSamplesResponse(SchemaModel):
    samples: list[RequestSample] = Field(default_factory=list)
    next_sequence_number: int | None = None


class RunMetricsResponse(SchemaModel):
    metrics: list[MetricSample] = Field(default_factory=list)
