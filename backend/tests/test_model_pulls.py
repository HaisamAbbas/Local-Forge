"""Pulling models through a local Ollama: explicit confirmation, progress, probe, cancel, refusals.

Ollama is mocked at the HTTP transport; nothing is downloaded.
"""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.fake import FAKE_ADAPTER_ID, FakeEngineAdapter, FakeEngineScenario
from localforge.adapters.ollama import OllamaEngineAdapter
from localforge.api.main import AppSettings, create_app

OLLAMA = "http://127.0.0.1:11434"


class FakeOllama:
    """Answers the Ollama routes LocalForge uses; /api/pull streams NDJSON progress."""

    def __init__(self) -> None:
        self.tags = ["qwen2.5:3b"]
        self.mode = "success"
        self.pull_requests: list[tuple[str, dict[str, Any]]] = []
        self.release = threading.Event()
        self.stream_closed = threading.Event()

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "HEAD":
            return httpx2.Response(200)
        if path == "/api/version":
            return httpx2.Response(200, json={"version": "0.40.3"})
        if path == "/api/tags":
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {"name": tag, "model": tag, "size": 1_000, "details": {"format": "gguf"}}
                        for tag in self.tags
                    ]
                },
            )
        if path == "/api/pull" and request.method == "POST":
            body = json.loads(request.content)
            self.pull_requests.append((str(request.url), body))
            if self.mode == "http_error":
                return httpx2.Response(500, json={"error": "registry unavailable"})
            return httpx2.Response(200, content=self._stream(body["model"]))
        return httpx2.Response(404, json={"error": "not found"})

    async def _stream(self, tag: str) -> AsyncIterator[bytes]:
        def line(payload: dict[str, Any]) -> bytes:
            return (json.dumps(payload) + "\n").encode()

        yield line({"status": "pulling manifest"})
        yield line(
            {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 250}
        )
        if self.mode == "error":
            yield line({"error": "pull model manifest: file does not exist"})
            return
        if self.mode == "hang":
            try:
                while not self.release.is_set():
                    await asyncio.sleep(0.01)
            finally:
                self.stream_closed.set()
            return
        yield line(
            {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 1000}
        )
        yield line(
            {"status": "pulling bbb", "digest": "sha256:bbb", "total": 100, "completed": 100}
        )
        yield line({"status": "verifying sha256 digest"})
        yield line({"status": "writing manifest"})
        if self.mode == "no_success":
            return
        self.tags.append(tag)
        yield line({"status": "success"})


@pytest.fixture
def ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def client(tmp_path: Path, ollama: FakeOllama) -> Iterator[TestClient]:
    def catalog_factory() -> AdapterCatalog:
        catalog = AdapterCatalog()
        catalog.register(OllamaEngineAdapter(transport=ollama.transport()))
        catalog.register(FakeEngineAdapter(FakeEngineScenario()))
        return catalog

    app = create_app(
        AppSettings(
            data_directory=tmp_path,
            adapter_catalog_factory=catalog_factory,
            session_token_factory=lambda: "session-token",
        )
    )
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        app.state.ollama_pull_transport = ollama.transport()
        app.state.ollama_tags_transport = ollama.transport()
        test_client.headers.update(
            {"X-LOCALFORGE-SESSION": "session-token", "Origin": "http://127.0.0.1:5173"}
        )
        yield test_client


def _ollama_profile(client: TestClient, *, endpoint: str = OLLAMA, enable: bool = True) -> str:
    response = client.post(
        "/api/v1/engines",
        json={
            "label": "Ollama here",
            "adapter_id": "localforge.ollama",
            "integration_mode": "attached_endpoint",
            "endpoint_url": endpoint,
        },
    )
    assert response.status_code == 201, response.text
    profile_id = str(response.json()["id"])
    if enable:
        probe = client.post(
            f"/api/v1/engines/{profile_id}/probe", json={"confirm_remote_egress": False}
        )
        assert probe.status_code == 200, probe.text
        enabled = client.post(f"/api/v1/engines/{profile_id}/enable", json={})
        assert enabled.status_code == 200, enabled.text
    return profile_id


