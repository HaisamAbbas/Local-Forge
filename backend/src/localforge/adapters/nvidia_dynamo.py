"""Attached NVIDIA Dynamo frontend connector with verified router-worker topology."""

import httpx2

from localforge.adapters.contracts import (
    AdapterCategory,
    AdapterConfiguration,
    AdapterManifest,
    AdapterPlatform,
    ConfigValidationResult,
    RuntimeFingerprint,
    RuntimeStackLayer,
)
from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)
from localforge.core.models import EngineIntegrationMode

NVIDIA_DYNAMO_ADAPTER_ID = "localforge.nvidia_dynamo"
NVIDIA_DYNAMO_ADAPTER_VERSION = "1.0.0"
_MAX_REPORTED_WORKER_LAYERS = 31


class NvidiaDynamoEngineAdapter(OpenAIServingEngineAdapter):
    """Attach to Dynamo's HTTP frontend and preserve its distinct metric scopes."""

    adapter_id = NVIDIA_DYNAMO_ADAPTER_ID
    adapter_version = NVIDIA_DYNAMO_ADAPTER_VERSION
    namespace = "nvidia_dynamo"
    display_name = "NVIDIA Dynamo frontend and serving stack"
    description = (
        "Attach to the Dynamo OpenAI-compatible frontend. Frontend metrics and verified router "
        "worker identities remain distinct from worker scrape endpoints and backend metrics."
    )
    metrics_prefix = "dynamo_frontend_"
    default_health_route = "/health"
    default_version_route = None
    default_model_info_route = None
    default_include_usage = False
    docs_url = (
        "https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/frontend/overview"
    )
    default_port = 8000
    tested_versions: tuple[str, ...] = ()
    unsupported_capabilities = frozenset({"version_discovery"})

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            credential_resolver=credential_resolver or default_credential_resolver(),
            transport=transport,
        )

    def describe(self) -> AdapterManifest:
        manifest = super().describe()
        return manifest.model_copy(
            update={
                "category": AdapterCategory.SERVING_STACK,
                "integration_modes": [EngineIntegrationMode.COMPOSED_SERVING_STACK],
                "operating_systems": [
                    AdapterPlatform.WINDOWS,
                    AdapterPlatform.LINUX,
                    AdapterPlatform.MACOS,
                ],
                "known_limitations": [
                    *manifest.known_limitations,
                    "This connector reads the Dynamo HTTP frontend /metrics endpoint only. Worker "
                    "component, endpoint-picker, operator, and backend-engine metrics use separate "
                    "scrape endpoints and are not collected here.",
                    "Frontend aggregate series and per-worker series remain separate by metric "
                    "name and labels; LOCALFORGE does not sum them or infer cross-frontend totals.",
                    "Worker stack versions and device details remain unknown unless another "
                    "verified source reports them.",
                ],
            }
        )

    def _chat_config(self, config: AdapterConfiguration) -> AdapterConfiguration:
        attached = config.model_copy(
            update={"integration_mode": EngineIntegrationMode.ATTACHED_ENDPOINT}
        )
        return super()._chat_config(attached)

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        attached = config.model_copy(
            update={"integration_mode": EngineIntegrationMode.ATTACHED_ENDPOINT}
        )
        return await super().validate_config(attached)

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        base = await super().collect_fingerprint(config)
        metrics = await super().read_metrics(config)
        worker_states: dict[str, tuple[str, str]] = {}
        if metrics.available:
            for metric in metrics.metrics:
                if metric.metric_name != "dynamo_frontend_router_worker_state" or metric.value != 1:
                    continue
                worker_id = metric.labels.get("router_worker_id")
                state = metric.labels.get("state")
                if not worker_id or not state or len(worker_id) > 80 or len(state) > 40:
                    continue
                identity = _safe_layer_fragment(
                    metric.labels.get("target_namespace", ""),
                    metric.labels.get("target_component", ""),
                )
                worker_states[worker_id] = (state, identity)

        layers = [
            RuntimeStackLayer(
                layer_type="orchestrator",
                name="NVIDIA Dynamo frontend/router",
                version=base.engine_version,
                source="verified_config" if base.engine_version is None else "engine_reported",
            )
        ]
        ordered_workers = sorted(worker_states.items())
        truncated = len(ordered_workers) > _MAX_REPORTED_WORKER_LAYERS
        for worker_id, (state, identity) in ordered_workers[:_MAX_REPORTED_WORKER_LAYERS]:
            suffix = f" [{identity}]" if identity else ""
            layers.append(
                RuntimeStackLayer(
                    layer_type="worker",
                    name=f"Dynamo worker {worker_id} ({state}){suffix}"[:160],
                    version=None,
                    source="engine_reported",
                )
            )
        settings = dict(base.settings)
        settings["frontend_metrics_available"] = metrics.available
        settings["worker_inventory_truncated"] = truncated
        return base.model_copy(update={"stack_layers": layers, "settings": settings})


def _safe_layer_fragment(namespace: str, component: str) -> str:
    values = [value[:60] for value in (namespace, component) if value and value.strip()]
    return "/".join(values)
