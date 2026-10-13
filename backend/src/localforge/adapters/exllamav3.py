"""Catalog metadata for ExLlamaV3 until its library pipeline has a fixture-backed adapter."""

from localforge.adapters.contracts import AdapterCategory, AdapterManifest, AdapterPlatform

EXLLAMAV3_ADAPTER_ID = "localforge.exllamav3"


def exllamav3_catalog_manifest() -> AdapterManifest:
    return AdapterManifest(
        protocol_version=1,
        adapter_id=EXLLAMAV3_ADAPTER_ID,
        adapter_version="1.0.0",
        display_name="ExLlamaV3 library pipeline",
        description=(
            "Planned isolated library pipeline for the ExLlamaV3 local inference runtime."
        ),
        category=AdapterCategory.LIBRARY_PIPELINE,
        implementation_status="planned",
        integration_modes=[],
        operating_systems=[AdapterPlatform.WINDOWS, AdapterPlatform.LINUX],
        version_tested=[],
        declared_capabilities=set(),
        supported_transports=[],
        native_metrics=[],
        known_limitations=[
            "ExLlamaV3 is a CUDA-oriented Python inference library; it does not provide its own "
            "project-owned OpenAI-compatible server. TabbyAPI is a separate project.",
            "Official release wheels require compatible Python, PyTorch, CUDA, and GPU builds. "
            "LOCALFORGE does not install these dependencies or download models.",
            "The LOCALFORGE ExLlamaV3 library pipeline is not implemented; no model execution or "
            "engine metrics are available through this catalog entry.",
        ],
        documentation_url="https://github.com/turboderp-org/exllamav3",
        status_details="Planned; no callable adapter is registered.",
    )
