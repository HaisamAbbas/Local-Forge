"""Manifest, catalog, asynchronous contract, and fake stream tests."""

import asyncio

import pytest
from pydantic import ValidationError

from localforge.adapters.catalog import (
    AdapterCapabilityClaimError,
    AdapterCatalog,
    AdapterNotFoundError,
    AdapterVersionError,
    default_adapter_catalog,
)
from localforge.adapters.contracts import (
    AdapterConfiguration,
    AdapterManifest,
    GenerationRequest,
    StreamEventType,
)
from localforge.adapters.fake import (
    FAKE_ADAPTER_ID,
    AdapterClosedError,
    FakeEngineAdapter,
    FakeEngineScenario,
    FakeStreamFailure,
)
from localforge.adapters.runner import AdapterCallRunner
from localforge.core.models import (
    CapabilitySupport,
    CapabilityValue,
    EngineIntegrationMode,
)
from tests.adapter_contract import assert_registered_adapter_contract


def _attached_config() -> AdapterConfiguration:
    return AdapterConfiguration(integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT)


def _managed_config() -> AdapterConfiguration:
    return AdapterConfiguration(integration_mode=EngineIntegrationMode.MANAGED_PROCESS)


def test_manifest_validation_and_default_catalog_listing() -> None:
    catalog = default_adapter_catalog()
    manifests = catalog.list_manifests()

    assert len(manifests) == 1
    manifest = manifests[0]
    assert isinstance(manifest, AdapterManifest)
    assert manifest.adapter_id == FAKE_ADAPTER_ID
    assert manifest.adapter_version == "1.0.0"
    assert manifest.integration_modes == [
        EngineIntegrationMode.ATTACHED_ENDPOINT,
        EngineIntegrationMode.MANAGED_PROCESS,
    ]
    assert "streaming" in manifest.declared_capabilities

    with pytest.raises(ValidationError, match="adapter_id"):
        AdapterManifest.model_validate(
            {**manifest.model_dump(), "adapter_id": "Invalid Adapter Id"}
        )
    with pytest.raises(ValidationError, match="adapter_version"):
        AdapterManifest.model_validate({**manifest.model_dump(), "adapter_version": "v1"})


def test_missing_and_old_protocol_adapters_fail_with_readable_diagnostics() -> None:
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter())

    with pytest.raises(AdapterNotFoundError, match="not installed or registered"):
        catalog.get("missing.adapter")
    with pytest.raises(AdapterVersionError, match="protocol version 0"):
        AdapterCatalog().register(FakeEngineAdapter(protocol_version=0))


def test_catalog_rejects_capabilities_the_manifest_did_not_declare() -> None:
    fake = FakeEngineAdapter(
        extra_capability_claims={
            "imaginary_gpu_magic": CapabilityValue(state=CapabilitySupport.SUPPORTED)
        }
    )
    catalog = AdapterCatalog()
    catalog.register(fake)

    with pytest.raises(AdapterCapabilityClaimError, match="undeclared capabilities"):
        asyncio.run(catalog.probe(FAKE_ADAPTER_ID, _attached_config()))


def test_unknown_capabilities_and_absent_metrics_remain_explicit() -> None:
    scenario = FakeEngineScenario(missing_usage=True)
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(scenario))

    async def run() -> None:
        probe = await catalog.probe(FAKE_ADAPTER_ID, _attached_config())
        assert probe.capabilities.capabilities["usage_counts"].state is CapabilitySupport.UNKNOWN
        assert probe.capabilities.capabilities["embeddings"].state is CapabilitySupport.UNKNOWN
        assert (
            probe.capabilities.capabilities["native_metrics"].state is CapabilitySupport.UNSUPPORTED
        )
        user_supplied = CapabilityValue(state=CapabilitySupport.USER_SUPPLIED)
        assert user_supplied.state is CapabilitySupport.USER_SUPPLIED
        metrics = await catalog.read_metrics(FAKE_ADAPTER_ID, _attached_config())
        assert not metrics.available
        assert metrics.diagnostic is not None
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        request = await catalog.prepare_request(
            FAKE_ADAPTER_ID,
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="no retained output"),
        )
        events = [event async for event in catalog.stream_request(FAKE_ADAPTER_ID, request)]
        assert StreamEventType.USAGE not in [event.event_type for event in events]
        assert events[-1].event_type is StreamEventType.COMPLETED
        await catalog.close()

    asyncio.run(run())


