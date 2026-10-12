"""Versioned domain schemas shared by the API, worker, and storage layer."""

import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from localforge.core.urls import parse_engine_endpoint

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
_EXTENSION_NAMESPACE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


def utc_now() -> datetime:
    """Return an aware UTC timestamp for persisted records."""

    return datetime.now(UTC)


class SchemaModel(BaseModel):
    """Base for strict top-level and nested contract values."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)


class DomainModel(SchemaModel):
    """Base for normalized, versioned and strict persisted domain records."""

    id: UUID = Field(default_factory=uuid4)
    schema_version: Literal[1] = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def updated_after_created(self) -> "DomainModel":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class EngineSpecificDomainModel(DomainModel):
    """Persisted engine records with an isolated adapter-owned extension namespace."""

    engine_extensions: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)

    @field_validator("engine_extensions")
    @classmethod
    def extension_data_must_be_namespaced(
        cls, value: dict[str, dict[str, JsonValue]]
    ) -> dict[str, dict[str, JsonValue]]:
        invalid = [
            namespace for namespace in value if not _EXTENSION_NAMESPACE.fullmatch(namespace)
        ]
        if invalid:
            raise ValueError("engine extension keys must use a lowercase namespace")
        return value


class CapabilitySupport(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    USER_SUPPLIED = "user_supplied"


class CapabilityValue(SchemaModel):
    state: CapabilitySupport = CapabilitySupport.UNKNOWN
    details: str | None = None
    source: str | None = None


class MachineProfileStatus(StrEnum):
    DETECTED = "detected"
    PARTIALLY_DETECTED = "partially_detected"
    MANUAL_UNVERIFIED = "manual_unverified"


class ProvenanceSource(StrEnum):
    DETECTED = "detected"
    USER_SUPPLIED = "user_supplied"
    ENGINE_REPORTED = "engine_reported"
    IMPORTED = "imported"


class FieldProvenance(SchemaModel):
    source: ProvenanceSource
    confidence: float | None = Field(default=None, ge=0, le=1)
    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC) if value is not None else None


class AcceleratorInfo(SchemaModel):
    name: str
    vendor: str | None = None
    memory_bytes: int | None = Field(default=None, ge=0)
    runtime: str | None = None
    driver_version: str | None = None


class HardwareSnapshot(SchemaModel):
    os_name: str | None = None
    os_version: str | None = None
    architecture: str | None = None
    cpu_model: str | None = None
    cpu_logical_cores: int | None = Field(default=None, ge=1)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    accelerators: list[AcceleratorInfo] | None = None


class MachineProfile(DomainModel):
    label: str = Field(min_length=1, max_length=120)
    status: MachineProfileStatus = MachineProfileStatus.MANUAL_UNVERIFIED
    host_name: str | None = None
    hardware: HardwareSnapshot | None = None
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)


class EngineIntegrationMode(StrEnum):
    ATTACHED_ENDPOINT = "attached_endpoint"
    MANAGED_PROCESS = "managed_process"
    LIBRARY_PIPELINE = "library_pipeline"
    COMPOSED_SERVING_STACK = "composed_serving_stack"


class EngineProfileStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    NEEDS_ATTENTION = "needs_attention"


class EngineProfile(EngineSpecificDomainModel):
    label: str = Field(min_length=1, max_length=120)
    adapter_id: str = Field(min_length=1, max_length=120)
    engine_display_name: str | None = None
    integration_mode: EngineIntegrationMode
    status: EngineProfileStatus = EngineProfileStatus.DISABLED
    endpoint_url: str | None = None
    health_route: str | None = None
    models_route: str | None = None
    adapter_version: str | None = None
    engine_version: str | None = None
    last_probed_at: datetime | None = None
    remote_access_enabled: bool = False
    machine_profile_id: UUID | None = None
    capability_snapshot_id: UUID | None = None
    selected_model_ref: str | None = None
    user_notes: str | None = None
    config: dict[str, JsonValue] = Field(default_factory=dict)
    secret_refs: dict[str, str] = Field(default_factory=dict)

    @field_validator("endpoint_url")
    @classmethod
    def endpoint_must_use_a_safe_url(cls, value: str | None) -> str | None:
        if value is not None:
            parse_engine_endpoint(value)
        return value

    @field_validator("last_probed_at")
    @classmethod
    def probe_timestamp_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @field_validator("secret_refs")
    @classmethod
    def credentials_must_be_opaque_references(cls, value: dict[str, str]) -> dict[str, str]:
        for key, reference in value.items():
            if not key.strip() or not reference.startswith(
                ("keyring://", "localforge-credential://")
            ):
                raise ValueError("credentials must use an OS credential-store reference")
            if len(reference) > 256 or any(character.isspace() for character in reference):
                raise ValueError("credential-store reference is invalid")
        return value

    @field_validator("config")
    @classmethod
    def config_must_not_contain_secret_values(
        cls, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        secret_keys = {
            "apikey",
            "authorization",
            "accesstoken",
            "password",
            "secret",
            "token",
            "credential",
            "credentials",
        }

        def contains_secret_key(item: JsonValue) -> bool:
            if isinstance(item, dict):
                for key, nested in item.items():
                    snake_case_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
                    key_parts = set(re.findall(r"[a-z0-9]+", snake_case_key.casefold()))
                    normalized_key = re.sub(r"[^a-z0-9]", "", snake_case_key.casefold())
                    sensitive_parts = key_parts.intersection(
                        {
                            "authorization",
                            "password",
                            "secret",
                            "token",
                            "credential",
                            "credentials",
                        }
                    )
                    api_key_parts = {"api", "key"}.issubset(key_parts)
                    auth_key_parts = {"auth", "key"}.issubset(key_parts)
                    if (
                        normalized_key in secret_keys
                        or sensitive_parts
                        or api_key_parts
                        or auth_key_parts
                        or contains_secret_key(nested)
                    ):
                        return True
            if isinstance(item, list):
                return any(contains_secret_key(nested) for nested in item)
            return False

        if contains_secret_key(value):
            raise ValueError("store credentials as secret references, not in config")
        return value


class CapabilitySnapshot(EngineSpecificDomainModel):
    adapter_id: str = Field(min_length=1, max_length=120)
    adapter_version: str = Field(min_length=1, max_length=80)
    probed_at: datetime = Field(default_factory=utc_now)
    capabilities: dict[str, CapabilityValue] = Field(default_factory=dict)

    @field_validator("probed_at")
    @classmethod
    def probed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class ModelArtifactStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNKNOWN = "unknown"


class ModelArtifact(EngineSpecificDomainModel):
    display_name: str = Field(min_length=1, max_length=240)
    engine_profile_id: UUID | None = None
    model_ref: str | None = None
    revision: str | None = None
    format: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    tokenizer_identity: str | None = None
    max_context_tokens: int | None = Field(default=None, ge=1)
    status: ModelArtifactStatus = ModelArtifactStatus.UNKNOWN


class DatasetSource(StrEnum):
    BUILT_IN_PRESET = "built_in_preset"
    IMPORTED_FILE = "imported_file"
    SYNTHETIC = "synthetic"


class DatasetStatus(StrEnum):
    READY = "ready"
    IMPORTING = "importing"
    FAILED = "failed"
    UNKNOWN = "unknown"


class DatasetFormat(StrEnum):
    JSONL = "jsonl"
    CSV = "csv"


class PromptSizingMethod(StrEnum):
    CHARACTERS = "characters"
    WORDS = "words"
    TOKENIZER = "tokenizer"


class DuplicatePolicy(StrEnum):
    KEEP = "keep"
    SKIP = "skip"
    ERROR = "error"


class Dataset(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    source: DatasetSource
    status: DatasetStatus = DatasetStatus.UNKNOWN
    description: str | None = None
    item_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    snapshot_hash: str | None = None
    content_retained: bool = False
    format: DatasetFormat | None = None
    prompt_field: str | None = Field(default=None, max_length=120)
    expected_answer_field: str | None = Field(default=None, max_length=120)
    row_limit: int | None = Field(default=None, ge=1, le=100_000)
    shuffle_seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    duplicate_policy: DuplicatePolicy | None = None
    prompt_sizing_method: PromptSizingMethod | None = None
    tokenizer_identity: str | None = Field(default=None, max_length=160)
    count_method: str | None = None
    duplicate_count: int = Field(default=0, ge=0)
    storage_ref: str | None = None
    redacted_summary: str | None = None


class WorkloadPreset(StrEnum):
    FIXED_PREFILL = "fixed_prefill"
    FIXED_DECODE = "fixed_decode"
    MIXED_CHAT = "mixed_chat"
    CONCURRENCY_SWEEP = "concurrency_sweep"
    TARGET_REQUEST_RATE = "target_request_rate"
    LONG_CONTEXT = "long_context"


class WorkloadKind(StrEnum):
    CHAT = "chat"
    COMPLETION = "completion"


class WorkloadInputMode(StrEnum):
    DATASET = "dataset"
    SYNTHETIC = "synthetic"


class WorkloadStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    INVALID = "invalid"


class TrafficMode(StrEnum):
    CLOSED_LOOP = "closed_loop"
    OPEN_LOOP = "open_loop"


class Workload(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    kind: WorkloadKind
    status: WorkloadStatus = WorkloadStatus.DRAFT
    input_mode: WorkloadInputMode
    dataset_id: UUID | None = None
    preset: WorkloadPreset | None = None
    request_count: int | None = Field(default=100, ge=1, le=1_000_000)
    duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    concurrency: int = Field(default=1, ge=1)
    concurrency_levels: list[int] = Field(default_factory=list, max_length=32)
    traffic_mode: TrafficMode = TrafficMode.CLOSED_LOOP
    arrival_rate_per_second: float | None = Field(default=None, gt=0, le=100_000)
    warmup_count: int = Field(default=0, ge=0, le=100_000)
    target_input_tokens: int | None = Field(default=None, ge=1)
    target_output_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int = Field(default=256, ge=1, le=1_000_000)
    prompt_sizing_method: PromptSizingMethod = PromptSizingMethod.CHARACTERS
    random_seed: int = Field(default=0, ge=0, le=2**63 - 1)
    sampling_config: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dataset_mode_requires_dataset(self) -> "Workload":
        if self.input_mode == WorkloadInputMode.DATASET and self.dataset_id is None:
            raise ValueError("dataset_id is required when input_mode is dataset")
        if self.input_mode == WorkloadInputMode.SYNTHETIC and self.dataset_id is not None:
            raise ValueError("dataset_id must be empty for a synthetic workload")
        if self.request_count is None and self.duration_seconds is None:
            raise ValueError("request_count or duration_seconds is required")
        if self.traffic_mode is TrafficMode.OPEN_LOOP and self.arrival_rate_per_second is None:
            raise ValueError("arrival_rate_per_second is required for open-loop traffic")
        if (
            self.traffic_mode is TrafficMode.CLOSED_LOOP
            and self.arrival_rate_per_second is not None
        ):
            raise ValueError("arrival_rate_per_second is only valid for open-loop traffic")
        if self.preset is WorkloadPreset.CONCURRENCY_SWEEP and not self.concurrency_levels:
            raise ValueError("concurrency_levels are required for a concurrency sweep")
        if self.preset is not None:
            expected_kind = {
                WorkloadPreset.FIXED_PREFILL: WorkloadKind.COMPLETION,
                WorkloadPreset.FIXED_DECODE: WorkloadKind.COMPLETION,
                WorkloadPreset.MIXED_CHAT: WorkloadKind.CHAT,
                WorkloadPreset.CONCURRENCY_SWEEP: WorkloadKind.CHAT,
                WorkloadPreset.TARGET_REQUEST_RATE: WorkloadKind.CHAT,
                WorkloadPreset.LONG_CONTEXT: WorkloadKind.COMPLETION,
            }[self.preset]
            if self.kind is not expected_kind:
                raise ValueError("workload kind does not match the selected preset")
        if (
            self.preset is WorkloadPreset.CONCURRENCY_SWEEP
            and self.traffic_mode is not TrafficMode.CLOSED_LOOP
        ):
            raise ValueError("concurrency sweeps require closed-loop traffic")
        if (
            self.preset is WorkloadPreset.TARGET_REQUEST_RATE
            and self.traffic_mode is not TrafficMode.OPEN_LOOP
        ):
            raise ValueError("target request-rate workloads require open-loop traffic")
        if any(level < 1 for level in self.concurrency_levels) or self.concurrency_levels != sorted(
            set(self.concurrency_levels)
        ):
            raise ValueError("concurrency_levels must be unique, positive, and ascending")
        return self

    @field_validator("sampling_config")
    @classmethod
    def sampling_configuration_is_safe(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        secret_names = {
            "access_token",
            "api_key",
            "api_token",
            "auth_token",
            "authorization",
            "bearer",
            "credential",
            "credentials",
            "password",
            "secret",
            "token",
        }
        normalized_secrets = {re.sub(r"[^a-z0-9]", "", name) for name in secret_names}

        def contains_secret_name(item: JsonValue) -> bool:
            if isinstance(item, dict):
                for key, nested in item.items():
                    normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
                    if normalized_key in normalized_secrets or contains_secret_name(nested):
                        return True
            if isinstance(item, list):
                return any(contains_secret_name(nested) for nested in item)
            return False

        if contains_secret_name(value):
            raise ValueError("sampling settings cannot contain credential fields")

        temperature = value.get("temperature")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or temperature < 0
        ):
            raise ValueError("temperature must be a finite non-negative number")
        top_p = value.get("top_p")
        if top_p is not None and (
            isinstance(top_p, bool)
            or not isinstance(top_p, (int, float))
            or not math.isfinite(top_p)
            or not 0 <= top_p <= 1
        ):
            raise ValueError("top_p must be a number between zero and one")
        for integer_key in ("top_k", "seed"):
            integer_value = value.get(integer_key)
            minimum = 1 if integer_key == "top_k" else 0
            if integer_value is not None and (
                isinstance(integer_value, bool)
                or not isinstance(integer_value, int)
                or integer_value < minimum
            ):
                raise ValueError(f"{integer_key} must be an integer of at least {minimum}")
        for stop_key in ("stop", "stop_sequences"):
            stop_value = value.get(stop_key)
            if stop_value is not None and not (
                isinstance(stop_value, str)
                or (
                    isinstance(stop_value, list)
                    and all(isinstance(item, str) for item in stop_value)
                )
            ):
                raise ValueError(f"{stop_key} must be text or a list of text values")

        def finite_json(item: JsonValue) -> bool:
            if isinstance(item, float):
                return math.isfinite(item)
            if isinstance(item, dict):
                return all(finite_json(nested) for nested in item.values())
            if isinstance(item, list):
                return all(finite_json(nested) for nested in item)
            return True

        if not finite_json(value):
            raise ValueError("sampling settings must contain finite numbers")
        return value


class BenchmarkPlanStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    BLOCKED = "blocked"


class BenchmarkPlan(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    status: BenchmarkPlanStatus = BenchmarkPlanStatus.DRAFT
    machine_profile_id: UUID | None = None
    engine_profile_id: UUID
    model_artifact_id: UUID | None = None
    workload_id: UUID
    warmup_requests: int = Field(default=0, ge=0)
    measured_requests: int | None = Field(default=100, ge=1)
    measurement_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    concurrency: int = Field(default=1, ge=1)
    timeout_seconds: float = Field(default=120, gt=0)
    retain_prompt_output: bool = False
    dataset_snapshot_hash: str | None = None
    configuration_snapshot: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def run_limit_must_be_present(self) -> "BenchmarkPlan":
        if self.measured_requests is None and self.measurement_duration_seconds is None:
            raise ValueError("a request limit or measurement duration is required")
        return self


class BenchmarkRunStatus(StrEnum):
    QUEUED = "queued"
    PREFLIGHT = "preflight"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class BenchmarkRun(DomainModel):
    plan_id: UUID
    status: BenchmarkRunStatus = BenchmarkRunStatus.QUEUED
    worker_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def run_timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC) if value is not None else None


class RequestOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"


class RequestSample(DomainModel):
    run_id: UUID
    sequence_number: int = Field(ge=0)
    outcome: RequestOutcome
    started_at: datetime
    completed_at: datetime | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    ttft_ms: float | None = Field(default=None, ge=0)
    inter_token_latency_ms: list[float] = Field(default_factory=list)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    prompt_reference: str | None = None
    output_reference: str | None = None
    redacted_summary: str | None = None
    content_retained: bool = False
    prompt_text: str | None = None
    output_text: str | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def sample_timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC) if value is not None else None

    @field_validator("inter_token_latency_ms")
    @classmethod
    def token_latencies_must_be_finite(cls, values: list[float]) -> list[float]:
        if any(value < 0 or not math.isfinite(value) for value in values):
            raise ValueError("token latencies must be finite and non-negative")
        return values

    @model_validator(mode="after")
    def retained_bodies_require_opt_in(self) -> "RequestSample":
        if (
            self.prompt_text is not None or self.output_text is not None
        ) and not self.content_retained:
            raise ValueError("prompt/output bodies require explicit content retention")
        return self


class MetricSource(StrEnum):
    CLIENT_MEASURED = "client_measured"
    ENGINE_NATIVE = "engine_native"
    SYSTEM_OBSERVED = "system_observed"
    DERIVED = "derived"
    USER_SUPPLIED = "user_supplied"


class MetricSample(DomainModel):
    run_id: UUID
    metric_name: str = Field(min_length=1, max_length=120)
    value: float
    unit: str = Field(min_length=1, max_length=80)
    source: MetricSource
    observed_at: datetime = Field(default_factory=utc_now)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("value")
    @classmethod
    def metric_value_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value

    @field_validator("observed_at")
    @classmethod
    def metric_timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class ExperimentGroupStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExperimentGroup(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    status: ExperimentGroupStatus = ExperimentGroupStatus.DRAFT
    run_ids: list[UUID] = Field(default_factory=list)
    description: str | None = None


class ReportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"


class ReportStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


class ReportMetadata(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    format: ReportFormat
    status: ReportStatus = ReportStatus.READY
    run_ids: list[UUID] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    export_ref: str | None = None
    redacted_summary: str | None = None

    @field_validator("generated_at")
    @classmethod
    def generated_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


DOMAIN_RECORD_MODELS = (
    MachineProfile,
    EngineProfile,
    CapabilitySnapshot,
    ModelArtifact,
    Dataset,
    Workload,
    BenchmarkPlan,
    BenchmarkRun,
    ExperimentGroup,
    ReportMetadata,
)
