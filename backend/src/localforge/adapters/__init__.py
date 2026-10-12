"""Capability-aware inference engine adapters and their shared catalog."""

from localforge.adapters.catalog import AdapterCatalog, default_adapter_catalog
from localforge.adapters.fake import FakeEngineAdapter, FakeEngineScenario

__all__ = ["AdapterCatalog", "FakeEngineAdapter", "FakeEngineScenario", "default_adapter_catalog"]
