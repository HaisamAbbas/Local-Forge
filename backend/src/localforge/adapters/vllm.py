"""Attach-only connector for vLLM's OpenAI-compatible serving API."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

VLLM_ADAPTER_ID = "localforge.vllm"
VLLM_ADAPTER_VERSION = "1.0.0"


class VllmEngineAdapter(OpenAIServingEngineAdapter):
    """Probe and benchmark an already running vLLM OpenAI-compatible endpoint."""

    adapter_id = VLLM_ADAPTER_ID
    adapter_version = VLLM_ADAPTER_VERSION
    namespace = "vllm"
    display_name = "vLLM serving endpoint"
    description = (
        "Attach to an existing vLLM server through its OpenAI-compatible Chat Completions API."
    )
    metrics_prefix = "vllm:"
    default_health_route = "/health"
    default_version_route = "/version"
    default_model_info_route = None
    default_include_usage = True
    docs_url = "https://docs.vllm.ai/en/stable/serving/online_serving/"
    default_port = 8000

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