def test_multi_token_chunks_and_optional_native_metrics_are_normalized() -> None:
    scenario = FakeEngineScenario(chunks=("one", " two", " three"), native_metrics=True)
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(scenario))

    async def run() -> None:
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        request = await catalog.prepare_request(
            FAKE_ADAPTER_ID,
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="three tokens"),
        )
        events = [event async for event in catalog.stream_request(FAKE_ADAPTER_ID, request)]
        chunks = [
            event.text
            for event in events
            if event.event_type in {StreamEventType.FIRST_OUTPUT, StreamEventType.OUTPUT_CHUNK}
        ]
        assert chunks == ["one", " two", " three"]
        metrics = await catalog.read_metrics(FAKE_ADAPTER_ID, _attached_config())
        assert metrics.available
        assert metrics.metrics[0].metric_name == "fake.requests_total"
        await catalog.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (FakeStreamFailure.ERROR, StreamEventType.ERROR),
        (FakeStreamFailure.DISCONNECTED, StreamEventType.DISCONNECTED),
        (FakeStreamFailure.TIMEOUT, StreamEventType.TIMEOUT),
    ],
)
def test_stream_reports_error_disconnect_and_timeout(failure, expected) -> None:
    scenario = FakeEngineScenario(
        chunks=("partial", " ignored"),
        failure=failure,
        failure_after_chunks=1,
    )
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(scenario))

    async def run() -> None:
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        request = await catalog.prepare_request(
            FAKE_ADAPTER_ID,
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="test"),
        )
        events = [event async for event in catalog.stream_request(FAKE_ADAPTER_ID, request)]
        assert [event.event_type for event in events] == [StreamEventType.FIRST_OUTPUT, expected]
        assert events[1].error_code is not None
        await catalog.close()

    asyncio.run(run())


def test_delayed_first_output_is_controllable_and_does_not_block_other_tasks() -> None:
    scenario = FakeEngineScenario(first_token_gate=asyncio.Event())
    catalog = AdapterCatalog()
    adapter = FakeEngineAdapter(scenario)
    catalog.register(adapter)

    async def run() -> None:
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        request = await catalog.prepare_request(
            FAKE_ADAPTER_ID,
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="test"),
        )
        stream = catalog.stream_request(FAKE_ADAPTER_ID, request)
        first_event = asyncio.create_task(anext(stream))
        await scenario.stream_started.wait()
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=0.5)
        assert not first_event.done()
        scenario.first_token_gate.set()
        event = await first_event
        assert event.event_type is StreamEventType.FIRST_OUTPUT
        await stream.aclose()
        assert not adapter.active_request_ids
        await catalog.close()

    asyncio.run(run())


def test_slow_probe_does_not_block_unrelated_event_loop_work() -> None:
    scenario = FakeEngineScenario(probe_gate=asyncio.Event())
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(scenario))

    async def run() -> None:
        probe_task = asyncio.create_task(catalog.probe(FAKE_ADAPTER_ID, _attached_config()))
        await scenario.probe_started.wait()
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=0.5)
        assert not probe_task.done()
        scenario.probe_gate.set()
        result = await probe_task
        assert result.status.value == "succeeded"
        await catalog.close()

    asyncio.run(run())


def test_probe_failure_keeps_an_actionable_diagnostic() -> None:
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(FakeEngineScenario(probe_failure="fixture unavailable")))

    async def run() -> None:
        result = await catalog.probe(FAKE_ADAPTER_ID, _attached_config())
        assert result.status.value == "failed"
        assert result.diagnostics[0].code == "fake_probe_failed"
        assert result.diagnostics[0].message == "fixture unavailable"
        await catalog.close()

    asyncio.run(run())


def test_abort_request_cancels_a_stream_while_waiting_for_first_output() -> None:
    scenario = FakeEngineScenario(first_token_gate=asyncio.Event())
    catalog = AdapterCatalog()
    adapter = FakeEngineAdapter(scenario)
    catalog.register(adapter)

    async def run() -> None:
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        request = await catalog.prepare_request(
            FAKE_ADAPTER_ID,
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="cancel this"),
        )
        stream = catalog.stream_request(FAKE_ADAPTER_ID, request)
        next_event = asyncio.create_task(anext(stream))
        await scenario.stream_started.wait()
        aborted = await catalog.abort_request(FAKE_ADAPTER_ID, request.request_id)
        assert aborted.supported and aborted.accepted
        event = await next_event
        assert event.event_type is StreamEventType.CANCELLED
        await stream.aclose()
        assert not adapter.active_request_ids
        await catalog.close()

    asyncio.run(run())


