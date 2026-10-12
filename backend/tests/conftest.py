"""Shared backend test fixtures."""

import sys
from pathlib import Path

import pytest

BACKEND_SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(BACKEND_SOURCE))


@pytest.fixture
def fake_engine():
    """Provide a deterministic local fake-engine fixture without network access."""

    from tests.fixtures.fake_engine import DeterministicFakeEngine

    return DeterministicFakeEngine()
