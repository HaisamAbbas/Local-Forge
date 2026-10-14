"""Fixture-backed connector tests; no engine installation, CUDA, or model downloads."""

import asyncio
import json

import pytest

from localforge.adapters.catalog import (
    AdapterCatalog,
    AdapterNotFoundError,
    default_adapter_catalog,
)
from localforge.adapters.contracts import (
    AdapterCategory,
    AdapterConfiguration,
    GenerationRequest,
    ProbeStatus,
    StreamEventType,
)
from localforge.adapters.koboldcpp import KOBOLDCPP_ADAPTER_ID, KoboldCppEngineAdapter
from localforge.adapters.lightllm import LIGHTLLM_ADAPTER_ID, LightLlmEngineAdapter
from localforge.adapters.lmdeploy import LMDEPLOY_ADAPTER_ID, LmDeployEngineAdapter
from localforge.adapters.localai import LOCALAI_ADAPTER_ID, LocalAIEngineAdapter
from localforge.adapters.mistral_rs import MISTRAL_RS_ADAPTER_ID, MistralRsEngineAdapter
from localforge.adapters.mlc_llm import MLC_LLM_ADAPTER_ID, MlcLlmEngineAdapter
from localforge.adapters.mlx_lm import MLX_LM_ADAPTER_ID, MlxLmEngineAdapter
from localforge.adapters.nvidia_dynamo import NVIDIA_DYNAMO_ADAPTER_ID, NvidiaDynamoEngineAdapter
from localforge.adapters.onnxruntime_genai import ONNXRUNTIME_GENAI_ADAPTER_ID
from localforge.adapters.openai_compatible import AdapterOperationUnsupported
from localforge.adapters.openvino_genai import OPENVINO_GENAI_ADAPTER_ID
from localforge.adapters.prometheus import parse_engine_prometheus
from localforge.adapters.sglang import SGLANG_ADAPTER_ID, SglangEngineAdapter
from localforge.adapters.tensorrt_llm import TENSORRT_LLM_ADAPTER_ID, TensorRtLlmEngineAdapter
from localforge.adapters.tgi import TGI_ADAPTER_ID, TgiEngineAdapter
from localforge.adapters.triton import TRITON_TENSORRT_LLM_ADAPTER_ID
from localforge.adapters.vllm import VLLM_ADAPTER_ID, VllmEngineAdapter
from localforge.core.models import CapabilitySupport, EngineIntegrationMode
from tests.adapter_contract import assert_registered_adapter_contract
from tests.fixtures.serving_engine import ServingEngineServer

VLLM_METRICS = """# HELP vllm:num_requests_running Number of requests currently running.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="fixture-model"} 2
# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{le="0.1",model_name="fixture-model"} 3
vllm:time_to_first_token_seconds_sum{model_name="fixture-model"} 0.25
vllm:time_to_first_token_seconds_count{model_name="fixture-model"} 3
# HELP vllm:prompt_tokens_total Number of prompt tokens processed.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total 100
sglang:num_running_reqs 8
"""

SGLANG_METRICS = """# HELP sglang:num_running_reqs The number of running requests
# TYPE sglang:num_running_reqs gauge
sglang:num_running_reqs{model_name="fixture-model"} 2
# HELP sglang:time_to_first_token_seconds Histogram of time to first token in seconds.
# TYPE sglang:time_to_first_token_seconds histogram
sglang:time_to_first_token_seconds_bucket{le="0.1",model_name="fixture-model"} 3
sglang:time_to_first_token_seconds_sum{model_name="fixture-model"} 0.25
sglang:time_to_first_token_seconds_count{model_name="fixture-model"} 3
"""


