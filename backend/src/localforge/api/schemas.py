"""Request and response schemas for local machine and engine profile routes."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from localforge.adapters.contracts import ProbeResult
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkRun,
    BenchmarkRunStatus,
    CapabilityValue,
    Dataset,
    EngineIntegrationMode,
    EngineProfileStatus,
    ExperimentGroup,
    HardwareSnapshot,
    JsonValue,
    MachineProfile,
    MetricSample,
    ModelArtifactStatus,
    PromptSizingMethod,
    ReportFormat,
    ReportMetadata,
    ReportStatus,
    RequestSample,
    RunEventRecord,
    SchemaModel,
    TrafficMode,
    Workload,
    WorkloadInputMode,
    WorkloadKind,
    WorkloadPreset,
)
from localforge.core.urls import parse_engine_endpoint
from localforge.runs import TERMINAL_RUN_STATUSES

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
    managed_process_running: bool
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
    engine_extensions: dict[str, dict[str, JsonValue]]
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


class PlaygroundRequest(SchemaModel):
    profile_id: UUID
    model_ref: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=32_000)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    temperature: float = Field(default=0.7, ge=0, le=2)
    confirm_remote_egress: bool = False


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


class BenchmarkPlanSettings(SchemaModel):
    name: str = Field(min_length=1, max_length=160)
    engine_profile_id: UUID
    machine_profile_id: UUID | None = None
    model_artifact_id: UUID | None = None
    warmup_requests: int = Field(default=0, ge=0)
    measured_requests: int | None = Field(default=100, ge=1)
    measurement_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    concurrency: int = Field(default=1, ge=1)
    timeout_seconds: float = Field(default=120, gt=0)
    retain_prompt_output: bool = False
    dataset_snapshot_hash: str | None = None


class BenchmarkPlanExport(SchemaModel):
    format_version: int = Field(default=1, ge=1, le=1)
    app_version: str = Field(min_length=1, max_length=40)
    database_schema_version: int = Field(ge=1)
    exported_at: datetime
    workload: Workload
    plan: BenchmarkPlanSettings


class ExperimentVariantInput(SchemaModel):
    name: str = Field(min_length=1, max_length=120)
    value: JsonValue


class ExperimentGroupCreate(SchemaModel):
    name: str = Field(min_length=1, max_length=160)
    baseline_plan_id: UUID
    independent_variable: str = Field(min_length=1, max_length=160)
    variants: list[ExperimentVariantInput] = Field(min_length=1, max_length=19)
    repetitions: int = Field(default=1, ge=1, le=10)
    randomize_order: bool = False
    order_seed: int = Field(default=0, ge=0, le=2**63 - 1)
    hypothesis: str | None = Field(default=None, max_length=2000)


class ExperimentGroupCreateResponse(SchemaModel):
    group: ExperimentGroup
    estimated_run_count: int = Field(ge=1, le=20)
    estimated_duration_seconds: float | None = Field(default=None, ge=0)


class ExperimentStartRequest(SchemaModel):
    confirm_estimated_runtime: bool = False


class RunComparisonRequest(SchemaModel):
    run_ids: list[UUID] = Field(min_length=2, max_length=20)
    baseline_run_id: UUID | None = None
    declared_variable: str | None = Field(default=None, max_length=160)


class ExperimentNextTrialResponse(SchemaModel):
    group: ExperimentGroup
    run: BenchmarkRun | None = None


class ReportCreate(SchemaModel):
    name: str = Field(min_length=1, max_length=160)
    filename: str | None = Field(default=None, min_length=1, max_length=160)
    format: ReportFormat
    run_ids: list[UUID] = Field(min_length=1, max_length=20)
    baseline_run_id: UUID | None = None
    experiment_group_id: UUID | None = None
    include_retained_content: bool = False


class ReportCreateResponse(SchemaModel):
    report: ReportMetadata


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


class RunCleanupRequest(SchemaModel):
    before: datetime | None = None
    statuses: list[BenchmarkRunStatus] | None = Field(default=None, max_length=10)
    keep_latest: int | None = Field(default=None, ge=0, le=1_000_000)
    dry_run: bool = True

    @field_validator("statuses")
    @classmethod
    def statuses_must_be_terminal(
        cls, value: list[BenchmarkRunStatus] | None
    ) -> list[BenchmarkRunStatus] | None:
        if value is not None and any(item not in TERMINAL_RUN_STATUSES for item in value):
            raise ValueError("cleanup can select only terminal run statuses")
        return value


class RunCleanupResponse(SchemaModel):
    run_ids: list[UUID] = Field(default_factory=list)
    count: int = Field(ge=0)
    dry_run: bool


class BenchmarkPlanCloneChanges(SchemaModel):
    model_artifact_id: UUID | None = None
    engine_profile_id: UUID | None = None
    concurrency: int | None = Field(default=None, ge=1, le=100_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    warmup_count: int | None = Field(default=None, ge=0, le=100_000)
    request_count: int | None = Field(default=None, ge=1, le=1_000_000)
    request_timeout_seconds: float | None = Field(default=None, ge=1, le=3600)

    @model_validator(mode="after")
    def only_model_and_temperature_accept_null(self) -> "BenchmarkPlanCloneChanges":
        nullable = {"model_artifact_id", "temperature"}
        null_fields = sorted(
            name for name in self.model_fields_set - nullable if getattr(self, name) is None
        )
        if null_fields:
            raise ValueError(f"these changes need a value: {', '.join(null_fields)}")
        return self


class BenchmarkPlanCloneRequest(SchemaModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    changes: BenchmarkPlanCloneChanges = Field(default_factory=BenchmarkPlanCloneChanges)


class BenchmarkPlanCloneResponse(BenchmarkPlanResponse):
    changed_fields: list[str] = Field(default_factory=list)


class ReportListItem(SchemaModel):
    id: UUID
    created_at: datetime
    title: str
    name: str
    format: ReportFormat
    status: ReportStatus
    run_ids: list[UUID]
    missing_run_ids: list[UUID]
    size_bytes: int | None
    available: bool
    filename: str | None
    content_included: bool
    error_message: str | None
    download_url: str | None


class ModelArtifactListItem(SchemaModel):
    id: UUID
    model_ref: str | None
    display_name: str
    status: ModelArtifactStatus
    engine_profile_id: UUID | None
    engine_label: str | None
    adapter_id: str | None
    format: str | None
    quantization: str | None
    size_bytes: int | None
    max_context_tokens: int | None
    revision: str | None
    tokenizer_identity: str | None
    updated_at: datetime
