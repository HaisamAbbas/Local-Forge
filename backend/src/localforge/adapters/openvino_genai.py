"""Isolated OpenVINO GenAI library pipeline connector."""

from collections.abc import Callable, Sequence

from localforge.adapters.contracts import AdapterPlatform
from localforge.adapters.library_pipeline import IsolatedLibraryPipelineAdapter

OPENVINO_GENAI_ADAPTER_ID = "localforge.openvino_genai"
OPENVINO_GENAI_ADAPTER_VERSION = "1.0.0"


class OpenVinoGenAiEngineAdapter(IsolatedLibraryPipelineAdapter):
    """Run a local OpenVINO GenAI model in a dedicated child process."""

    adapter_id = OPENVINO_GENAI_ADAPTER_ID
    adapter_version = OPENVINO_GENAI_ADAPTER_VERSION
    display_name = "OpenVINO GenAI library pipeline"
    description = (
        "Run a pre-existing OpenVINO GenAI model on this host in an isolated Python worker."
    )
    docs_url = (
        "https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai.html"
    )
    backend_name = "openvino_genai"
    package_name = "openvino-genai"
    runtime_name = "OpenVINO GenAI"
    supported_platforms = (
        AdapterPlatform.WINDOWS,
        AdapterPlatform.LINUX,
        AdapterPlatform.MACOS,
    )

    def __init__(
        self,
        *,
        _platform_name: Callable[[], str] | None = None,
        _worker_command: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        if _platform_name is None:
            super().__init__(_worker_command=_worker_command)
        else:
            super().__init__(
                _platform_name=_platform_name,
                _worker_command=_worker_command,
            )
