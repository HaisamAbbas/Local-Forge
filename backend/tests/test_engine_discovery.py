"""Loopback engine discovery: identification, self-skip, validation, and bounded scanning."""

import asyncio
import json
import time
from collections.abc import Callable
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from localforge.adapters.catalog import default_adapter_catalog
from localforge.api.main import AppSettings, create_app
from localforge.discovery import (
    MAX_DISCOVERY_PORTS,
    DiscoveryResponse,
    SavedEndpoint,
    discover_engines,
    matching_profile_ids,
    parse_ports,
)

Routes = dict[str, tuple[int, object]]


def _implemented() -> dict[str, str]:
    catalog = default_adapter_catalog()
    return {
        manifest.adapter_id: manifest.display_name
        for manifest in catalog.list_manifests()
        if manifest.implementation_status == "implemented"
    }


def _transport(
    servers: dict[int, Routes],
    seen: list[httpx2.Request] | None = None,
    *,
    hang_ports: frozenset[int] = frozenset(),
    raw: dict[int, Callable[[httpx2.Request], httpx2.Response]] | None = None,
) -> httpx2.MockTransport:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        port = request.url.port
        assert request.url.host == "127.0.0.1"
        assert request.method == "GET"
        assert "authorization" not in request.headers
        if port in hang_ports:
            await asyncio.sleep(30)
        if raw is not None and port in raw:
            return raw[port](request)
        routes = servers.get(port or 0)
        if routes is None:
            raise httpx2.ConnectError("connection refused", request=request)
        status, payload = routes.get(request.url.path, (404, {"error": "not found"}))
        if isinstance(payload, str):
            return httpx2.Response(status, text=payload)
        return httpx2.Response(status, json=payload)

    return httpx2.MockTransport(handler)


def _scan(
    servers: dict[int, Routes],
    ports: list[int],
    **kwargs: object,
) -> DiscoveryResponse:
    transport = kwargs.pop("transport", None) or _transport(servers)
    return asyncio.run(
        discover_engines(
            implemented_adapters=_implemented(),
            ports=ports,
            transport=transport,  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )
    )


def _models(*ids: str, owned_by: str = "owner") -> tuple[int, object]:
    return 200, {"object": "list", "data": [{"id": i, "owned_by": owned_by} for i in ids]}


ENGINES: dict[str, tuple[Routes, str, str, str | None]] = {
    "ollama": (
        {
            "/api/version": (200, {"version": "0.40.3"}),
            "/v1/models": _models("qwen2.5:3b", owned_by="library"),
            "/": (200, "Ollama is running"),
        },
        "localforge.ollama",
        "",
        "0.40.3",
    ),
    "koboldcpp": (
        {
            "/api/extra/version": (200, {"result": "KoboldCpp", "version": "1.123"}),
            "/v1/models": _models("koboldcpp/qwen", owned_by="koboldcpp"),
            # KoboldCpp emulates some llama.cpp routes; the specific check must win.
            "/props": (200, {"build_info": "kcpp"}),
        },
        "localforge.koboldcpp",
        "",
        "1.123",
    ),
    "llama_cpp_props": (
        {
            "/props": (200, {"build_info": "b11609-abc", "model_path": "C:\\m\\q.gguf"}),
            "/v1/models": _models("q.gguf", owned_by="llamacpp"),
        },
        "localforge.llama_cpp",
        "",
        "b11609-abc",
    ),
    "llama_cpp_owned_by": (
        {"/v1/models": _models("q.gguf", owned_by="llamacpp")},
        "localforge.llama_cpp",
        "",
        None,
    ),
    "vllm": (
        {
            "/version": (200, {"version": "0.10.2"}),
            "/v1/models": _models("meta/llama", owned_by="vllm"),
        },
        "localforge.vllm",
        "",
        "0.10.2",
    ),
    "sglang_owned_by": (
        {"/v1/models": _models("qwen", owned_by="sglang")},
        "localforge.sglang",
        "",
        None,
    ),
    "sglang_model_info": (
        {
            "/v1/models": _models("qwen"),
            "/get_model_info": (200, {"model_path": "x", "is_generation": True}),
        },
        "localforge.sglang",
        "",
        None,
    ),
    "tgi": (
        {
            "/info": (200, {"model_id": "bigscience/bloom", "version": "3.3.7"}),
            "/v1/models": _models("tgi"),
        },
        "localforge.tgi",
        "",
        "3.3.7",
    ),
    "localai": (
        {
            "/readyz": (200, "OK"),
            "/version": (200, {"version": "4.11.0"}),
            "/v1/models": _models("phi-2"),
        },
        "localforge.localai",
        "",
        "4.11.0",
    ),
    "lmdeploy": (
        {"/v1/models": _models("internlm", owned_by="lmdeploy")},
        "localforge.lmdeploy",
        "",
        None,
    ),
    "generic": (
        {"/v1/models": _models("some-model", owned_by="organization_owner")},
        "localforge.openai_compatible",
        "/v1",
        None,
    ),
}


