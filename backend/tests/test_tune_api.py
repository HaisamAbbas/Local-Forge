"""Tuning from the browser: starting, progress, cancelling and refusing what cannot be tuned.

The private benchmark server and the benchmark runs are replaced with fakes; no engine starts.
"""

import contextlib
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from localforge.api import tunes
from localforge.api.main import AppSettings, create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        AppSettings(data_directory=tmp_path, session_token_factory=lambda: "session-token")
    )
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        test_client.headers.update(
            {"X-LOCALFORGE-SESSION": "session-token", "Origin": "http://127.0.0.1:5173"}
        )
        yield test_client


def _llama_profile(client: TestClient, tmp_path: Path) -> str:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    response = client.post(
        "/api/v1/engines",
        json={
            "label": "My llama.cpp",
            "adapter_id": "localforge.llama_cpp",
            "integration_mode": "managed_process",
            "endpoint_url": "http://127.0.0.1:9931",
            "engine_extensions": {
                "llama_cpp": {"executable_path": sys.executable, "model_path": str(model)}
            },
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _result(speed: float) -> dict[str, Any]:
    return {
        "metrics": {
            "generation_throughput_tokens_per_second.run_rate": {"value": speed},
            "ttft_ms.p50": {"value": 100.0},
            "request_latency_ms.p50": {"value": 1000.0 / speed},
        }
    }


@pytest.fixture
def fake_benchmarks(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Trials return at once, or wait on the returned event when it is cleared."""

    gate = threading.Event()
    gate.set()

    @contextlib.contextmanager
    def fake_server(_directory: Path) -> Iterator[str]:
        yield "http://127.0.0.1:1"

    def fake_evaluator(_client: object, _plan: dict[str, Any]) -> Any:
        def evaluate(settings: dict[str, Any]) -> dict[str, Any]:
            gate.wait(timeout=10)
            return _result(30.0 if settings.get("threads") == 8 else 20.0)

        return evaluate

    monkeypatch.setattr(tunes, "private_server", fake_server)
    monkeypatch.setattr(tunes, "make_evaluator", fake_evaluator)
    monkeypatch.setattr(
        tunes,
        "search_space_for",
        lambda _plan, _requested: {"threads": [4, 8], "gpu_layers": [], "batch_size": []},
    )
    return gate


def _wait_for(client: TestClient, job_id: str, status: str) -> dict[str, Any]:
    for _ in range(100):
        job = client.get(f"/api/v1/tunes/{job_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.05)
    raise AssertionError(f"the tune never reached {status}: {job}")


def test_a_tune_runs_to_a_recommendation(
    client: TestClient, tmp_path: Path, fake_benchmarks: threading.Event
) -> None:
    profile_id = _llama_profile(client, tmp_path)
    started = client.post("/api/v1/tunes", json={"engine_profile_id": profile_id})
    assert started.status_code == 202, started.text
    assert started.json()["total_trials"] == 3
    job = _wait_for(client, started.json()["id"], "completed")
    assert job["best_settings"] == {"threads": 8}
    assert job["improvement_percent"] == 50.0
    assert [trial["settings"] for trial in job["trials"]] == [{}, {"threads": 4}, {"threads": 8}]
    assert job["recommended_launch_settings"]["threads"] == 8
    assert job["recommended_launch_settings"]["model_path"].endswith("model.gguf")
    assert client.get("/api/v1/tunes").json()[0]["id"] == job["id"]


def test_only_one_tune_runs_at_a_time_and_it_can_be_stopped(
    client: TestClient, tmp_path: Path, fake_benchmarks: threading.Event
) -> None:
    fake_benchmarks.clear()
    profile_id = _llama_profile(client, tmp_path)
    first = client.post("/api/v1/tunes", json={"engine_profile_id": profile_id}).json()
    second = client.post("/api/v1/tunes", json={"engine_profile_id": profile_id})
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "tune_running"

    stopping = client.post(f"/api/v1/tunes/{first['id']}/cancel").json()
    assert stopping["stopping"] is True
    fake_benchmarks.set()
    job = _wait_for(client, first["id"], "cancelled")
    assert job["recommended_launch_settings"] is None


def test_other_engines_and_modes_are_refused(
    client: TestClient, tmp_path: Path, fake_benchmarks: threading.Event
) -> None:
    attached = client.post(
        "/api/v1/engines",
        json={
            "label": "Attached",
            "adapter_id": "localforge.llama_cpp",
            "integration_mode": "attached_endpoint",
            "endpoint_url": "http://127.0.0.1:8080",
        },
    ).json()["id"]
    refused = client.post("/api/v1/tunes", json={"engine_profile_id": attached})
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "tune_unsupported"
    missing = client.post(
        "/api/v1/tunes", json={"engine_profile_id": "00000000-0000-4000-8000-000000000000"}
    )
    assert missing.status_code == 404


def test_tune_routes_need_the_session_token_and_a_loopback_host(
    client: TestClient, tmp_path: Path, fake_benchmarks: threading.Event
) -> None:
    profile_id = _llama_profile(client, tmp_path)
    app = client.app
    with TestClient(app, base_url="http://127.0.0.1") as anonymous:
        refused = anonymous.post("/api/v1/tunes", json={"engine_profile_id": profile_id})
    assert refused.status_code in {401, 403}
    with TestClient(app, base_url="http://example.test") as remote:
        assert remote.get("/api/v1/tunes").status_code == 403
