"""Local-child fixtures exercise isolated inference adapters without model libraries."""

import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from localforge.adapters.catalog import AdapterCatalog, default_adapter_catalog
from localforge.adapters.contracts import AdapterConfiguration, GenerationRequest, StreamEventType
from localforge.adapters.exllamav3 import EXLLAMAV3_ADAPTER_ID
from localforge.adapters.library_pipeline import IsolatedLibraryPipelineAdapter
from localforge.adapters.onnxruntime_genai import (
    ONNXRUNTIME_GENAI_ADAPTER_ID,
    OnnxRuntimeGenAiEngineAdapter,
)
from localforge.adapters.openvino_genai import (
    OPENVINO_GENAI_ADAPTER_ID,
    OpenVinoGenAiEngineAdapter,
)
from localforge.core.models import EngineIntegrationMode
from tests.adapter_contract import assert_registered_adapter_contract

FAKE_WORKER_SOURCE = """
import json
import os
import sys
import time

scenario = sys.argv[1]
counter_path = sys.argv[2] if len(sys.argv) > 2 else ""
start_count = 1
if counter_path:
    try:
        start_count = int(open(counter_path, encoding="utf-8").read()) + 1
    except (FileNotFoundError, ValueError):
        pass
    with open(counter_path, "w", encoding="utf-8") as counter:
        counter.write(str(start_count))

initial = json.loads(sys.stdin.buffer.readline())
if initial.get("op") != "initialize":
    print(json.dumps({"type": "error", "code": "ipc_validation_failed"}), flush=True)
    raise SystemExit(2)
if scenario == "startup_error":
    print(json.dumps({"type": "error", "code": "library_dependency_missing"}), flush=True)
    raise SystemExit(3)
print(
    json.dumps(
        {
            "type": "ready",
            "runtime_name": "fixture",
            "package_version": "1.2.3",
            "model_format": "fixture",
            "reported_device": "CPU",
        }
    ),
    flush=True,
)

for line in sys.stdin.buffer:
    request = json.loads(line)
    if request.get("op") == "close":
        raise SystemExit(0)
    if scenario == "crash" or (scenario == "restart" and start_count == 1):
        os._exit(19)
    if scenario == "timeout":
        time.sleep(2)
    if scenario == "malformed":
        print("not-json", flush=True)
        continue
    if scenario == "oversized":
        sys.stdout.write("x" * 1048600 + "\\n")
        sys.stdout.flush()
        continue
    if scenario == "unexpected_field":
        print(json.dumps({"type": "chunk", "text": "bad", "path": "unsafe"}), flush=True)
        continue
    print(json.dumps({"type": "chunk", "text": "fixture output"}), flush=True)
    print(json.dumps({"type": "complete"}), flush=True)
"""


class _FakeOpenVinoAdapter(OpenVinoGenAiEngineAdapter):
    pass


@pytest.fixture
def fake_worker(tmp_path: Path) -> Callable[[str, Path | None], Callable[[str], Sequence[str]]]:
    script = tmp_path / "fixture_worker.py"
    script.write_text(FAKE_WORKER_SOURCE, encoding="utf-8")

    def command_for(
        scenario: str, counter_path: Path | None = None
    ) -> Callable[[str], Sequence[str]]:
        def command(_backend: str) -> Sequence[str]:
            args = (sys.executable, str(script), scenario)
            return args + ((str(counter_path),) if counter_path is not None else ())

        return command

    return command_for


def _config(model_path: Path, *, request_timeout: int = 5) -> AdapterConfiguration:
    return AdapterConfiguration(
        integration_mode=EngineIntegrationMode.LIBRARY_PIPELINE,
        values={
            "model_path": str(model_path),
            "startup_timeout_seconds": 5,
            "request_timeout_seconds": request_timeout,
        },
    )


