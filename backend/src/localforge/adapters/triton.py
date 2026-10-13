"""Catalog metadata for the not-yet-implemented Triton TensorRT-LLM stack adapter."""

from localforge.adapters.contracts import AdapterCategory, AdapterManifest, AdapterPlatform

TRITON_TENSORRT_LLM_ADAPTER_ID = "localforge.triton_tensorrt_llm"


def triton_tensorrt_llm_catalog_manifest() -> AdapterManifest:
    """Describe the verified stack without pretending its model API is OpenAI chat."""

    return AdapterManifest(
        protocol_version=1,
        adapter_id=TRITON_TENSORRT_LLM_ADAPTER_ID,
        adapter_version="1.0.0",
        display_name="NVIDIA Triton + TensorRT-LLM backend",
        description=(
            "Planned serving-stack connector. Triton exposes model-specific inference routes; "
            "LOCALFORGE does not yet implement that API as a chat adapter."
        ),
        category=AdapterCategory.SERVING_STACK,
        implementation_status="planned",
        integration_modes=[],
        operating_systems=[AdapterPlatform.LINUX],
        version_tested=[],
        declared_capabilities=set(),
        supported_transports=["http", "https", "grpc"],
        native_metrics=[],
        known_limitations=[
            "Triton with TensorRT-LLM is a composed serving stack, not a single runtime.",
            "The official backend uses Triton model routes such as /v2/models/{name}/generate; "
            "LOCALFORGE currently requires a verified Chat Completions integration for an "
            "implemented chat adapter.",
            "Linux and NVIDIA GPU deployment requirements apply to the serving host; no managed "
            "launch, download, or accelerator detection is implemented.",
            "Triton and TensorRT-LLM versions and backend identity remain unknown until an "
            "implemented probe can verify each layer separately.",
        ],
        documentation_url="https://github.com/triton-inference-server/tensorrtllm_backend",
        status_details="Planned; no callable adapter is registered for this stack yet.",
    )