def test_default_catalog_registers_vllm_and_sglang_as_distinct_connectors() -> None:
    async def run() -> None:
        catalog = default_adapter_catalog()
        try:
            identifiers = {item.adapter_id for item in catalog.list_manifests()}
            assert VLLM_ADAPTER_ID in identifiers
            assert SGLANG_ADAPTER_ID in identifiers
            assert VLLM_ADAPTER_ID != SGLANG_ADAPTER_ID
            assert {
                TENSORRT_LLM_ADAPTER_ID,
                LMDEPLOY_ADAPTER_ID,
                LIGHTLLM_ADAPTER_ID,
                TGI_ADAPTER_ID,
                LOCALAI_ADAPTER_ID,
                KOBOLDCPP_ADAPTER_ID,
                TRITON_TENSORRT_LLM_ADAPTER_ID,
                MLX_LM_ADAPTER_ID,
                MLC_LLM_ADAPTER_ID,
                MISTRAL_RS_ADAPTER_ID,
                NVIDIA_DYNAMO_ADAPTER_ID,
                OPENVINO_GENAI_ADAPTER_ID,
                ONNXRUNTIME_GENAI_ADAPTER_ID,
            } <= identifiers
            triton = next(
                item
                for item in catalog.list_manifests()
                if item.adapter_id == TRITON_TENSORRT_LLM_ADAPTER_ID
            )
            assert triton.implementation_status == "planned"
            assert triton.integration_modes == []
            with pytest.raises(AdapterNotFoundError):
                catalog.get(TRITON_TENSORRT_LLM_ADAPTER_ID)
        finally:
            await catalog.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("adapter_type", "adapter_id", "engine", "namespace", "version", "metrics"),
    [
        (
            TensorRtLlmEngineAdapter,
            TENSORRT_LLM_ADAPTER_ID,
            "tensorrt_llm",
            "tensorrt_llm",
            "1.3",
            "# TYPE trtllm_request_success_total counter\ntrtllm_request_success_total 4\n",
        ),
        (
            LmDeployEngineAdapter,
            LMDEPLOY_ADAPTER_ID,
            "lmdeploy",
            "lmdeploy",
            None,
            "# TYPE lmdeploy:requests_total counter\nlmdeploy:requests_total 4\n",
        ),
        (LightLlmEngineAdapter, LIGHTLLM_ADAPTER_ID, "lightllm", "lightllm", None, ""),
        (
            TgiEngineAdapter,
            TGI_ADAPTER_ID,
            "tgi",
            "tgi",
            "3.3.7",
            "# TYPE tgi_request_count counter\ntgi_request_count 4\n",
        ),
        (
            LocalAIEngineAdapter,
            LOCALAI_ADAPTER_ID,
            "localai",
            "localai",
            "4.11.0",
            "# TYPE api_call_request_count_total counter\napi_call_request_count_total 4\n",
        ),
        (
            KoboldCppEngineAdapter,
            KOBOLDCPP_ADAPTER_ID,
            "koboldcpp",
            "koboldcpp",
            "1.122.1",
            "",
        ),
        (MlxLmEngineAdapter, MLX_LM_ADAPTER_ID, "mlx_lm", "mlx_lm", None, ""),
        (MlcLlmEngineAdapter, MLC_LLM_ADAPTER_ID, "mlc_llm", "mlc_llm", None, ""),
        (
            MistralRsEngineAdapter,
            MISTRAL_RS_ADAPTER_ID,
            "mistral_rs",
            "mistral_rs",
            None,
            "# TYPE mistralrs_requests_total counter\nmistralrs_requests_total 4\n",
        ),
    ],
)
def test_additional_connectors_pass_shared_contract_and_report_fixture_coverage(
    adapter_type: type,
    adapter_id: str,
    engine: str,
    namespace: str,
    version: str | None,
    metrics: str,
) -> None:
    async def run() -> None:
        server = ServingEngineServer(engine=engine, version=version, metrics=metrics)
        await server.start()
        adapter = adapter_type()
        catalog = AdapterCatalog()
        manifest = catalog.register(adapter)
        extension: dict[str, object] = {}
        if namespace == "localai":
            extension = {"model_id": "fixture-model", "discover_backend": True}
        config = _config(server.endpoint_url, namespace, extension=extension)
        try:
            assert manifest.implementation_status == "implemented"
            assert manifest.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
            assert manifest.operating_systems
            assert manifest.documentation_url
            if adapter_id in {MLX_LM_ADAPTER_ID, MLC_LLM_ADAPTER_ID, MISTRAL_RS_ADAPTER_ID}:
                assert manifest.version_tested == []
            else:
                assert manifest.version_tested
                # Every connector keeps fixture coverage; live-verified releases are labelled.
                assert any("local API fixture" in value for value in manifest.version_tested)
                assert all(
                    "local API fixture" in value or "(live" in value
                    for value in manifest.version_tested
                )
            assert ("prometheus_text" in manifest.supported_transports) is bool(
                manifest.native_metrics
            )
            await assert_registered_adapter_contract(catalog, adapter_id, config)
            probe = await catalog.probe(adapter_id, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            assert probe.engine_version == version
            if manifest.native_metrics:
                result = await catalog.read_metrics(adapter_id, config)
                assert result.available
                assert result.metrics
                assert all(item.source.value == "engine_native" for item in result.metrics)
            else:
                result = await catalog.read_metrics(adapter_id, config)
                assert not result.available
                assert result.diagnostic is not None
                assert result.diagnostic.code == "native_metrics_unsupported"

            chat_requests = [
                request
                for request in server.requests
                if request["method"] == "POST" and request["path"] == "/v1/chat/completions"
            ]
            assert chat_requests
            assert any(request["body"].get("stream") is True for request in chat_requests)
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_mlx_lm_catalog_separates_client_and_server_platforms() -> None:
    manifest = MlxLmEngineAdapter().describe()
    assert [item.value for item in manifest.operating_systems] == ["windows", "linux", "macos"]
    assert "Apple silicon Mac" in manifest.description
    assert "health" not in manifest.declared_capabilities
    assert "version_discovery" not in manifest.declared_capabilities
    assert "native_metrics" not in manifest.declared_capabilities


def test_mlc_and_mistral_manifests_do_not_claim_unverified_versions() -> None:
    mlc = MlcLlmEngineAdapter().describe()
    mistral = MistralRsEngineAdapter().describe()
    assert mlc.version_tested == []
    assert "health" not in mlc.declared_capabilities
    assert "version_discovery" not in mlc.declared_capabilities
    assert mistral.version_tested == []
    assert "version_discovery" not in mistral.declared_capabilities


def test_localai_fingerprint_keeps_wrapper_and_verified_backend_as_separate_layers() -> None:
    async def run() -> None:
        server = ServingEngineServer(
            engine="localai",
            version="4.11.0",
            selected_backend="llama-cpp",
        )
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(LocalAIEngineAdapter())
        config = _config(
            server.endpoint_url,
            "localai",
            extension={"model_id": "fixture-model", "discover_backend": True},
        )
        try:
            fingerprint = await catalog.collect_fingerprint(LOCALAI_ADAPTER_ID, config)
            assert [
                (layer.layer_type, layer.name, layer.version) for layer in fingerprint.stack_layers
            ] == [
                ("wrapper", "LocalAI", "4.11.0"),
                ("backend", "llama-cpp", None),
            ]
            serialized = json.dumps(fingerprint.model_dump(mode="json"))
            assert 'selected_backend": "llama-cpp' in serialized
            assert "fixture-secret-value" not in serialized
            assert "fixture-admin-secret" not in serialized
            assert any(
                request["path"] == "/api/models/config-json/fixture-model"
                for request in server.requests
            )
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_localai_unknown_backend_stays_unknown_and_unsafe_model_ids_are_not_queried() -> None:
    async def run() -> None:
        server = ServingEngineServer(
            engine="localai",
            version="4.11.0",
            selected_backend=None,
            backend_status=403,
        )
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(LocalAIEngineAdapter())
        unauthorized_config = _config(
            server.endpoint_url,
            "localai",
            extension={"model_id": "fixture-model", "discover_backend": True},
        )
        unsafe_config = _config(
            server.endpoint_url,
            "localai",
            extension={"model_id": "../private", "discover_backend": True},
        )
        try:
            unauthorized = await catalog.collect_fingerprint(
                LOCALAI_ADAPTER_ID, unauthorized_config
            )
            assert [layer.layer_type for layer in unauthorized.stack_layers] == ["wrapper"]
            assert unauthorized.settings["localai"]["backend_discovery"] == "unauthorized"
            before = len(server.requests)
            unsafe = await catalog.collect_fingerprint(LOCALAI_ADAPTER_ID, unsafe_config)
            assert [layer.layer_type for layer in unsafe.stack_layers] == ["wrapper"]
            assert unsafe.settings["localai"]["backend_discovery"] == "invalid_model_id"
            assert not any(
                request["path"].startswith("/api/models/config-json/")
                for request in server.requests[before:]
            )
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_tgi_maintenance_and_managed_install_status_are_explicit() -> None:
    manifest = TgiEngineAdapter().describe()
    assert manifest.maintenance_mode
    assert not manifest.recommended_for_new_managed_install
    assert manifest.implementation_status == "implemented"
    assert manifest.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
    assert "maintenance mode" in (manifest.status_details or "").casefold()


@pytest.mark.parametrize(
    ("adapter_type", "adapter_id", "engine", "namespace"),
    [
        (TensorRtLlmEngineAdapter, TENSORRT_LLM_ADAPTER_ID, "tensorrt_llm", "tensorrt_llm"),
        (LmDeployEngineAdapter, LMDEPLOY_ADAPTER_ID, "lmdeploy", "lmdeploy"),
        (LightLlmEngineAdapter, LIGHTLLM_ADAPTER_ID, "lightllm", "lightllm"),
        (TgiEngineAdapter, TGI_ADAPTER_ID, "tgi", "tgi"),
        (LocalAIEngineAdapter, LOCALAI_ADAPTER_ID, "localai", "localai"),
        (KoboldCppEngineAdapter, KOBOLDCPP_ADAPTER_ID, "koboldcpp", "koboldcpp"),
    ],
)
def test_additional_connectors_sanitize_errors_and_bound_stream_timeouts(
    adapter_type: type,
    adapter_id: str,
    engine: str,
    namespace: str,
) -> None:
    async def run() -> None:
        failed_server = ServingEngineServer(engine=engine, chat_status=500)
        await failed_server.start()
        failed_catalog = AdapterCatalog()
        failed_catalog.register(adapter_type())
        failed_config = _config(failed_server.endpoint_url, namespace)
        try:
            prepared = await failed_catalog.prepare_request(
                adapter_id,
                failed_config,
                GenerationRequest(model_ref="fixture-model", prompt="private fixture prompt"),
            )
            events = [event async for event in failed_catalog.stream_request(adapter_id, prepared)]
            assert events[-1].event_type is StreamEventType.ERROR
            assert "fixture-secret-value" not in str([event.model_dump() for event in events])
        finally:
            await failed_catalog.close()
            await failed_server.close()

        slow_server = ServingEngineServer(engine=engine, chat_delay=0.25)
        await slow_server.start()
        slow_catalog = AdapterCatalog()
        slow_catalog.register(adapter_type())
        slow_config = _config(
            slow_server.endpoint_url,
            namespace,
            extension={"timeout_seconds": 0.1},
        )
        try:
            prepared = await slow_catalog.prepare_request(
                adapter_id,
                slow_config,
                GenerationRequest(model_ref="fixture-model", prompt="bounded fixture prompt"),
            )
            events = [event async for event in slow_catalog.stream_request(adapter_id, prepared)]
            assert events[-1].event_type is StreamEventType.TIMEOUT
            assert events[-1].error_code == "endpoint_timeout"
        finally:
            await slow_catalog.close()
            await slow_server.close()

    asyncio.run(run())


def _config(
    endpoint_url: str,
    namespace: str,
    *,
    extension: dict[str, object] | None = None,
    secret_refs: dict[str, str] | None = None,
) -> AdapterConfiguration:
    return AdapterConfiguration(
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        values={
            "endpoint_url": endpoint_url,
            "engine_extensions": {namespace: extension or {}},
        },
        secret_refs=secret_refs or {},
    )


DYNAMO_METRICS = "\n".join(
    [
        "# TYPE dynamo_frontend_requests_total counter",
        'dynamo_frontend_requests_total{model="fixture-model"} 29',
        "# TYPE dynamo_frontend_worker_active_requests gauge",
        'dynamo_frontend_worker_active_requests{worker_id="worker-a",'
        'worker_type="decode",request_phase="decode"} 2',
        'dynamo_frontend_worker_active_requests{worker_id="worker-b",'
        'worker_type="decode",request_phase="prefill"} 3',
        "# TYPE dynamo_frontend_router_worker_state gauge",
        'dynamo_frontend_router_worker_state{router_worker_id="worker-a",'
        'state="available",target_namespace="dynamo",target_component="vllm"} 1',
        'dynamo_frontend_router_worker_state{router_worker_id="worker-b",'
        'state="pending",target_namespace="dynamo",target_component="sglang"} 1',
    ]
)


def _dynamo_config(endpoint_url: str) -> AdapterConfiguration:
    return AdapterConfiguration(
        integration_mode=EngineIntegrationMode.COMPOSED_SERVING_STACK,
        values={
            "endpoint_url": endpoint_url,
            "engine_extensions": {"nvidia_dynamo": {}},
        },
    )


def test_dynamo_preserves_aggregate_metrics_and_verified_worker_topology() -> None:
    async def run() -> None:
        server = ServingEngineServer(engine="dynamo", metrics=DYNAMO_METRICS)
        await server.start()
        catalog = AdapterCatalog()
        manifest = catalog.register(NvidiaDynamoEngineAdapter())
        config = _dynamo_config(server.endpoint_url)
        try:
            assert manifest.category is AdapterCategory.SERVING_STACK
            assert manifest.integration_modes == [EngineIntegrationMode.COMPOSED_SERVING_STACK]
            assert "version_discovery" not in manifest.declared_capabilities
            await assert_registered_adapter_contract(catalog, NVIDIA_DYNAMO_ADAPTER_ID, config)

            fingerprint = await catalog.collect_fingerprint(NVIDIA_DYNAMO_ADAPTER_ID, config)
            workers = [
                layer.name for layer in fingerprint.stack_layers if layer.layer_type == "worker"
            ]
            assert workers == [
                "Dynamo worker worker-a (available) [dynamo/vllm]",
                "Dynamo worker worker-b (pending) [dynamo/sglang]",
            ]
            metrics = await catalog.read_metrics(NVIDIA_DYNAMO_ADAPTER_ID, config)
            aggregate = next(
                item
                for item in metrics.metrics
                if item.metric_name == "dynamo_frontend_requests_total"
            )
            worker_samples = [
                item
                for item in metrics.metrics
                if item.metric_name == "dynamo_frontend_worker_active_requests"
            ]
            assert aggregate.value == 29
            assert [item.value for item in worker_samples] == [2, 3]
            assert [item.labels["worker_id"] for item in worker_samples] == ["worker-a", "worker-b"]
            assert not any(
                item.metric_name == "dynamo_frontend_requests_total_worker_sum"
                for item in metrics.metrics
            )
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_dynamo_does_not_infer_workers_when_worker_inventory_metrics_are_missing() -> None:
    async def run() -> None:
        server = ServingEngineServer(
            engine="dynamo",
            metrics="\n".join(
                [
                    "# TYPE dynamo_frontend_requests_total counter",
                    "dynamo_frontend_requests_total 0",
                ]
            ),
        )
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(NvidiaDynamoEngineAdapter())
        try:
            fingerprint = await catalog.collect_fingerprint(
                NVIDIA_DYNAMO_ADAPTER_ID, _dynamo_config(server.endpoint_url)
            )
            assert [layer.layer_type for layer in fingerprint.stack_layers] == ["orchestrator"]
            assert fingerprint.settings["frontend_metrics_available"] is True
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("adapter_type", "adapter_id", "engine", "namespace", "metrics"),
    [
        (VllmEngineAdapter, VLLM_ADAPTER_ID, "vllm", "vllm", VLLM_METRICS),
        (SglangEngineAdapter, SGLANG_ADAPTER_ID, "sglang", "sglang", SGLANG_METRICS),
    ],
)
def test_connectors_pass_shared_contract_and_remain_attach_only(
    adapter_type: type[VllmEngineAdapter] | type[SglangEngineAdapter],
    adapter_id: str,
    engine: str,
    namespace: str,
    metrics: str,
) -> None:
    async def run() -> None:
        server = ServingEngineServer(engine=engine, version="0.10.2", metrics=metrics)
        await server.start()
        adapter = adapter_type()
        catalog = AdapterCatalog()
        manifest = catalog.register(adapter)
        config = _config(server.endpoint_url, namespace)
        try:
            assert manifest.adapter_id == adapter_id
            assert manifest.integration_modes == [EngineIntegrationMode.ATTACHED_ENDPOINT]
            await assert_registered_adapter_contract(catalog, adapter_id, config)
            chat_requests = [
                request
                for request in server.requests
                if request["method"] == "POST" and request["path"] == "/v1/chat/completions"
            ]
            assert chat_requests
            if namespace == "sglang":
                assert all("stream_options" not in request["body"] for request in chat_requests)
            else:
                streaming_requests = [
                    request for request in chat_requests if request["body"].get("stream") is True
                ]
                contract_request = next(
                    request
                    for request in streaming_requests
                    if request["body"]["messages"][0]["content"] == "contract check"
                )
                assert contract_request["body"].get("stream_options") == {"include_usage": True}
            with pytest.raises(
                AdapterOperationUnsupported, match="attaches to an existing endpoint"
            ):
                await adapter.start(config)
            assert all(
                request["method"] != "DELETE" and request["path"] not in {"/api/pull"}
                for request in server.requests
            )
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_vllm_probe_scopes_capabilities_to_reported_version_and_keeps_native_units() -> None:
    async def run() -> None:
        server = ServingEngineServer(engine="vllm", version="0.10.2", metrics=VLLM_METRICS)
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(VllmEngineAdapter())
        config = _config(server.endpoint_url, "vllm")
        try:
            probe = await catalog.probe(VLLM_ADAPTER_ID, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            assert probe.engine_version == "0.10.2"
            assert probe.checks["version_discovery"].state is CapabilitySupport.SUPPORTED
            assert probe.capabilities.capabilities["streaming"].source == (
                "localforge.vllm:0.10.2:live_probe"
            )
            assert (
                probe.capabilities.capabilities["usage_counts"].state is CapabilitySupport.SUPPORTED
            )
            assert probe.checks["usage_counts"].source == ("localforge.vllm:0.10.2:live_probe")
            metrics = await catalog.read_metrics(VLLM_ADAPTER_ID, config)
            assert metrics.available
            samples = {item.metric_name: item for item in metrics.metrics}
            assert samples["vllm:num_requests_running"].unit == "requests"
            assert samples["vllm:num_requests_running"].kind == "gauge"
            assert samples["vllm:time_to_first_token_seconds_sum"].unit == "seconds"
            assert samples["vllm:time_to_first_token_seconds_bucket"].unit == "observations"
            assert (
                samples["vllm:time_to_first_token_seconds_bucket"].labels[
                    "localforge_prometheus_type"
                ]
                == "histogram"
            )
            assert "sglang:num_running_reqs" not in samples
            assert all(item.source.value == "engine_native" for item in metrics.metrics)
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_sglang_uses_safe_metadata_route_and_never_calls_server_info() -> None:
    async def run() -> None:
        server = ServingEngineServer(
            engine="sglang",
            metrics=SGLANG_METRICS,
            expected_api_key="fixture-api-key",
        )
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(
            SglangEngineAdapter(credential_resolver=lambda _reference: "fixture-api-key")
        )
        config = _config(
            server.endpoint_url,
            "sglang",
            secret_refs={"api_key": "localforge-credential://serving-engine"},
        )
        try:
            probe = await catalog.probe(SGLANG_ADAPTER_ID, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            assert probe.engine_version is None
            assert probe.checks["version_discovery"].state is CapabilitySupport.UNKNOWN
            assert (
                probe.capabilities.capabilities["usage_counts"].state is CapabilitySupport.UNKNOWN
            )
            assert any(
                item.code == "server_version_not_safely_exposed" for item in probe.diagnostics
            )
            assert probe.checks["runtime_metadata"].state is CapabilitySupport.SUPPORTED
            fingerprint = await catalog.collect_fingerprint(SGLANG_ADAPTER_ID, config)
            serialized = json.dumps(fingerprint.model_dump(mode="json"))
            assert "C:\\private\\weights" not in serialized
            assert "fixture-secret-value" not in serialized
            assert "fixture-admin-secret" not in serialized
            assert all(
                request["path"] not in {"/server_info", "/get_server_info"}
                for request in server.requests
            )
            assert all(
                request["headers"].get("authorization") == "Bearer fixture-api-key"
                for request in server.requests
            )
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_sglang_version_route_is_operator_selected_and_capabilities_are_version_scoped() -> None:
    async def run() -> None:
        server = ServingEngineServer(
            engine="sglang",
            version="0.4.9",
            metrics=SGLANG_METRICS,
        )
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(SglangEngineAdapter())
        config = _config(
            server.endpoint_url,
            "sglang",
            extension={"version_route": "/version"},
        )
        try:
            probe = await catalog.probe(SGLANG_ADAPTER_ID, config)
            assert probe.engine_version == "0.4.9"
            assert probe.capabilities.capabilities["chat_completion"].source == (
                "localforge.sglang:0.4.9:live_probe"
            )
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_sglang_usage_probe_is_opt_in_and_version_scoped() -> None:
    async def run() -> None:
        server = ServingEngineServer(engine="sglang", metrics=SGLANG_METRICS)
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(SglangEngineAdapter())
        config = _config(
            server.endpoint_url,
            "sglang",
            extension={"include_usage": True},
        )
        try:
            probe = await catalog.probe(SGLANG_ADAPTER_ID, config)
            assert probe.capabilities.capabilities["usage_counts"].state is (
                CapabilitySupport.SUPPORTED
            )
            assert probe.checks["usage_counts"].source == (
                "localforge.sglang:unreported:live_probe"
            )
            usage_requests = [
                request
                for request in server.requests
                if request["method"] == "POST"
                and request["path"] == "/v1/chat/completions"
                and request["body"]["messages"][0]["content"] == "LOCALFORGE usage check. Reply OK."
            ]
            assert len(usage_requests) == 1
            assert usage_requests[0]["body"]["stream_options"] == {"include_usage": True}
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_malformed_usage_probe_response_remains_unknown() -> None:
    async def run() -> None:
        server = ServingEngineServer(engine="vllm", usage_probe_body="not-json")
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(VllmEngineAdapter())
        config = _config(server.endpoint_url, "vllm")
        try:
            probe = await catalog.probe(VLLM_ADAPTER_ID, config)
            usage = probe.capabilities.capabilities["usage_counts"]
            assert usage.state is CapabilitySupport.UNKNOWN
            assert "malformed JSON" in (usage.details or "")
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("route_key", "route_value"),
    [
        ("version_route", "/server_info"),
        ("version_route", "/get_server_info"),
        ("version_route", "/%73erver_info"),
        ("health_route", "/server_info"),
        ("metrics_route", "/get_server_info"),
        ("model_info_route", "/server_info"),
        ("models_route", "/server_info"),
    ],
)
def test_sglang_rejects_secret_exposing_routes(route_key: str, route_value: str) -> None:
    async def run() -> None:
        server = ServingEngineServer(engine="sglang", version="0.4.9")
        await server.start()
        adapter = SglangEngineAdapter()
        config = _config(
            server.endpoint_url,
            "sglang",
            extension={route_key: route_value} if route_key != "models_route" else {},
        )
        if route_key == "models_route":
            config = config.model_copy(
                update={"values": {**config.values, "models_route": route_value}}
            )
        try:
            probe = await adapter.probe(config)
            assert probe.status is ProbeStatus.FAILED
            assert probe.checks["configuration"].state is CapabilitySupport.UNKNOWN
            assert any(item.code == "route_unsafe" for item in probe.diagnostics)
            assert server.requests == []
        finally:
            await adapter.close()
            await server.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("route_key", "route_value"),
    [
        ("version_route", 7),
        ("health_route", False),
        ("metrics_route", ["metrics"]),
        ("model_info_route", {"path": "model_info"}),
        ("models_route", 12),
    ],
)
def test_non_string_endpoint_routes_are_rejected(route_key: str, route_value: object) -> None:
    async def run() -> None:
        server = ServingEngineServer(engine="vllm")
        await server.start()
        adapter = VllmEngineAdapter()
        config = _config(
            server.endpoint_url,
            "vllm",
            extension={route_key: route_value} if route_key != "models_route" else {},
        )
        if route_key == "models_route":
            config = config.model_copy(
                update={"values": {**config.values, "models_route": route_value}}
            )
        try:
            validation = await adapter.validate_config(config)
            assert not validation.valid
            assert any(item.code == "route_invalid" for item in validation.diagnostics)
            probe = await adapter.probe(config)
            assert probe.status is ProbeStatus.FAILED
            assert server.requests == []
        finally:
            await adapter.close()
            await server.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("adapter_type", "adapter_id", "engine", "namespace", "extension"),
    [
        (VllmEngineAdapter, VLLM_ADAPTER_ID, "vllm", "vllm", {}),
        (
            SglangEngineAdapter,
            SGLANG_ADAPTER_ID,
            "sglang",
            "sglang",
            {"include_usage": True},
        ),
    ],
)
def test_stream_usage_is_parsed_when_enabled_for_that_engine(
    adapter_type: type[VllmEngineAdapter] | type[SglangEngineAdapter],
    adapter_id: str,
    engine: str,
    namespace: str,
    extension: dict[str, object],
) -> None:
    async def run() -> None:
        server = ServingEngineServer(engine=engine)
        await server.start()
        catalog = AdapterCatalog()
        catalog.register(adapter_type())
        config = _config(server.endpoint_url, namespace, extension=extension)
        try:
            prepared = await catalog.prepare_request(
                adapter_id,
                config,
                GenerationRequest(model_ref="fixture-model", prompt="fixture prompt"),
            )
            events = [event async for event in catalog.stream_request(adapter_id, prepared)]
            usage = next(event for event in events if event.event_type is StreamEventType.USAGE)
            assert usage.usage is not None
            assert (usage.usage.input_tokens, usage.usage.output_tokens, usage.usage.exact) == (
                4,
                1,
                True,
            )
        finally:
            await catalog.close()
            await server.close()

    asyncio.run(run())


