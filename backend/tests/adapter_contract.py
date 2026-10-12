"""Reusable asynchronous checks for adapters registered in the shared catalog."""

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.contracts import (
    AdapterConfiguration,
    GenerationRequest,
    StreamEventType,
)
from localforge.core.models import EngineIntegrationMode


async def assert_registered_adapter_contract(
    catalog: AdapterCatalog,
    adapter_id: str,
    config: AdapterConfiguration,
) -> None:
    """Exercise the stable adapter surface; reusable for future connector fixtures."""

    manifest = catalog.manifest_for(adapter_id)
    assert manifest.protocol_version == 1
    assert config.integration_mode in manifest.integration_modes
    assert (await catalog.validate_config(adapter_id, config)).valid

    probe = await catalog.probe(adapter_id, config)
    assert probe.adapter_id == manifest.adapter_id
    assert probe.adapter_version == manifest.adapter_version
    assert set(probe.capabilities.capabilities) <= manifest.declared_capabilities

    models = await catalog.list_models(adapter_id, config)
    assert models
    request = await catalog.prepare_request(
        adapter_id,
        config,
        GenerationRequest(model_ref=models[0].model_ref, prompt="contract check"),
    )
    events = [event async for event in catalog.stream_request(adapter_id, request)]
    assert events[0].event_type is StreamEventType.FIRST_OUTPUT
    assert events[-1].event_type is StreamEventType.COMPLETED

    metrics = await catalog.read_metrics(adapter_id, config)
    assert metrics.available or metrics.diagnostic is not None
    fingerprint = await catalog.collect_fingerprint(adapter_id, config)
    assert fingerprint.adapter_id == adapter_id

    if EngineIntegrationMode.MANAGED_PROCESS in manifest.integration_modes:
        process_config = AdapterConfiguration(
            integration_mode=EngineIntegrationMode.MANAGED_PROCESS
        )
        process = await catalog.start(adapter_id, process_config)
        await catalog.stop(adapter_id, process)
