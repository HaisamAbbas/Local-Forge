"""Manifest, catalog, asynchronous contract, and fake stream tests."""

import asyncio
import re
from pathlib import Path
from uuid import uuid4

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
    AdapterStreamEvent,
    GenerationRequest,
    PreparedRequest,
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

    assert len(manifests) == 20
    manifest = next(item for item in manifests if item.adapter_id == FAKE_ADAPTER_ID)
    assert isinstance(manifest, AdapterManifest)
    assert manifest.adapter_id == FAKE_ADAPTER_ID
    assert manifest.adapter_version == "1.0.0"
    assert manifest.integration_modes == [
        EngineIntegrationMode.ATTACHED_ENDPOINT,
        EngineIntegrationMode.MANAGED_PROCESS,
    ]
    assert "streaming" in manifest.declared_capabilities
    compatible = next(
        item for item in manifests if item.adapter_id == "localforge.openai_compatible"
    )
    assert compatible.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
    assert "managed_process" not in compatible.declared_capabilities
    llama_cpp = next(item for item in manifests if item.adapter_id == "localforge.llama_cpp")
    assert llama_cpp.integration_modes == [
        EngineIntegrationMode.ATTACHED_ENDPOINT,
        EngineIntegrationMode.MANAGED_PROCESS,
    ]
    assert "b11580" in llama_cpp.description
    assert any(
        "GGUF model files must already exist locally" in item
        for item in llama_cpp.known_limitations
    )
    ollama = next(item for item in manifests if item.adapter_id == "localforge.ollama")
    assert ollama.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
    assert ollama.operating_systems
    assert ollama.version_tested == ["0.12.0 (local API fixture)"]
    assert "server_reported_durations" in ollama.declared_capabilities
    assert not ollama.declared_capabilities.intersection({"managed_process", "cancellation"})
    vllm = next(item for item in manifests if item.adapter_id == "localforge.vllm")
    sglang = next(item for item in manifests if item.adapter_id == "localforge.sglang")
    assert vllm.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
    assert sglang.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
    assert vllm.adapter_id != sglang.adapter_id
    assert llama_cpp.version_tested == ["b11580 (local API fixture)"]
    assert compatible.operating_systems
    triton = next(item for item in manifests if item.adapter_id == "localforge.triton_tensorrt_llm")
    assert triton.implementation_status == "planned"
    assert triton.integration_modes == []
    assert triton.version_tested == []
    exllama = next(item for item in manifests if item.adapter_id == "localforge.exllamav3")
    assert exllama.implementation_status == "planned"
    assert exllama.integration_modes == []
    for adapter_id in (
        "localforge.mlx_lm",
        "localforge.mlc_llm",
        "localforge.mistral_rs",
        "localforge.openvino_genai",
        "localforge.onnxruntime_genai",
    ):
        entry = next(item for item in manifests if item.adapter_id == adapter_id)
        assert entry.implementation_status == "implemented"

    with pytest.raises(ValidationError, match="adapter_id"):
        AdapterManifest.model_validate(
            {**manifest.model_dump(), "adapter_id": "Invalid Adapter Id"}
        )
    with pytest.raises(ValidationError, match="adapter_version"):
        AdapterManifest.model_validate({**manifest.model_dump(), "adapter_version": "v1"})


