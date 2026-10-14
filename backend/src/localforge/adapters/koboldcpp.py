"""Attach-only connector for KoboldCpp's OpenAI-compatible API."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

KOBOLDCPP_ADAPTER_ID = "localforge.koboldcpp"
KOBOLDCPP_ADAPTER_VERSION = "1.0.0"


class KoboldCppEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to KoboldCpp while keeping its bundled inference backend unidentified."""

    adapter_id = KOBOLDCPP_ADAPTER_ID
    adapter_version = KOBOLDCPP_ADAPTER_VERSION
    namespace = "koboldcpp"
    display_name = "KoboldCpp serving endpoint"
    description = (
        "Attach to KoboldCpp's OpenAI-compatible chat API. KoboldCpp is identified as the server; "
        "its lower-level runtime is not inferred."
    )
    metrics_prefix = None
    default_health_route = None
    default_version_route = "/api/extra/version"
    default_model_info_route = None
    # KoboldCpp 1.123 returns a usage chunk when asked (verified live on Windows).
    default_include_usage = True
    docs_url = "https://github.com/LostRuins/koboldcpp/wiki"
    default_port = 5001
    tested_versions = ("1.122.1 (local API fixture)", "1.123 (live, Windows CPU)")

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
