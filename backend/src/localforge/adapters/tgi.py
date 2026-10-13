"""Attach-only connector for Hugging Face Text Generation Inference."""

import httpx2

from localforge.adapters.openai_compatible import CredentialResolver
from localforge.adapters.serving_engine import (
    OpenAIServingEngineAdapter,
    default_credential_resolver,
)

TGI_ADAPTER_ID = "localforge.tgi"
TGI_ADAPTER_VERSION = "1.0.0"


class TgiEngineAdapter(OpenAIServingEngineAdapter):
    """Connect to an existing TGI endpoint and expose its maintenance status in the catalog."""

    adapter_id = TGI_ADAPTER_ID
    adapter_version = TGI_ADAPTER_VERSION
    namespace = "tgi"
    display_name = "Hugging Face TGI serving endpoint"
    description = (
        "Attach to TGI's OpenAI-compatible Messages API for an existing deployment. TGI is in "
        "maintenance mode."
    )
    metrics_prefix = "tgi_"
    default_health_route = "/health"
    default_version_route = "/info"
    default_model_info_route = "/info"
    default_include_usage = False
    docs_url = "https://huggingface.co/docs/text-generation-inference/en/index"
    default_port = 80
    tested_versions = ("3.3.7 (local API fixture)",)
    maintenance_mode = True
    recommended_for_new_managed_install = False
    status_details = (
        "Maintenance mode. Connect to an existing deployment; LOCALFORGE does not recommend "
        "new managed installations."
    )

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
