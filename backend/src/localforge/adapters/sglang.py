"""Attach-only connector for SGLang's OpenAI-compatible serving API."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

SGLANG_ADAPTER_ID = "localforge.sglang"
SGLANG_ADAPTER_VERSION = "1.0.0"


class SglangEngineAdapter(OpenAIServingEngineAdapter):
    """Probe and benchmark an existing SGLang endpoint without querying unsafe server info."""

    adapter_id = SGLANG_ADAPTER_ID
    adapter_version = SGLANG_ADAPTER_VERSION
    namespace = "sglang"
    display_name = "SGLang serving endpoint"
    description = (
        "Attach to an existing SGLang server through its OpenAI-compatible Chat Completions API."
    )
    metrics_prefix = "sglang:"
    default_health_route = "/ready"
    # SGLang's /server_info returns full launch arguments, including configured credentials.
    default_version_route = None
    default_model_info_route = "/model_info"
    # Streaming usage options have had version-specific failures; leave them opt-in.
    default_include_usage = False
    docs_url = "https://docs.sglang.ai/basic_usage/openai_api.html"
    default_port = 30000
    unsafe_routes = frozenset({"server_info", "get_server_info"})

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