@pytest.mark.parametrize(
    ("adapter_type", "adapter_id"),
    [
        (_FakeOpenVinoAdapter, OPENVINO_GENAI_ADAPTER_ID),
        (OnnxRuntimeGenAiEngineAdapter, ONNXRUNTIME_GENAI_ADAPTER_ID),
    ],
)
def test_library_adapters_pass_fixture_contract_and_fingerprint_runtime(
    adapter_type: type[IsolatedLibraryPipelineAdapter],
    adapter_id: str,
    fake_worker: Callable[[str, Path | None], Callable[[str], Sequence[str]]],
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "fixture-model"
        model_path.mkdir()
        adapter = adapter_type(_worker_command=fake_worker("normal"))
        catalog = AdapterCatalog()
        manifest = catalog.register(adapter)
        config = _config(model_path)
        try:
            assert manifest.integration_modes == [EngineIntegrationMode.LIBRARY_PIPELINE]
            assert manifest.version_tested == []
            await assert_registered_adapter_contract(catalog, adapter_id, config)
            fingerprint = await catalog.collect_fingerprint(adapter_id, config)
            serialized = json.dumps(fingerprint.model_dump(mode="json"))
            assert fingerprint.runtime_version == "1.2.3"
            assert str(model_path) not in serialized
            assert sys.executable not in serialized
            assert fingerprint.settings["model_format"] == "fixture"
        finally:
            await catalog.close()
        assert not adapter._sessions

    asyncio.run(run())


def test_library_pipeline_reads_profile_namespace_and_timeout_settings(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "fixture-model"
        model_path.mkdir()
        adapter = _FakeOpenVinoAdapter()
        config = AdapterConfiguration(
            integration_mode=EngineIntegrationMode.LIBRARY_PIPELINE,
            values={
                "engine_extensions": {
                    "openvino_genai": {
                        "model_path": str(model_path),
                        "python_executable": sys.executable,
                        "startup_timeout_seconds": 45,
                        "request_timeout_seconds": 90,
                        "device": "GPU",
                    }
                },
                "request_timeout_seconds": 120,
            },
        )
        try:
            validation = await adapter.validate_config(config)
            assert validation.valid
            path, device, python_executable, startup_timeout, request_timeout = (
                adapter._configuration(config)
            )
            assert path == str(model_path.resolve())
            assert device == "GPU"
            assert python_executable == str(Path(sys.executable).resolve())
            assert startup_timeout == 45
            assert request_timeout == 120
        finally:
            await adapter.close()

    asyncio.run(run())


@pytest.mark.parametrize("scenario", ["crash", "malformed", "oversized", "unexpected_field"])
def test_library_worker_crash_and_invalid_ipc_are_sanitized(
    scenario: str,
    fake_worker: Callable[[str, Path | None], Callable[[str], Sequence[str]]],
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        adapter = _FakeOpenVinoAdapter(_worker_command=fake_worker(scenario))
        config = _config(model_path)
        request = await adapter.prepare_request(
            config, GenerationRequest(model_ref="fixture", prompt="local prompt")
        )
        try:
            events = [event async for event in adapter.stream_request(request)]
            assert events[-1].event_type is StreamEventType.ERROR
            assert events[-1].error_code in {
                "library_worker_crashed",
                "ipc_validation_failed",
            }
            assert str(model_path) not in (events[-1].error_message or "")
            assert not adapter._sessions
        finally:
            await adapter.close()

    asyncio.run(run())


def test_library_worker_timeout_is_killed_and_cleaned_up(
    fake_worker: Callable[[str, Path | None], Callable[[str], Sequence[str]]],
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        adapter = _FakeOpenVinoAdapter(_worker_command=fake_worker("timeout"))
        config = _config(model_path, request_timeout=1)
        request = await adapter.prepare_request(
            config, GenerationRequest(model_ref="fixture", prompt="local prompt")
        )
        session = adapter._sessions[next(iter(adapter._sessions))]
        events = [event async for event in adapter.stream_request(request)]
        assert events[-1].event_type is StreamEventType.TIMEOUT
        assert session.process.returncode is not None
        assert not adapter._sessions
        await adapter.close()

    asyncio.run(run())


def test_library_worker_restart_recovers_after_crash(
    fake_worker: Callable[[str, Path | None], Callable[[str], Sequence[str]]],
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        counter_path = tmp_path / "worker-starts.txt"
        adapter = _FakeOpenVinoAdapter(_worker_command=fake_worker("restart", counter_path))
        config = _config(model_path)
        first = await adapter.prepare_request(
            config, GenerationRequest(model_ref="fixture", prompt="first")
        )
        failed = [event async for event in adapter.stream_request(first)]
        assert failed[-1].event_type is StreamEventType.ERROR
        second = await adapter.prepare_request(
            config, GenerationRequest(model_ref="fixture", prompt="second")
        )
        recovered = [event async for event in adapter.stream_request(second)]
        assert recovered[0].event_type is StreamEventType.FIRST_OUTPUT
        assert recovered[-1].event_type is StreamEventType.COMPLETED
        assert counter_path.read_text(encoding="utf-8") == "2"
        await adapter.close()

    asyncio.run(run())


def test_library_dependency_failure_is_reported_as_failed_probe(
    fake_worker: Callable[[str, Path | None], Callable[[str], Sequence[str]]],
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        adapter = _FakeOpenVinoAdapter(_worker_command=fake_worker("startup_error"))
        result = await adapter.probe(_config(model_path))
        assert result.status.value == "failed"
        assert result.diagnostics[0].code == "library_worker_unavailable"
        assert str(model_path) not in result.diagnostics[0].message
        # The worker's reason reaches the user instead of a generic checklist.
        assert "cannot import the runtime package" in result.diagnostics[0].message
        await adapter.close()

    asyncio.run(run())


def test_successful_library_probe_closes_its_temporary_model_worker(
    fake_worker: Callable[[str, Path | None], Callable[[str], Sequence[str]]],
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        counter_path = tmp_path / "worker-starts.txt"
        adapter = _FakeOpenVinoAdapter(_worker_command=fake_worker("normal", counter_path))
        result = await adapter.probe(_config(model_path))
        assert result.status.value == "succeeded"
        assert result.engine_version == "1.2.3"
        # Plans pick models from the probe's inventory, so the configured model must be there.
        assert [model.model_ref for model in result.models] == ["localforge-configured:model"]
        assert not adapter._sessions
        assert counter_path.read_text(encoding="utf-8") == "1"
        await adapter.close()

    asyncio.run(run())


def test_probe_cleanup_does_not_stop_a_concurrent_benchmark_worker(
    fake_worker: Callable[[str, Path | None], Callable[[str], Sequence[str]]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        counter_path = tmp_path / "worker-starts.txt"
        adapter = _FakeOpenVinoAdapter(_worker_command=fake_worker("normal", counter_path))
        config = _config(model_path)
        probe_cleanup_started = asyncio.Event()
        continue_probe_cleanup = asyncio.Event()
        original_discard = adapter._discard_session

        async def pause_probe_cleanup(session: object) -> None:
            if getattr(session, "key", "").startswith("probe:"):
                probe_cleanup_started.set()
                await continue_probe_cleanup.wait()
            await original_discard(session)  # type: ignore[arg-type]

        monkeypatch.setattr(adapter, "_discard_session", pause_probe_cleanup)
        probe_task = asyncio.create_task(adapter.probe(config))
        try:
            await asyncio.wait_for(probe_cleanup_started.wait(), timeout=2)
            request = await adapter.prepare_request(
                config, GenerationRequest(model_ref="fixture", prompt="benchmark")
            )
        finally:
            continue_probe_cleanup.set()
        probe_result = await probe_task
        events = [event async for event in adapter.stream_request(request)]
        assert probe_result.status.value == "succeeded"
        assert events[0].event_type is StreamEventType.FIRST_OUTPUT
        assert events[-1].event_type is StreamEventType.COMPLETED
        assert counter_path.read_text(encoding="utf-8") == "2"
        await adapter.close()

    asyncio.run(run())


def test_library_pipeline_reports_actionable_platform_mismatch(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        adapter = OpenVinoGenAiEngineAdapter(_platform_name=lambda: "FreeBSD")
        config = _config(model_path)
        validation = await adapter.validate_config(config)
        assert not validation.valid
        assert validation.diagnostics[0].code == "platform_unsupported"
        assert "supported host OS" in validation.diagnostics[0].message
        probe = await adapter.probe(config)
        assert probe.status.value == "failed"
        assert probe.diagnostics[0].code == "platform_unsupported"
        await adapter.close()

    asyncio.run(run())


@pytest.mark.parametrize("system_name", ["Windows", "Linux", "Darwin"])
def test_openvino_pipeline_accepts_each_documented_host_os(
    system_name: str,
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model_path = tmp_path / "model"
        model_path.mkdir()
        adapter = OpenVinoGenAiEngineAdapter(_platform_name=lambda: system_name)
        validation = await adapter.validate_config(_config(model_path))
        assert validation.valid
        await adapter.close()

    asyncio.run(run())


def test_exllama_and_dynamo_catalog_state_is_explicitly_reported() -> None:
    async def run() -> None:
        catalog = default_adapter_catalog()
        try:
            exllama = next(
                item for item in catalog.list_manifests() if item.adapter_id == EXLLAMAV3_ADAPTER_ID
            )
            dynamo = catalog.manifest_for("localforge.nvidia_dynamo")
            assert exllama.implementation_status == "planned"
            assert exllama.integration_modes == []
            assert dynamo.implementation_status == "implemented"
            assert EngineIntegrationMode.COMPOSED_SERVING_STACK in dynamo.integration_modes
        finally:
            await catalog.close()

    asyncio.run(run())


def test_prompt_15c_support_matrix_records_every_target_status() -> None:
    matrix_path = Path(__file__).parents[2] / "docs" / "connector-support-matrix.md"
    rows = [
        line
        for line in matrix_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `localforge.")
    ]
    expectations = {
        "localforge.mlx_lm": "implemented",
        "localforge.mlc_llm": "implemented",
        "localforge.openvino_genai": "implemented",
        "localforge.onnxruntime_genai": "implemented",
        "localforge.mistral_rs": "implemented",
        "localforge.nvidia_dynamo": "implemented",
        "localforge.exllamav3": "planned",
    }
    for adapter_id, status in expectations.items():
        row = next((line for line in rows if line.startswith(f"| `{adapter_id}` |")), None)
        assert row is not None
        assert f"| {status} |" in row
