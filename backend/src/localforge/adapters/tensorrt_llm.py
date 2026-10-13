"""Attach-only connector for NVIDIA TensorRT-LLM's documented serve API."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

TENSORRT_LLM_ADAPTER_ID = "localforge.tensorrt_llm"
TENSORRT_LLM_ADAPTER_VERSION = "1.0.0"


class TensorRtLlmEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to an already running ``trtllm-serve`` endpoint."""

    adapter_id = TENSORRT_LLM_ADAPTER_ID
    adapter_version = TENSORRT_LLM_ADAPTER_VERSION
    namespace = "tensorrt_llm"
    display_name = "TensorRT-LLM serving endpoint"
    description = (
        "Attach to NVIDIA TensorRT-LLM's OpenAI-compatible serve API; LOCALFORGE never launches "
        "or installs the GPU runtime."
    )
    metrics_prefix = "trtllm_"
    default_health_route = "/health"
    default_version_route = "/version"
    default_model_info_route = None
    default_include_usage = True
    docs_url = "https://nvidia.github.io/TensorRT-LLM/commands/trtllm-serve/trtllm-serve.html"
    default_port = 8000
    tested_versions = ("1.3 (local API fixture)",)

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
