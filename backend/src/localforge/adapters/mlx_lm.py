"""Attach-only connector for the official mlx-lm local HTTP server."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

MLX_LM_ADAPTER_ID = "localforge.mlx_lm"
MLX_LM_ADAPTER_VERSION = "1.0.0"


class MlxLmEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to an already running mlx-lm server on Apple silicon macOS."""

    adapter_id = MLX_LM_ADAPTER_ID
    adapter_version = MLX_LM_ADAPTER_VERSION
    namespace = "mlx_lm"
    display_name = "MLX-LM serving endpoint"
    description = (
        "Attach to mlx-lm's local OpenAI-compatible HTTP server. The endpoint must already be "
        "running on a supported Apple silicon Mac."
    )
    metrics_prefix = None
    default_health_route = None
    default_version_route = None
    default_model_info_route = None
    default_include_usage = False
    docs_url = "https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py"
    default_port = 8080
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
