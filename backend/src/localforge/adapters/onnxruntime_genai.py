"""Isolated ONNX Runtime GenAI library pipeline connector."""

from collections.abc import Callable, Sequence

from localforge.adapters.contracts import AdapterPlatform
from localforge.adapters.library_pipeline import IsolatedLibraryPipelineAdapter

ONNXRUNTIME_GENAI_ADAPTER_ID = "localforge.onnxruntime_genai"
ONNXRUNTIME_GENAI_ADAPTER_VERSION = "1.0.0"


class OnnxRuntimeGenAiEngineAdapter(IsolatedLibraryPipelineAdapter):
    """Run a local ONNX Runtime GenAI model in a dedicated child process."""

    adapter_id = ONNXRUNTIME_GENAI_ADAPTER_ID
    adapter_version = ONNXRUNTIME_GENAI_ADAPTER_VERSION
    display_name = "ONNX Runtime GenAI library pipeline"
    description = (
        "Run a pre-existing ONNX Runtime GenAI model on this host in an isolated Python worker."
    )
    docs_url = "https://onnxruntime.ai/docs/genai/"
    backend_name = "onnxruntime_genai"
    package_name = "onnxruntime-genai"
    runtime_name = "ONNX Runtime GenAI"
    configure_device = False
    default_device = "model_config"
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