def test_complete_adapter_catalog_matches_support_matrix() -> None:
    intended_adapter_ids = {
        "localforge.llama_cpp",
        "localforge.ollama",
        "localforge.vllm",
        "localforge.sglang",
        "localforge.tensorrt_llm",
        "localforge.triton_tensorrt_llm",
        "localforge.nvidia_dynamo",
        "localforge.lmdeploy",
        "localforge.lightllm",
        "localforge.tgi",
        "localforge.mlx_lm",
        "localforge.mlc_llm",
        "localforge.openvino_genai",
        "localforge.onnxruntime_genai",
        "localforge.mistral_rs",
        "localforge.exllamav3",
        "localforge.localai",
        "localforge.koboldcpp",
        "localforge.openai_compatible",
    }
    catalog = default_adapter_catalog()
    manifests = {item.adapter_id: item for item in catalog.list_manifests()}
    matrix_path = Path(__file__).resolve().parents[2] / "docs" / "connector-support-matrix.md"
    matrix_rows: dict[str, list[str]] = {}
    for line in matrix_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `localforge."):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 8, f"Malformed support-matrix row: {line}"
        adapter_id = cells[0].strip("`")
        assert adapter_id not in matrix_rows, f"Duplicate support-matrix row: {adapter_id}"
        matrix_rows[adapter_id] = cells

    expected_ids = intended_adapter_ids | {FAKE_ADAPTER_ID}
    assert set(manifests) == expected_ids
    assert set(matrix_rows) == expected_ids

    for adapter_id, manifest in manifests.items():
        _, _, status, modes, platforms, versions, docs, _ = matrix_rows[adapter_id]
        assert status == manifest.implementation_status
        assert (modes.split(", ") if modes != "none" else []) == [
            mode.value for mode in manifest.integration_modes
        ]
        assert (platforms.split(", ") if platforms != "none" else []) == [
            platform.value for platform in manifest.operating_systems
        ]
        versions = versions.strip("`")
        assert (versions.split("; ") if versions != "none" else []) == manifest.version_tested
        if manifest.documentation_url is None:
            assert docs == "none"
        else:
            match = re.fullmatch(r"\[Docs\]\((.+)\)", docs)
            assert match is not None
            assert match.group(1) == manifest.documentation_url


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


def test_catalog_rejects_prepared_requests_that_change_identity() -> None:
    class MisidentifiedAdapter(FakeEngineAdapter):
        async def prepare_request(
            self, config: AdapterConfiguration, request: GenerationRequest
        ) -> PreparedRequest:
            prepared = await super().prepare_request(config, request)
            return prepared.model_copy(update={"request_id": uuid4()})

    catalog = AdapterCatalog()
    catalog.register(MisidentifiedAdapter())

    async def run() -> None:
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        with pytest.raises(AdapterCapabilityClaimError, match="preserve the generation request"):
            await catalog.prepare_request(
                FAKE_ADAPTER_ID,
                _attached_config(),
                GenerationRequest(model_ref=models[0].model_ref, prompt="identity check"),
            )
        await catalog.close()

    asyncio.run(run())


def test_catalog_rejects_stream_events_for_a_different_request() -> None:
    class MisidentifiedEventAdapter(FakeEngineAdapter):
        def stream_request(self, request: PreparedRequest):
            async def events():
                async for event in super(MisidentifiedEventAdapter, self).stream_request(request):
                    yield event.model_copy(update={"request_id": uuid4()})

            return events()

    catalog = AdapterCatalog()
    catalog.register(MisidentifiedEventAdapter())

    async def run() -> None:
        models = await catalog.list_models(FAKE_ADAPTER_ID, _attached_config())
        prepared = await catalog.prepare_request(
            FAKE_ADAPTER_ID,
            _attached_config(),
            GenerationRequest(model_ref=models[0].model_ref, prompt="event identity check"),
        )
        with pytest.raises(AdapterCapabilityClaimError, match="different request"):
            async for _event in catalog.stream_request(FAKE_ADAPTER_ID, prepared):
                pass
        await catalog.close()

    asyncio.run(run())


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


def test_stream_runner_bounds_queue_and_cleans_blocked_producer() -> None:
    runner = AdapterCallRunner()
    event = AdapterStreamEvent(
        request_id=uuid4(),
        sequence=0,
        event_type=StreamEventType.OUTPUT_CHUNK,
        text="chunk",
    )
    produced = 0

    async def fast_stream():
        nonlocal produced
        while True:
            produced += 1
            yield event.model_copy(update={"sequence": produced - 1})

    async def run() -> None:
        stream = runner.stream(fast_stream)
        first = await anext(stream)
        assert first.sequence == 0
        await stream.aclose()
        assert produced <= 34
        assert not runner._tasks

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
