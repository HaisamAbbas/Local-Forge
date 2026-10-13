"""Shared backend test fixtures."""

import sys
from pathlib import Path

import pytest

BACKEND_SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(BACKEND_SOURCE))


@pytest.fixture(autouse=True)
def _enable_test_fixture_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most API tests drive the deterministic fake engine, which users must opt in to."""

    monkeypatch.setenv("LOCALFORGE_SHOW_TEST_FIXTURE_ENGINES", "1")


@pytest.fixture(autouse=True)
def _no_hardware_command_execution(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep API tests from running nvidia-smi, PowerShell, lspci, or sysctl on the host."""

    if request.module.__name__.endswith("test_system_inspection"):
        return
    monkeypatch.setattr(
        "localforge.system.inspection._run_command", lambda _arguments, _timeout: None
    )


@pytest.fixture
def fake_engine():
    """Provide a deterministic local fake-engine fixture without network access."""

    from tests.fixtures.fake_engine import DeterministicFakeEngine

    return DeterministicFakeEngine()
