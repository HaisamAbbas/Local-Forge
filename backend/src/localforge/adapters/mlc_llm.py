"""Attach-only connector for MLC LLM's OpenAI-compatible REST server."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

MLC_LLM_ADAPTER_ID = "localforge.mlc_llm"
MLC_LLM_ADAPTER_VERSION = "1.0.0"


class MlcLlmEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to an existing MLC LLM server, without compiling or downloading a model."""

    adapter_id = MLC_LLM_ADAPTER_ID
    adapter_version = MLC_LLM_ADAPTER_VERSION
    namespace = "mlc_llm"
    display_name = "MLC LLM serving endpoint"
    description = (
        "Attach to MLC LLM's REST server. The selected model must already be compiled and served."
    )
    metrics_prefix = None
    default_health_route = None
    default_version_route = None
    default_model_info_route = None
    default_include_usage = False
    docs_url = "https://llm.mlc.ai/docs/deploy/rest.html"
    default_port = 8000
    tested_versions: tuple[str, ...] = ()
    unsupported_capabilities = frozenset({"health", "version_discovery"})

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
