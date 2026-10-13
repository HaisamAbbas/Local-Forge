"""Attach-only connector for LMDeploy's OpenAI-compatible API server."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

LMDEPLOY_ADAPTER_ID = "localforge.lmdeploy"
LMDEPLOY_ADAPTER_VERSION = "1.0.0"


class LmDeployEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to a running LMDeploy API server and preserve its metrics namespace."""

    adapter_id = LMDEPLOY_ADAPTER_ID
    adapter_version = LMDEPLOY_ADAPTER_VERSION
    namespace = "lmdeploy"
    display_name = "LMDeploy serving endpoint"
    description = (
        "Attach to LMDeploy's OpenAI-compatible API server and optionally read its native "
        "Prometheus metrics."
    )
    metrics_prefix = "lmdeploy:"
    default_health_route = None
    default_version_route = None
    default_model_info_route = None
    default_include_usage = False
    docs_url = "https://lmdeploy.readthedocs.io/en/latest/llm/api_server.html"
    default_port = 23333
    tested_versions = ("0.18.0 (local API fixture)",)

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
