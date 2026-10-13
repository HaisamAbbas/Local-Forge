"""Capability-aware inference engine adapters and their shared catalog."""

from localforge.adapters.catalog import AdapterCatalog, default_adapter_catalog
from localforge.adapters.fake import FakeEngineAdapter, FakeEngineScenario
from localforge.adapters.llama_cpp import LlamaCppEngineAdapter
from localforge.adapters.openai_compatible import OpenAICompatibleEngineAdapter
from localforge.adapters.sglang import SglangEngineAdapter
from localforge.adapters.vllm import VllmEngineAdapter

__all__ = [
    "AdapterCatalog",
    "FakeEngineAdapter",
    "FakeEngineScenario",
    "LlamaCppEngineAdapter",
    "OpenAICompatibleEngineAdapter",
    "SglangEngineAdapter",
    "VllmEngineAdapter",
    "default_adapter_catalog",
]
