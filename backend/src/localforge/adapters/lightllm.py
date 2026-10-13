"""Attach-only connector for LightLLM's OpenAI-compatible chat endpoint."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

LIGHTLLM_ADAPTER_ID = "localforge.lightllm"
LIGHTLLM_ADAPTER_VERSION = "1.0.0"


class LightLlmEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to a running LightLLM server without guessing its model runtime metadata."""

    adapter_id = LIGHTLLM_ADAPTER_ID
    adapter_version = LIGHTLLM_ADAPTER_VERSION
    namespace = "lightllm"
    display_name = "LightLLM serving endpoint"
    description = (
        "Attach to LightLLM's OpenAI-compatible Chat Completions endpoint. Version and native "
        "metrics stay unknown unless a documented route is available."
    )
    metrics_prefix = None
    default_health_route = "/health"
    default_version_route = None
    default_model_info_route = None
    default_include_usage = False
    docs_url = "https://github.com/ModelTC/LightLLM"
    default_port = 8000
    tested_versions = ("1.2.0 (local API fixture)",)

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
