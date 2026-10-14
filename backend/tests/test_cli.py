import os
import socket
from pathlib import Path
from typing import Any

import pytest

from localforge import cli


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
