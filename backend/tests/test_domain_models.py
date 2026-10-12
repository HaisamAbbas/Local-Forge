"""Validation boundaries for versioned domain contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from localforge.core.models import (
    BenchmarkPlan,
    CapabilitySnapshot,
    CapabilitySupport,
    CapabilityValue,
    EngineIntegrationMode,
    EngineProfile,
    HardwareSnapshot,
    MachineProfile,
    MetricSample,
    MetricSource,
    ModelArtifact,
    RequestSample,
    Workload,
    WorkloadInputMode,
    WorkloadKind,
)


def test_machine_profile_accepts_unknown_hardware_and_detected_boundaries() -> None:
    unknown = MachineProfile(label="Remote workstation")
    detected = MachineProfile(
        label="Local workstation",
        hardware=HardwareSnapshot(cpu_logical_cores=1, memory_total_bytes=0),
    )

    assert unknown.hardware is None
    assert unknown.schema_version == 1
    assert detected.hardware is not None
    assert detected.hardware.cpu_logical_cores == 1
    assert detected.hardware.memory_total_bytes == 0


def test_required_fields_invalid_enums_and_numeric_boundaries_are_rejected() -> None:
    with pytest.raises(ValidationError):
        MachineProfile()
    with pytest.raises(ValidationError):
        ModelArtifact(display_name="Example", status="installed")
    with pytest.raises(ValidationError):
        HardwareSnapshot(cpu_logical_cores=0)
    with pytest.raises(ValidationError):
        BenchmarkPlan(name="Incomplete", workload_id="0191f8c6-26a3-7c9f-9a23-f0155b84732c")


def test_unknown_capability_is_preserved_and_engine_extensions_are_namespaced() -> None:
    engine = EngineProfile(
        label="Local fake",
        adapter_id="fake",
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        engine_extensions={"fake.adapter": {"trace_mode": "deterministic"}},
    )
    capability = CapabilityValue(state=CapabilitySupport.UNKNOWN)

    assert engine.engine_extensions["fake.adapter"]["trace_mode"] == "deterministic"
    assert capability.state is CapabilitySupport.UNKNOWN
    with pytest.raises(ValidationError, match="lowercase namespace"):
        CapabilitySnapshot(
            adapter_id="fake",
            adapter_version="1",
            engine_extensions={"Invalid Namespace": {"field": "value"}},
        )
    with pytest.raises(ValidationError, match="lowercase namespace"):
        EngineProfile(
            label="Invalid extension",
            adapter_id="fake",
            integration_mode="attached_endpoint",
            engine_extensions={"Fake Adapter": {"field": "value"}},
        )
    with pytest.raises(ValidationError, match="secret references"):
        EngineProfile(
            label="Unsafe config",
            adapter_id="fake",
            integration_mode="attached_endpoint",
            config={"nested": {"apiKey": "plaintext"}},
        )
    with pytest.raises(ValidationError, match="secret references"):
        EngineProfile(
            label="Unsafe API token config",
            adapter_id="fake",
            integration_mode="attached_endpoint",
            config={"api_token": "plaintext"},
        )


def test_schema_versions_reject_unknown_contract_revisions() -> None:
    with pytest.raises(ValidationError, match="Input should be 1"):
        MachineProfile(label="Future profile", schema_version=2)


def test_engine_endpoint_schemes_and_secret_references_are_safe() -> None:
    remote = EngineProfile(
        label="Remote endpoint",
        adapter_id="fake",
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        endpoint_url="https://inference.example/v1",
    )
    local = EngineProfile(
        label="Local endpoint",
        adapter_id="fake",
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        endpoint_url="http://127.0.0.1:11434",
        secret_refs={"api_token": "keyring://localforge/test-api"},
    )

    assert remote.remote_access_enabled is False
    assert local.secret_refs["api_token"] == "keyring://localforge/test-api"
    with pytest.raises(ValidationError, match="HTTPS"):
        EngineProfile(
            label="Insecure remote endpoint",
            adapter_id="fake",
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            endpoint_url="http://inference.example/v1",
        )
    with pytest.raises(ValidationError, match="embedded credentials"):
        EngineProfile(
            label="Credential in URL",
            adapter_id="fake",
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            endpoint_url="https://user:password@inference.example/v1",
        )
    with pytest.raises(ValidationError, match="query parameters"):
        EngineProfile(
            label="Secret in query",
            adapter_id="fake",
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            endpoint_url="https://inference.example/v1?api_key=secret-value",
        )
    with pytest.raises(ValidationError, match="credential-store reference"):
        EngineProfile(
            label="Plain credential",
            adapter_id="fake",
            integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
            secret_refs={"api_token": "plain-text-secret"},
        )


def test_workload_requires_a_dataset_only_for_dataset_mode() -> None:
    with pytest.raises(ValidationError, match="dataset_id is required"):
        Workload(
            name="Dataset run",
            kind=WorkloadKind.CHAT,
            input_mode=WorkloadInputMode.DATASET,
            request_count=1,
        )

    synthetic = Workload(
        name="Synthetic run",
        kind=WorkloadKind.COMPLETION,
        input_mode=WorkloadInputMode.SYNTHETIC,
        request_count=1,
        concurrency=1,
    )
    assert synthetic.dataset_id is None


def test_metric_contract_preserves_units_sources_timestamps_and_labels() -> None:
    observed_at = datetime.fromisoformat("2026-10-12T14:00:00+02:00")
    expected_utc = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    sample = MetricSample(
        run_id="0191f8c6-26a3-7c9f-9a23-f0155b84732c",
        metric_name="time_to_first_token",
        value=12.5,
        unit="ms",
        source=MetricSource.CLIENT_MEASURED,
        observed_at=observed_at,
        labels={"phase": "measured", "engine": "fake"},
    )

    assert sample.value == 12.5
    assert sample.unit == "ms"
    assert sample.source is MetricSource.CLIENT_MEASURED
    assert sample.observed_at == expected_utc
    assert sample.labels == {"phase": "measured", "engine": "fake"}
    with pytest.raises(ValidationError, match="finite"):
        MetricSample(
            run_id=sample.run_id,
            metric_name="bad",
            value=float("inf"),
            unit="ms",
            source=MetricSource.ENGINE_NATIVE,
        )


def test_request_content_requires_a_retention_flag_and_timezone_aware_time() -> None:
    shared = {
        "run_id": "0191f8c6-26a3-7c9f-9a23-f0155b84732c",
        "sequence_number": 0,
        "outcome": "succeeded",
    }
    with pytest.raises(ValidationError, match="explicit content retention"):
        RequestSample(**shared, started_at=datetime.now(UTC), prompt_text="private")
    with pytest.raises(ValidationError, match="timezone"):
        RequestSample(**shared, started_at=datetime(2026, 10, 12, 12, 0))

    redacted = RequestSample(
        **shared,
        started_at=datetime.now(UTC),
        redacted_summary="prompt and output omitted",
    )
    assert redacted.prompt_text is None
