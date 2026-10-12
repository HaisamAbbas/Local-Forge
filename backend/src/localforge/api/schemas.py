"""Request and response schemas for local machine and engine profile routes."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from localforge.adapters.contracts import ProbeResult
from localforge.core.models import (
    CapabilityValue,
    EngineIntegrationMode,
    EngineProfileStatus,
    HardwareSnapshot,
    JsonValue,
    MachineProfile,
    SchemaModel,
)
from localforge.core.urls import parse_engine_endpoint


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