@pytest.mark.parametrize("name", sorted(ENGINES))
def test_each_engine_is_identified(name: str) -> None:
    routes, adapter_id, suffix, version = ENGINES[name]
    result = _scan({8080: routes}, [8080])

    assert [entry.model_dump() for entry in result.scanned] == [{"port": 8080, "status": "found"}]
    (candidate,) = result.candidates
    assert candidate.adapter_id == adapter_id
    assert candidate.endpoint_url == f"http://127.0.0.1:8080{suffix}"
    assert candidate.engine_version == version
    assert candidate.models
    assert candidate.evidence.startswith("GET /")
    assert candidate.display_name == _implemented()[adapter_id]
    assert candidate.existing_profile_ids == []


def test_ollama_falls_back_to_tags_for_models() -> None:
    routes: Routes = {
        "/api/version": (200, {"version": "0.40.3"}),
        "/api/tags": (200, {"models": [{"name": "llama3.2:1b"}, {"bad": 1}]}),
    }
    (candidate,) = _scan({11434: routes}, [11434]).candidates
    assert candidate.models == ["llama3.2:1b"]


def test_unrecognized_no_response_and_self_skip() -> None:
    servers: dict[int, Routes] = {
        8081: {"/": (200, "<html>hello</html>")},
        8000: {
            "/api/v1/health": (
                200,
                {
                    "status": "ok",
                    "api_version": "v1",
                    "app_version": "0.1.0",
                    "database": {"status": "ready", "schema_version": 5},
                },
            )
        },
    }
    seen: list[httpx2.Request] = []
    result = _scan(
        servers,
        [8081, 5001, 8000, 9931],
        transport=_transport(servers, seen),
        self_ports=[9931],
    )
    assert [(entry.port, entry.status) for entry in result.scanned] == [
        (8081, "unrecognized"),
        (5001, "no_response"),
        (8000, "skipped_self"),
        (9931, "skipped_self"),
    ]
    assert result.candidates == []
    assert all(request.url.port != 9931 for request in seen)
    # LocalForge itself is recognized from its health route without further probing.
    assert [r.url.path for r in seen if r.url.port == 8000] == ["/api/v1/health"]


def test_protocol_errors_count_as_unrecognized() -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RemoteProtocolError("not http", request=request)

    result = _scan({}, [6379], transport=_transport({}, raw={6379: broken}))
    assert result.scanned[0].status == "unrecognized"


def test_oversized_responses_are_not_parsed() -> None:
    huge = {"data": [{"id": "x" * 1000, "owned_by": "vllm"} for _ in range(400)]}
    result = _scan({8000: {"/v1/models": (200, huge)}}, [8000])
    assert result.scanned[0].status == "unrecognized"


def test_hanging_ports_do_not_hang_the_scan() -> None:
    servers: dict[int, Routes] = {11434: ENGINES["ollama"][0]}
    started = time.perf_counter()
    result = _scan(
        servers,
        [11434, 8080],
        transport=_transport(servers, hang_ports=frozenset({8080})),
        port_budget_seconds=0.3,
    )
    assert time.perf_counter() - started < 3
    assert [(entry.port, entry.status) for entry in result.scanned] == [
        (11434, "found"),
        (8080, "no_response"),
    ]
    assert len(result.candidates) == 1


def test_planned_or_missing_adapters_are_never_suggested() -> None:
    result = asyncio.run(
        discover_engines(
            implemented_adapters={"localforge.openai_compatible": "OpenAI-compatible endpoint"},
            ports=[8000],
            transport=_transport({8000: ENGINES["vllm"][0]}),
        )
    )
    (candidate,) = result.candidates
    assert candidate.adapter_id == "localforge.openai_compatible"
    assert candidate.endpoint_url == "http://127.0.0.1:8000/v1"


def test_parse_ports() -> None:
    assert parse_ports(None)[0] == 11434
    assert parse_ports(" 8080, 5001,8080 ") == [8080, 5001]
    for bad in ("0", "65536", "abc", "-1", "", " , ", "1.5"):
        with pytest.raises(ValueError):
            parse_ports(bad)
    with pytest.raises(ValueError):
        parse_ports(",".join(str(port) for port in range(1, MAX_DISCOVERY_PORTS + 2)))
    assert len(parse_ports(",".join(str(p) for p in range(1, MAX_DISCOVERY_PORTS + 1)))) == 32


