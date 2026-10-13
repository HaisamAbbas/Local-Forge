"""Attach-only connector for the mistral.rs OpenAI-compatible server."""

import httpx2

from localforge.adapters.contracts import AdapterManifest
from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

MISTRAL_RS_ADAPTER_ID = "localforge.mistral_rs"
MISTRAL_RS_ADAPTER_VERSION = "1.0.0"


class MistralRsEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to mistral.rs while keeping its server identity separate from model artifacts."""

    adapter_id = MISTRAL_RS_ADAPTER_ID
    adapter_version = MISTRAL_RS_ADAPTER_VERSION
    namespace = "mistral_rs"
    display_name = "mistral.rs serving endpoint"
    description = (
        "Attach to the mistral.rs OpenAI-compatible server and its optional Prometheus endpoint."
    )
    metrics_prefix = "mistralrs_"
    default_health_route = "/health"
    default_version_route = None
    default_model_info_route = None
    default_include_usage = False
    docs_url = "https://ericlbuehler.github.io/mistral.rs/guides/serve/openai-compatible-apis/"
    default_port = 1234
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
                "known_limitations": [
                    *manifest.known_limitations,
                    "mistral.rs does not validate bearer credentials. Its default server bind is "
                    "all interfaces; use a loopback bind or an authenticating reverse proxy.",
                    "Only the mistralrs_* engine metric family is collected; the server's "
                    "http_* metrics are not normalized by this adapter.",
                ]
            }
        )
