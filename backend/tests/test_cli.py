import os
import socket
from pathlib import Path
from typing import Any

import pytest

from localforge import cli

REAL_STARTUP_LINES = cli.startup_recommendation_lines


@pytest.fixture(autouse=True)
def _no_startup_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup recommendations scan ports and inspect hardware; tests opt in explicitly."""

    monkeypatch.setattr(cli, "startup_recommendation_lines", lambda *args: [])


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import uvicorn

    calls: list[dict[str, Any]] = []

    def fake_run(app: str, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def test_starts_the_app_factory_on_loopback(
    uvicorn_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "choose_port", lambda host, requested: 8123)

    assert cli.main(["--no-browser"]) == 0

    assert uvicorn_calls == [
        {
            "app": "localforge.api.main:create_app",
            "factory": True,
            "host": "127.0.0.1",
            "port": 8123,
            "log_level": "warning",
        }
    ]


def test_refuses_non_loopback_hosts(uvicorn_calls: list[dict[str, Any]]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--host", "0.0.0.0", "--no-browser"])

    assert exit_info.value.code == 2
    assert uvicorn_calls == []


def test_data_dir_is_exported_for_the_api_and_workers(
    uvicorn_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LOCALFORGE_DATA_DIR", raising=False)
    monkeypatch.setattr(cli, "choose_port", lambda host, requested: 8123)

    cli.main(["--no-browser", "--data-dir", str(tmp_path / "lab")])

    assert os.environ["LOCALFORGE_DATA_DIR"] == str((tmp_path / "lab").resolve())


def test_falls_back_to_a_free_port_when_the_default_is_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        taken = holder.getsockname()[1]
        monkeypatch.setattr(cli, "DEFAULT_PORT", taken)

        chosen = cli.choose_port("127.0.0.1", None)
        assert chosen != taken
        assert cli.port_is_free("127.0.0.1", chosen)

        with pytest.raises(SystemExit, match="already in use"):
            cli.choose_port("127.0.0.1", taken)


def test_browser_url_brackets_ipv6() -> None:
    assert cli.browser_url("127.0.0.1", 8000) == "http://127.0.0.1:8000/"
    assert cli.browser_url("::1", 8000) == "http://[::1]:8000/"


def test_opens_the_browser_once_the_api_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    class Healthy:
        status = 200

        def __enter__(self) -> "Healthy":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    attempts: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> Healthy:
        attempts.append(url)
        if len(attempts) < 2:
            raise OSError("not listening yet")
        return Healthy()

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    opened: list[str] = []

    cli.open_when_ready("http://127.0.0.1:8000/", opened.append)

    assert attempts == ["http://127.0.0.1:8000/health"] * 2
    assert opened == ["http://127.0.0.1:8000/"]


def _canned_response() -> Any:
    import asyncio

    import httpx2

    from localforge.adapters.catalog import default_adapter_catalog
    from localforge.core.models import (
        AcceleratorInfo,
        HardwareSnapshot,
        MachineProfile,
        MachineProfileStatus,
    )
    from localforge.recommendations import build_recommendations
    from localforge.system.inspection import CpuFeatures

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    machine = MachineProfile(
        label="This machine",
        status=MachineProfileStatus.DETECTED,
        hardware=HardwareSnapshot(
            os_name="Windows",
            os_version="10.0.26200",
            architecture="AMD64",
            cpu_model="Intel(R) Core(TM) Ultra 7 155U",
            memory_total_bytes=32 * 1024**3,
            accelerators=[
                AcceleratorInfo(name="Intel(R) Graphics", vendor="Intel", integrated=True)
            ],
        ),
    )
    return asyncio.run(
        build_recommendations(
            default_adapter_catalog().list_manifests(),
            saved_machine=machine,
            discovery_transport=httpx2.MockTransport(refuse),
            cpu_features_provider=lambda: CpuFeatures(avx2=True, avx512=False),
            install_detector=lambda rules, family, profiles: {},
        )
    )


@pytest.fixture
def canned_recommendations(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    import localforge.recommendations as recommendations

    response = _canned_response()
    requested: list[Path] = []

    def fake(data_directory: Path, **kwargs: object) -> Any:
        requested.append(data_directory)
        return response

    monkeypatch.setattr(recommendations, "recommend_for_data_directory", fake)

    async def no_ollama(*_args: object, **_kwargs: object) -> dict[str, list[str]]:
        return {}  # the "Models to try" section must not contact a real local Ollama

    monkeypatch.setattr("localforge.model_catalog.fetch_ollama_tags", no_ollama)
    return requested


def test_recommend_prints_ranked_engines_without_a_server(
    canned_recommendations: list[Path],
    uvicorn_calls: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    assert cli.main(["recommend", "--data-dir", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert canned_recommendations == [tmp_path.resolve()]
    assert uvicorn_calls == []
    assert "Machine: Windows 11 · Intel Core Ultra 7 155U" in output
    assert "4-bit models up to about" in output
    assert "   1  " in output
    assert "Not suitable here:" in output
    assert "vLLM: Needs an NVIDIA GPU with CUDA" in output
    assert "This is not a speed measurement" in output


def test_recommend_json_output(
    canned_recommendations: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    assert cli.main(["recommend", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["recommendations"][0]["rank"] == 1
    assert payload["machine"]["cpu_features"] == {"avx2": True, "avx512": False}


def test_first_start_prints_the_top_five_before_the_url(
    canned_recommendations: list[Path],
    uvicorn_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "startup_recommendation_lines", REAL_STARTUP_LINES)
    monkeypatch.setattr(cli, "choose_port", lambda host, requested: 8123)

    cli.main(["--no-browser", "--data-dir", str(tmp_path / "fresh")])

    output = capsys.readouterr().out
    assert "Engine recommendations for this machine" in output
    assert output.index("Machine: Windows 11") < output.index("is running at")
    assert "   5  " in output and "   6  " not in output
    assert "Run `localforge recommend` for the full list." in output
    assert "Not suitable here:" not in output


def test_later_starts_print_one_best_fit_line(
    canned_recommendations: list[Path],
    uvicorn_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    (tmp_path / "localforge.sqlite3").write_bytes(b"")
    monkeypatch.setattr(cli, "startup_recommendation_lines", REAL_STARTUP_LINES)
    monkeypatch.setattr(cli, "choose_port", lambda host, requested: 8123)

    cli.main(["--no-browser", "--data-dir", str(tmp_path)])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Best fit for this machine: ")
    assert lines[0].endswith("Run `localforge recommend` for the full list.")
    assert lines[1].startswith("LocalForge ")


def test_startup_recommendations_fail_silently(
    uvicorn_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    import localforge.recommendations as recommendations

    def broken(*args: object, **kwargs: object) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(recommendations, "recommend_for_data_directory", broken)
    assert REAL_STARTUP_LINES(tmp_path, True, 8123) == []

    def slow(*args: object, **kwargs: object) -> Any:
        import time

        time.sleep(1)
        return _canned_response()

    monkeypatch.setattr(recommendations, "recommend_for_data_directory", slow)
    monkeypatch.setattr(cli, "STARTUP_RECOMMENDATION_SECONDS", 0.05)
    assert REAL_STARTUP_LINES(tmp_path, False, 8123) == []


def test_bench_subcommand_is_dispatched_with_its_own_arguments(
    monkeypatch: pytest.MonkeyPatch, uvicorn_calls: list[dict[str, Any]]
) -> None:
    import localforge.headless as headless

    seen: list[list[str]] = []
    monkeypatch.setattr(headless, "bench_main", lambda argv: seen.append(list(argv)) or 7)

    assert cli.main(["bench", "plan.json", "--tolerance", "5"]) == 7
    assert seen == [["plan.json", "--tolerance", "5"]]
    assert uvicorn_calls == []