def _pull(client: TestClient, profile_id: str, tag: str, **extra: object) -> httpx2.Response:
    body: dict[str, object] = {"engine_profile_id": profile_id, "ollama_tag": tag, "confirm": True}
    body.update(extra)
    return client.post("/api/v1/models/pull", json=body)  # type: ignore[return-value]


def _wait(client: TestClient, pull_id: str, until: str = "finished") -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        view = client.get(f"/api/v1/models/pulls/{pull_id}").json()
        if (
            until == "finished"
            and view["status"] != "running"
            and view["probe_status"]
            in {
                "not_started",
                "succeeded",
                "failed",
            }
        ):
            return dict(view)
        if until == "progress" and view["bytes_completed"]:
            return dict(view)
        time.sleep(0.02)
    raise AssertionError(f"pull {pull_id} did not reach {until}")


def test_pull_streams_progress_then_probes_so_the_model_appears(
    client: TestClient, ollama: FakeOllama
) -> None:
    profile_id = _ollama_profile(client)
    response = _pull(client, profile_id, "llama3.2:3b")
    assert response.status_code == 202, response.text
    started = response.json()
    assert started["status"] == "running"
    assert started["catalog_model_id"] == "llama-3.2-3b-instruct"

    view = _wait(client, started["id"])
    assert view["status"] == "succeeded"
    assert view["phase"] == "success"
    assert view["bytes_total"] == 1100 and view["bytes_completed"] == 1100
    assert view["percent"] == 100.0
    assert view["error"] is None
    assert view["probe_status"] == "succeeded"
    assert "2 models" in view["probe_message"]
    assert ollama.pull_requests == [
        (f"{OLLAMA}/api/pull", {"model": "llama3.2:3b", "stream": True})
    ]
    models = client.get("/api/v1/models").json()
    assert "llama3.2:3b" in {item["model_ref"] for item in models}
    listed = client.get("/api/v1/models/pulls").json()
    assert [item["id"] for item in listed] == [started["id"]]

    suggestions = client.get("/api/v1/models/suggestions?use_case=chat").json()
    llama = next(i for i in suggestions["suggestions"] if i["model_id"] == "llama-3.2-3b-instruct")
    assert llama["downloaded"][0]["engine_profile_id"] == profile_id