def test_missing_or_disabled_metrics_do_not_block_streaming_benchmark() -> None:
    async def run() -> None:
        for extension, metrics_status in (({}, 404), ({"metrics_enabled": False}, 200)):
            server = ServingEngineServer(
                engine="vllm",
                version="0.10.2",
                metrics_status=metrics_status,
                metrics=VLLM_METRICS,
            )
            await server.start()
            adapter = VllmEngineAdapter()
            config = _config(server.endpoint_url, "vllm", extension=extension)
            try:
                catalog = AdapterCatalog()
                catalog.register(adapter)
                probe = await catalog.probe(VLLM_ADAPTER_ID, config)
                assert probe.status is ProbeStatus.SUCCEEDED
                result = await catalog.read_metrics(VLLM_ADAPTER_ID, config)
                assert not result.available
                assert result.diagnostic is not None
                prepared = await catalog.prepare_request(
                    VLLM_ADAPTER_ID,
                    config,
                    GenerationRequest(model_ref="fixture-model", prompt="fixture prompt"),
                )
                events = [
                    event async for event in catalog.stream_request(VLLM_ADAPTER_ID, prepared)
                ]
                assert events[-1].event_type is StreamEventType.COMPLETED
                if extension.get("metrics_enabled") is False:
                    assert not any(request["path"] == "/metrics" for request in server.requests)
                await catalog.close()
            finally:
                await server.close()

    asyncio.run(run())