def test_early_stream_close_releases_active_request_state() -> None:
    scenario = FakeEngineScenario(first_token_gate=asyncio.Event())
    catalog = AdapterCatalog()
    adapter = FakeEngineAdapter(scenario)
    catalog.register(adapter)

    async def run() -> None:
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        request = await catalog.prepare_request(
            FAKE_ADAPTER_ID,
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="close this stream"),
        )
        stream = catalog.stream_request(FAKE_ADAPTER_ID, request)
        pending = asyncio.create_task(anext(stream))
        await scenario.stream_started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        assert not adapter.active_request_ids
        await catalog.close()

    asyncio.run(run())


def test_managed_stop_only_targets_a_handle_owned_by_the_adapter() -> None:
    first = FakeEngineAdapter()
    second = FakeEngineAdapter()
    first_catalog = AdapterCatalog()
    second_catalog = AdapterCatalog()
    first_catalog.register(first)
    second_catalog.register(second)

    async def run() -> None:
        process = await first_catalog.start(FAKE_ADAPTER_ID, _managed_config())
        assert process in first.owned_process_handles
        with pytest.raises(RuntimeError, match="not recorded as owned"):
            await second_catalog.stop(FAKE_ADAPTER_ID, process)
        await first_catalog.stop(FAKE_ADAPTER_ID, process)
        assert not first.owned_process_handles
        await first_catalog.close()
        await second_catalog.close()

    asyncio.run(run())


def test_fake_adapter_satisfies_the_reusable_adapter_contract() -> None:
    catalog = default_adapter_catalog()

    async def run() -> None:
        await assert_registered_adapter_contract(
            catalog,
            FAKE_ADAPTER_ID,
            _attached_config(),
        )
        await catalog.close()

    asyncio.run(run())


def test_catalog_rejects_modes_the_adapter_does_not_support() -> None:
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter())
    config = AdapterConfiguration(integration_mode=EngineIntegrationMode.LIBRARY_PIPELINE)

    with pytest.raises(ValueError, match="does not support integration mode"):
        asyncio.run(catalog.validate_config(FAKE_ADAPTER_ID, config))


def test_fake_config_validation_returns_readable_field_diagnostics() -> None:
    catalog = default_adapter_catalog()
    invalid = AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values={"unexpected": True},
    )

    async def run() -> None:
        result = await catalog.validate_config(FAKE_ADAPTER_ID, invalid)
        assert not result.valid
        assert result.diagnostics[0].code == "unknown_config_field"
        assert "unexpected" in result.diagnostics[0].message
        await catalog.close()

    asyncio.run(run())


def test_runner_close_cancels_a_blocked_async_call() -> None:
    runner = AdapterCallRunner()
    started = asyncio.Event()

    async def blocked_call() -> None:
        started.set()
        await asyncio.Event().wait()

    async def run() -> None:
        call_task = asyncio.create_task(runner.invoke(blocked_call))
        await started.wait()
        await runner.close()
        with pytest.raises(asyncio.CancelledError):
            await call_task

    asyncio.run(run())


def test_close_cleans_fake_process_and_request_resources() -> None:
    scenario = FakeEngineScenario(first_token_gate=asyncio.Event())
    adapter = FakeEngineAdapter(scenario)

    async def run() -> None:
        process = await adapter.start(_managed_config())
        models = await adapter.list_models(_attached_config())
        request = await adapter.prepare_request(
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="close cleanup"),
        )
        stream = adapter.stream_request(request)
        first_event = asyncio.create_task(anext(stream))
        await scenario.stream_started.wait()
        await adapter.close()
        assert not adapter.owned_process_handles
        event = await first_event
        assert event.event_type is StreamEventType.CANCELLED
        await stream.aclose()
        assert not adapter.active_request_ids
        with pytest.raises(AdapterClosedError, match="adapter is closed"):
            await adapter.list_models(_attached_config())
        assert process.adapter_id == FAKE_ADAPTER_ID

    asyncio.run(run())