def test_pull_reports_progress_and_can_be_cancelled(client: TestClient, ollama: FakeOllama) -> None:
    ollama.mode = "hang"
    profile_id = _ollama_profile(client)
    pull_id = _pull(client, profile_id, "qwen2.5:7b").json()["id"]
    progress = _wait(client, pull_id, until="progress")
    assert progress["status"] == "running"
    assert progress["bytes_completed"] == 250 and progress["bytes_total"] == 1000
    assert progress["percent"] == 25.0
    assert progress["phase"] == "pulling aaa"

    duplicate = _pull(client, profile_id, "qwen2.5:7b")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "pull_in_progress"
    assert duplicate.json()["detail"]["pull_id"] == pull_id

    cancelled = client.post(f"/api/v1/models/pulls/{pull_id}/cancel", json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert ollama.stream_closed.wait(5)  # closing the stream is what stops Ollama's download
    view = _wait(client, pull_id)
    assert view["status"] == "cancelled" and view["probe_status"] == "not_started"
    assert "qwen2.5:7b" not in ollama.tags
    assert (
        client.post(
            "/api/v1/models/pulls/00000000-0000-4000-8000-000000000000/cancel", json={}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("error", "pull model manifest: file does not exist"),
        ("http_error", "registry unavailable"),
        ("no_success", "without confirming success"),
    ],
)
def test_pull_failures_are_reported_without_probing(
    client: TestClient, ollama: FakeOllama, mode: str, message: str
) -> None:
    ollama.mode = mode
    profile_id = _ollama_profile(client)
    pull_id = _pull(client, profile_id, "gemma3:1b").json()["id"]
    view = _wait(client, pull_id)
    assert view["status"] == "failed"
    assert message in view["error"]
    assert view["probe_status"] == "not_started"


def test_pull_requires_confirmation_session_and_a_known_tag(
    client: TestClient, ollama: FakeOllama
) -> None:
    profile_id = _ollama_profile(client)
    unconfirmed = _pull(client, profile_id, "qwen2.5:3b", confirm=False)
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["detail"]["code"] == "pull_confirmation_required"

    unknown = _pull(client, profile_id, "someone/custom-model:latest")
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unknown_model_tag"

    invalid = _pull(client, profile_id, "../../etc/passwd", custom_tag=True)
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_model_tag"

    # A custom tag cannot send Ollama to an arbitrary registry host.
    for foreign in (
        "evil.example.com/ns/model:latest",
        "evil.example.com/model:latest",
        "localhost/ns/model:latest",
        "evil/ns/model:latest",
    ):
        refused_registry = _pull(client, profile_id, foreign, custom_tag=True)
        assert refused_registry.status_code == 422, foreign
        assert refused_registry.json()["detail"]["code"] == "registry_not_allowed", foreign
    assert ollama.pull_requests == []

    no_token = TestClient(client.app, base_url="http://127.0.0.1")
    refused = no_token.post(
        "/api/v1/models/pull",
        json={"engine_profile_id": profile_id, "ollama_tag": "qwen2.5:3b", "confirm": True},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "local_session_required"
    assert ollama.pull_requests == []

    custom = _pull(client, profile_id, "someone/custom-model:latest", custom_tag=True)
    assert custom.status_code == 202, custom.text
    assert custom.json()["catalog_model_id"] is None
    assert _wait(client, custom.json()["id"])["status"] == "succeeded"


def test_pull_refuses_remote_disabled_and_non_ollama_profiles(
    client: TestClient, ollama: FakeOllama
) -> None:
    remote = _ollama_profile(client, endpoint="https://gpu.example.com", enable=False)
    response = _pull(client, remote, "qwen2.5:3b")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "remote_pull_refused"

    disabled = _ollama_profile(client, enable=False)
    response = _pull(client, disabled, "qwen2.5:3b")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "engine_profile_not_enabled"

    fake = client.post(
        "/api/v1/engines",
        json={
            "label": "Fake",
            "adapter_id": FAKE_ADAPTER_ID,
            "integration_mode": "attached_endpoint",
        },
    ).json()["id"]
    response = _pull(client, fake, "qwen2.5:3b")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_an_ollama_profile"

    missing = _pull(client, "00000000-0000-4000-8000-000000000000", "qwen2.5:3b")
    assert missing.status_code == 404
    assert ollama.pull_requests == []


def test_pull_routes_are_loopback_only(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    with TestClient(app, base_url="http://example.com") as remote_client:
        assert remote_client.get("/api/v1/models/pulls").status_code == 403
        assert (
            remote_client.post(
                "/api/v1/models/pull",
                json={"engine_profile_id": "x", "ollama_tag": "qwen2.5:3b", "confirm": True},
            ).status_code
            == 403
        )


def test_registry_host_follows_ollamas_name_parsing() -> None:
    from localforge.model_catalog import ollama_registry_host

    assert ollama_registry_host("qwen2.5:3b") is None
    assert ollama_registry_host("someone/custom-model:latest") is None
    assert ollama_registry_host("hf.co/user/repo:Q4_K_M") == "hf.co"
    assert ollama_registry_host("registry.ollama.ai/library/qwen2.5:3b") == "registry.ollama.ai"
    assert ollama_registry_host("Evil.Example.com/model") == "evil.example.com"
    assert ollama_registry_host("localhost/ns/model") == "localhost"