def test_native_metrics_timeout_is_optional_and_chat_stream_errors_are_sanitized() -> None:
    async def run() -> None:
        slow_metrics = ServingEngineServer(
            engine="vllm",
            version="0.10.2",
            metrics=VLLM_METRICS,
            metrics_delay=0.75,
        )
        await slow_metrics.start()
        catalog = AdapterCatalog()
        catalog.register(VllmEngineAdapter())
        config = _config(
            slow_metrics.endpoint_url,
            "vllm",
            extension={"timeout_seconds": 0.5},
        )
        try:
            probe = await catalog.probe(VLLM_ADAPTER_ID, config)
            assert probe.status is ProbeStatus.SUCCEEDED
            assert probe.checks["native_metrics"].state is CapabilitySupport.UNKNOWN
        finally:
            await catalog.close()
            await slow_metrics.close()

        failed_chat = ServingEngineServer(engine="sglang", chat_status=500)
        await failed_chat.start()
        adapter = SglangEngineAdapter()
        config = _config(failed_chat.endpoint_url, "sglang")
        try:
            prepared = await adapter.prepare_request(
                config,
                GenerationRequest(model_ref="fixture-model", prompt="private fixture prompt"),
            )
            events = [event async for event in adapter.stream_request(prepared)]
            assert events[-1].event_type is StreamEventType.ERROR
            assert "fixture-secret-value" not in str([event.model_dump() for event in events])
        finally:
            await adapter.close()
            await failed_chat.close()

    asyncio.run(run())