def test_matching_profile_ids_normalizes_endpoints() -> None:
    ids = [uuid4() for _ in range(6)]
    saved = [
        SavedEndpoint(ids[0], "localforge.ollama", "http://localhost:11434/"),
        SavedEndpoint(ids[1], "localforge.ollama", "http://127.0.0.1:11434/v1"),
        SavedEndpoint(ids[2], "localforge.llama_cpp", "http://127.0.0.1:11434"),
        SavedEndpoint(ids[3], "localforge.ollama", "http://127.0.0.1:11435"),
        SavedEndpoint(ids[4], "localforge.ollama", None),
        SavedEndpoint(ids[5], "localforge.ollama", "not a url"),
    ]
    assert matching_profile_ids("localforge.ollama", "http://127.0.0.1:11434", saved) == ids[:2]

    generic = [
        SavedEndpoint(ids[0], "localforge.openai_compatible", "http://localhost:1234/v1/"),
        SavedEndpoint(ids[1], "localforge.openai_compatible", "http://127.0.0.1:1234"),
    ]
    assert matching_profile_ids(
        "localforge.openai_compatible", "http://127.0.0.1:1234/v1", generic
    ) == [ids[0]]


def _api_client(tmp_path) -> TestClient:  # type: ignore[no-untyped-def]
    app = create_app(
        AppSettings(
            data_directory=tmp_path,
            session_token_factory=lambda: "test-token",
        )
    )
    return TestClient(app, base_url="http://127.0.0.1")


def test_discover_route_reports_candidates_and_existing_profiles(tmp_path) -> None:  # type: ignore[no-untyped-def]
    servers: dict[int, Routes] = {11434: ENGINES["ollama"][0], 1234: ENGINES["generic"][0]}
    seen: list[httpx2.Request] = []
    with _api_client(tmp_path) as client:
        client.app.state.engine_discovery_transport = _transport(servers, seen)  # type: ignore[attr-defined]
        session = client.get("/api/v1/session").json()
        client.headers.update({session["header_name"]: session["token"]})
        created = client.post(
            "/api/v1/engines",
            json={
                "label": "Local Ollama",
                "adapter_id": "localforge.ollama",
                "integration_mode": "attached_endpoint",
                "endpoint_url": "http://localhost:11434",
            },
        )
        assert created.status_code == 201, created.text
        del client.headers[session["header_name"]]

        response = client.get("/api/v1/engines/discover")
        assert response.status_code == 200, response.text
        body = response.json()

    assert set(body) >= {"scanned", "candidates", "duration_ms"}
    assert isinstance(body["duration_ms"], int)
    statuses = {entry["port"]: entry["status"] for entry in body["scanned"]}
    assert statuses[11434] == "found"
    assert statuses[1234] == "found"
    assert statuses[8080] == "no_response"
    by_adapter = {candidate["adapter_id"]: candidate for candidate in body["candidates"]}
    ollama = by_adapter["localforge.ollama"]
    assert ollama == {
        "adapter_id": "localforge.ollama",
        "display_name": "Ollama",
        "endpoint_url": "http://127.0.0.1:11434",
        "port": 11434,
        "engine_version": "0.40.3",
        "models": ["qwen2.5:3b"],
        "evidence": "GET /api/version returned an Ollama version",
        "existing_profile_ids": [created.json()["id"]],
    }
    assert by_adapter["localforge.openai_compatible"]["endpoint_url"] == "http://127.0.0.1:1234/v1"
    assert all(request.method == "GET" for request in seen)


def test_discover_route_accepts_and_validates_port_override(tmp_path) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx2.Request] = []
    with _api_client(tmp_path) as client:
        client.app.state.engine_discovery_transport = _transport({}, seen)  # type: ignore[attr-defined]
        response = client.get("/api/v1/engines/discover", params={"ports": "8080,5001"})
        assert response.status_code == 200
        assert [entry["port"] for entry in response.json()["scanned"]] == [8080, 5001]
        assert {request.url.port for request in seen} == {8080, 5001}

        for bad in ("70000", "abc", "0", ",".join(str(p) for p in range(1000, 1033))):
            rejected = client.get("/api/v1/engines/discover", params={"ports": bad})
            assert rejected.status_code == 422
            assert rejected.json()["detail"]["code"] == "invalid_ports"


def test_discover_route_skips_its_own_port_and_rejects_remote_hosts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx2.Request] = []
    app = create_app(AppSettings(data_directory=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8123") as client:
        client.app.state.engine_discovery_transport = _transport({}, seen)  # type: ignore[attr-defined]
        response = client.get("/api/v1/engines/discover", params={"ports": "8123"})
        assert response.status_code == 200
        assert response.json()["scanned"] == [{"port": 8123, "status": "skipped_self"}]
        assert seen == []

    with TestClient(app, base_url="http://example.com") as remote:
        remote.app.state.engine_discovery_transport = _transport({}, seen)  # type: ignore[attr-defined]
        rejected = remote.get("/api/v1/engines/discover")
        assert rejected.status_code == 403
        assert rejected.json()["detail"]["code"] == "loopback_only"


def test_discovery_json_shape_is_stable() -> None:
    result = _scan({11434: ENGINES["ollama"][0]}, [11434])
    payload = json.loads(result.model_dump_json())
    assert set(payload["candidates"][0]) == {
        "adapter_id",
        "display_name",
        "endpoint_url",
        "port",
        "engine_version",
        "models",
        "evidence",
        "existing_profile_ids",
    }