def test_prometheus_parser_skips_malformed_nonfinite_and_other_engine_samples() -> None:
    parsed = parse_engine_prometheus(
        """# HELP vllm:queue_depth Number of requests currently queued.
# TYPE vllm:queue_depth gauge
vllm:queue_depth{model_name="fixture",authorization="should-be-filtered"} 3
# HELP vllm:renamed_queue_depth Number of requests in the renamed queue metric.
# TYPE vllm:renamed_queue_depth gauge
vllm:renamed_queue_depth 7
# TYPE vllm:queue_without_help gauge
vllm:queue_without_help 9
# HELP vllm:output_tokens_per_second Output tokens per second.
# TYPE vllm:output_tokens_per_second gauge
vllm:output_tokens_per_second 2
# HELP vllm:legacy_quantiles Latency quantiles.
# TYPE vllm:legacy_quantiles summary
vllm:legacy_quantiles{quantile="0.9"} 2.4
vllm:broken{label="unterminated 4
vllm:nan_value NaN
sglang:num_running_reqs 99
""",
        metric_prefix="vllm:",
    )
    assert len(parsed.metrics) == 4
    samples = {item.metric_name: item for item in parsed.metrics}
    assert samples["vllm:queue_depth"].unit == "requests"
    assert samples["vllm:queue_depth"].value == 3
    assert "authorization" not in samples["vllm:queue_depth"].labels
    assert samples["vllm:renamed_queue_depth"].metric_name == "vllm:renamed_queue_depth"
    assert samples["vllm:renamed_queue_depth"].unit == "requests"
    assert samples["vllm:queue_without_help"].unit == "native"
    assert samples["vllm:output_tokens_per_second"].unit == "tokens/second"
    assert parsed.missing_definitions == 1
    assert parsed.malformed_lines == 2
    assert parsed.unsupported_samples == 1


def test_prometheus_parser_filters_other_namespaces_before_sample_limit() -> None:
    unrelated = "\n".join(f"python:collector_metric_{index} {index}" for index in range(10_000))
    parsed = parse_engine_prometheus(
        f"{unrelated}\n# TYPE vllm:num_requests_running gauge\nvllm:num_requests_running 2\n",
        metric_prefix="vllm:",
    )

    assert len(parsed.metrics) == 1
    assert parsed.metrics[0].metric_name == "vllm:num_requests_running"
    assert parsed.truncated_samples == 0


def test_prometheus_parser_reports_engine_samples_truncated_by_limit() -> None:
    samples = "\n".join(f'vllm:queue_depth{{instance="{index}"}} 1' for index in range(10_001))
    parsed = parse_engine_prometheus(
        f"# TYPE vllm:queue_depth gauge\n{samples}\n",
        metric_prefix="vllm:",
    )

    assert len(parsed.metrics) == 10_000
    assert parsed.truncated_samples == 1
