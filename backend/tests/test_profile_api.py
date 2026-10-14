"""Machine and engine profile API behavior, privacy, and local-origin protection."""

import asyncio
import os
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.fake import (
    FAKE_ADAPTER_ID,
    FakeEngineAdapter,
    FakeEngineScenario,
)
from localforge.adapters.llama_cpp import LLAMA_CPP_ADAPTER_ID, LlamaCppEngineAdapter
from localforge.api.dependencies import engine_profile_mutation_lock
from localforge.api.main import AppSettings, create_app
from localforge.core.models import EngineProfile, EngineProfileStatus
from localforge.security.local_session import local_session_token_matches
from localforge.storage.database import open_database
from localforge.storage.repositories import RecordRepository


def _client(tmp_path, scenario: FakeEngineScenario | None = None):
    scenario = scenario or FakeEngineScenario()
    catalogs: list[AdapterCatalog] = []

    def catalog_factory() -> AdapterCatalog:
        catalog = AdapterCatalog()
        catalog.register(FakeEngineAdapter(scenario))
        catalogs.append(catalog)
        return catalog

    app = create_app(
        AppSettings(
            data_directory=tmp_path,
            adapter_catalog_factory=catalog_factory,
            session_token_factory=lambda: "test-local-session-token",
        )
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, app, scenario, catalogs


def _authorize_mutations(client: TestClient) -> None:
    session = client.get("/api/v1/session")
    assert session.status_code == 200
    assert session.headers["cache-control"] == "no-store"
    payload = session.json()
    client.headers.update(
        {
            payload["header_name"]: payload["token"],
            "Origin": "http://127.0.0.1:5173",
        }
    )


def _create_profile(client: TestClient, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "label": "Test engine",
        "adapter_id": FAKE_ADAPTER_ID,
        "integration_mode": "attached_endpoint",
    }
    body.update(overrides)
    response = client.post("/api/v1/engines", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_engine_profile_mutation_lock_serializes_same_profile_operations() -> None:
    async def exercise_lock() -> None:
        app = FastAPI()
        app.state.engine_profile_lifecycle_locks = {}
        request = Request({"type": "http", "app": app})
        profile_id = UUID("02f2ab7a-bfe1-49d3-a765-61bb887ea610")
        first_entered = asyncio.Event()
        second_attempted = asyncio.Event()
        second_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def mutate(*, second: bool) -> None:
            if second:
                second_attempted.set()
            async with asynccontextmanager(engine_profile_mutation_lock)(profile_id, request):
                if second:
                    second_entered.set()
                else:
                    first_entered.set()
                    await release_first.wait()

        first = asyncio.create_task(mutate(second=False))
        await first_entered.wait()
        second = asyncio.create_task(mutate(second=True))
        await second_attempted.wait()
        await asyncio.sleep(0)
        assert not second_entered.is_set()

        release_first.set()
        await asyncio.gather(first, second)
        assert second_entered.is_set()

    asyncio.run(exercise_lock())


def test_mutations_require_a_local_session_and_reject_foreign_origins(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    with client:
        cross_origin_session = client.get(
            "/api/v1/session",
            headers={"Origin": "https://attacker.example"},
        )
        assert cross_origin_session.status_code == 200
        assert "access-control-allow-origin" not in cross_origin_session.headers

        denied_without_token = client.post(
            "/api/v1/machine-profiles",
            json={"label": "Remote target"},
        )
        assert denied_without_token.status_code == 403
        assert denied_without_token.json()["detail"]["code"] == "local_session_required"

        _authorize_mutations(client)
        client.headers["Origin"] = "https://attacker.example"
        denied_foreign_origin = client.post(
            "/api/v1/machine-profiles",
            json={"label": "Remote target"},
        )
        assert denied_foreign_origin.status_code == 403
        assert denied_foreign_origin.json()["detail"]["code"] == "origin_rejected"


def test_every_mutation_family_requires_a_local_session_and_loopback_host(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    mutation_requests = (
        ("POST", "/api/v1/datasets/import"),
        ("POST", "/api/v1/workloads"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/experiments"),
        ("POST", "/api/v1/reports"),
        ("PUT", "/api/v1/machine-profiles/current"),
        ("PATCH", f"/api/v1/engines/{UUID(int=1)}"),
        ("DELETE", f"/api/v1/engines/{UUID(int=1)}"),
    )
    with client:
        for method, path in mutation_requests:
            response = client.request(method, path)
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["detail"]["code"] == "local_session_required"

        _authorize_mutations(client)
        denied_foreign_host = client.post(
            "/api/v1/workloads",
            headers={"Host": "attacker.example"},
            json={},
        )
        assert denied_foreign_host.status_code == 403
        assert denied_foreign_host.json()["detail"]["code"] == "loopback_only"


def test_session_token_comparison_rejects_non_ascii_values_without_error() -> None:
    assert local_session_token_matches("é", "valid-ascii-token") is False
    assert local_session_token_matches("valid-ascii-token", "valid-ascii-token") is True


def test_system_endpoint_and_manual_machine_profiles_keep_provenance(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    with client:
        detected = client.get("/api/v1/system")
        assert detected.status_code == 200
        detected_profile = detected.json()
        assert detected_profile["status"] in {"detected", "partially_detected"}
        assert "provenance" in detected_profile

        _authorize_mutations(client)
        created = client.post(
            "/api/v1/machine-profiles",
            json={
                "label": "Remote GPU host",
                "hardware": {
                    "os_name": "Linux",
                    "cpu_model": "User supplied CPU",
                    "cpu_logical_cores": 16,
                    "memory_total_bytes": 64 * 1024**3,
                },
            },
        )
        assert created.status_code == 200
        profile = created.json()
        assert profile["status"] == "manual_unverified"
        assert profile["provenance"]["cpu_model"]["source"] == "user_supplied"
        assert profile["hardware"]["accelerators"] is None
        assert client.get("/api/v1/machine-profiles").json()[0]["id"] == profile["id"]


def test_current_detected_machine_profile_is_saved_and_reused(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    with client:
        _authorize_mutations(client)
        first = client.put("/api/v1/machine-profiles/current")
        second = client.put("/api/v1/machine-profiles/current")
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["status"] in {"detected", "partially_detected"}
        assert client.get("/api/v1/machine-profiles").json()[0]["id"] == first.json()["id"]

        engine = _create_profile(client, machine_profile_id=first.json()["id"])
        assert engine["machine_profile_id"] == first.json()["id"]


def test_engine_profile_crud_probe_models_and_credential_masking(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    credential_reference = "keyring://localforge/test-api"
    with client:
        _authorize_mutations(client)
        profile = _create_profile(
            client,
            engine_display_name="Deterministic test target",
            endpoint_url="http://127.0.0.1:12345",
            secret_refs={"api_token": credential_reference},
        )
        profile_id = profile["id"]
        assert profile["adapter_version"] == "1.0.0"
        assert profile["integration_mode"] == "attached_endpoint"
        assert profile["credential_configured"] is True
        assert "secret_refs" not in profile
        assert credential_reference not in str(profile)

        listed = client.get("/api/v1/engines")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == profile_id
        assert client.get(f"/api/v1/engines/{profile_id}").status_code == 200

        changed = client.patch(
            f"/api/v1/engines/{profile_id}",
            json={"label": "Renamed target"},
        )
        assert changed.status_code == 200
        assert changed.json()["label"] == "Renamed target"

        probed = client.post(f"/api/v1/engines/{profile_id}/probe", json={})
        assert probed.status_code == 200, probed.text
        assert probed.json()["result"]["status"] == "succeeded"
        assert probed.json()["profile"]["engine_version"] == "fake-1"
        assert probed.json()["profile"]["capabilities"]["streaming"]["state"] == "supported"
        models = client.get(f"/api/v1/engines/{profile_id}/models")
        assert models.status_code == 200
        assert models.json()[0]["status"] == "available"
        assert models.json()[0]["display_name"] == "LocalForge Test Model"

        enabled = client.post(f"/api/v1/engines/{profile_id}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "enabled"
        disabled = client.post(f"/api/v1/engines/{profile_id}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

        deleted = client.delete(f"/api/v1/engines/{profile_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/engines/{profile_id}").status_code == 404


def test_model_inventory_failure_does_not_erase_health_or_known_models(tmp_path) -> None:
    client, _, scenario, _ = _client(tmp_path)
    with client:
        _authorize_mutations(client)
        profile = _create_profile(client)
        profile_id = profile["id"]
        first_probe = client.post(f"/api/v1/engines/{profile_id}/probe", json={})
        assert first_probe.status_code == 200

        scenario.model_inventory_failure = "model listing route is unavailable"
        partial_probe = client.post(f"/api/v1/engines/{profile_id}/probe", json={})
        assert partial_probe.status_code == 200
        checks = partial_probe.json()["result"]["checks"]
        assert checks["health"]["state"] == "supported"
        assert checks["model_inventory"]["state"] == "unknown"
        assert partial_probe.json()["result"]["diagnostics"][0]["code"] == (
            "model_inventory_unavailable"
        )
        models = client.get(f"/api/v1/engines/{profile_id}/models").json()
        assert len(models) == 1
        assert models[0]["status"] == "available"


def test_auth_failure_is_distinct_and_secret_reference_is_never_returned(tmp_path) -> None:
    scenario = FakeEngineScenario(
        probe_failure="The test adapter received an unauthorized response.",
        probe_failure_code="unauthorized",
    )
    client, _, _, _ = _client(tmp_path, scenario)
    reference = "keyring://localforge/auth-test"
    with client:
        _authorize_mutations(client)
        profile = _create_profile(
            client,
            secret_refs={"api_token": reference},
        )
        result = client.post(f"/api/v1/engines/{profile['id']}/probe", json={})
        assert result.status_code == 200
        assert result.json()["result"]["status"] == "failed"
        assert result.json()["result"]["diagnostics"][0]["code"] == "unauthorized"
        assert result.json()["profile"]["status"] == "needs_attention"
        assert reference not in result.text
        assert "secret_refs" not in result.text


def test_successful_probe_recovers_profile_after_auth_failure(tmp_path) -> None:
    scenario = FakeEngineScenario(
        probe_failure="The test adapter received an unauthorized response.",
        probe_failure_code="unauthorized",
    )
    client, _, scenario, _ = _client(tmp_path, scenario)
    with client:
        _authorize_mutations(client)
        profile = _create_profile(client)
        profile_id = profile["id"]

        failed = client.post(f"/api/v1/engines/{profile_id}/probe", json={})
        assert failed.status_code == 200
        assert failed.json()["profile"]["status"] == "needs_attention"
        assert failed.json()["result"]["reachable"]["state"] == "supported"
        assert failed.json()["result"]["health"]["state"] == "unsupported"

        scenario.probe_failure = None
        recovered = client.post(f"/api/v1/engines/{profile_id}/probe", json={})
        assert recovered.status_code == 200
        assert recovered.json()["profile"]["status"] == "disabled"
        enabled = client.post(f"/api/v1/engines/{profile_id}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "enabled"


def test_invalid_endpoint_is_rejected_without_echoing_submitted_secret(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    secret_url = "http://inference.example/v1?api_key=very-secret-value"
    with client:
        _authorize_mutations(client)
        response = client.post(
            "/api/v1/engines",
            json={
                "label": "Unsafe endpoint",
                "adapter_id": FAKE_ADAPTER_ID,
                "integration_mode": "attached_endpoint",
                "endpoint_url": secret_url,
            },
        )
        assert response.status_code == 422
        assert "very-secret-value" not in response.text
        assert "http://inference.example" not in response.text


def test_plaintext_api_token_in_profile_config_is_rejected(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    plaintext_token = "never-store-this-token"
    with client:
        _authorize_mutations(client)
        response = client.post(
            "/api/v1/engines",
            json={
                "label": "Unsafe token profile",
                "adapter_id": FAKE_ADAPTER_ID,
                "integration_mode": "attached_endpoint",
                "config": {"api_token": plaintext_token},
            },
        )
        assert response.status_code == 422
        assert plaintext_token not in response.text
        assert client.get("/api/v1/engines").json() == []


def test_remote_probe_discloses_egress_before_any_adapter_call(tmp_path) -> None:
    client, _, scenario, _ = _client(tmp_path)
    with client:
        _authorize_mutations(client)
        profile = _create_profile(
            client,
            endpoint_url="https://inference.example/v1",
        )
        profile_id = profile["id"]
        blocked = client.post(
            f"/api/v1/engines/{profile_id}/probe",
            json={"confirm_remote_egress": False},
        )
        assert blocked.status_code == 409
        disclosure = blocked.json()["detail"]
        assert disclosure["code"] == "remote_confirmation_required"
        assert disclosure["host"] == "inference.example"
        assert disclosure["request_will_be_sent"] is False
        assert "one or more short chat-completion checks" in disclosure["message"]
        assert "usage-count check" in disclosure["message"]
        assert "will leave this machine" in disclosure["message"]
        assert not scenario.probe_started.is_set()

        confirmed = client.post(
            f"/api/v1/engines/{profile_id}/probe",
            json={"confirm_remote_egress": True},
        )
        assert confirmed.status_code == 200
        assert scenario.probe_started.is_set()
        assert confirmed.json()["profile"]["remote_access_enabled"] is True


def test_managed_profile_start_and_stop_only_use_recorded_adapter_handle(tmp_path) -> None:
    client, app, _, _ = _client(tmp_path)
    with client:
        _authorize_mutations(client)
        profile = _create_profile(client, integration_mode="managed_process")
        profile_id = profile["id"]

        started = client.post(f"/api/v1/engines/{profile_id}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "enabled"
        assert UUID(profile_id) in app.state.engine_process_handles
        blocked_delete = client.delete(f"/api/v1/engines/{profile_id}")
        assert blocked_delete.status_code == 409
        assert blocked_delete.json()["detail"]["code"] == "engine_process_running"

        stopped = client.post(f"/api/v1/engines/{profile_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "disabled"
        assert UUID(profile_id) not in app.state.engine_process_handles
        assert client.delete(f"/api/v1/engines/{profile_id}").status_code == 204


def test_llama_managed_routes_forward_saved_endpoint_and_extension_configuration(tmp_path) -> None:
    class OwnedTestProcess:
        def __init__(self) -> None:
            self.pid = 62431
            self.returncode = None
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    async def run_test() -> None:
        executable = tmp_path / "llama-server.exe"
        model = tmp_path / "model with spaces.gguf"
        executable.write_bytes(b"test process fixture")
        if os.name != "nt":
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        model.write_bytes(b"test model fixture; never passed to a real server")
        adapter = LlamaCppEngineAdapter()
        processes: list[OwnedTestProcess] = []
        captured: dict[str, object] = {}

        async def spawn(arguments: list[str], working_directory: Path) -> OwnedTestProcess:
            process = OwnedTestProcess()
            processes.append(process)
            captured["arguments"] = arguments
            captured["working_directory"] = working_directory
            return process

        async def ready(_process: object, _port: int, _timeout: float) -> bool:
            return True

        adapter._spawn = spawn  # type: ignore[method-assign]
        adapter._port_available = lambda _port: True  # type: ignore[method-assign]
        adapter._wait_until_ready = ready  # type: ignore[method-assign]

        catalogs: list[AdapterCatalog] = []

        def catalog_factory() -> AdapterCatalog:
            catalog = AdapterCatalog()
            catalog.register(adapter)
            catalogs.append(catalog)
            return catalog

        app = create_app(
            AppSettings(
                data_directory=tmp_path / "data",
                adapter_catalog_factory=catalog_factory,
                session_token_factory=lambda: "test-local-session-token",
            )
        )
        client = TestClient(app, base_url="http://127.0.0.1")
        with client:
            _authorize_mutations(client)
            created = client.post(
                "/api/v1/engines",
                json={
                    "label": "Managed llama",
                    "adapter_id": LLAMA_CPP_ADAPTER_ID,
                    "integration_mode": "managed_process",
                    "endpoint_url": "http://127.0.0.1:9931",
                    "engine_extensions": {
                        "llama_cpp": {
                            "executable_path": str(executable),
                            "model_path": str(model),
                            "working_directory": str(tmp_path),
                            "startup_timeout_seconds": 1,
                            "context_size": 4096,
                            "parallelism": 2,
                            "gpu_layers": "all",
                        }
                    },
                },
            )
            assert created.status_code == 201, created.text
            profile_id = created.json()["id"]
            assert created.json()["managed_process_running"] is False

            blocked_probe = client.post(f"/api/v1/engines/{profile_id}/probe", json={})
            assert blocked_probe.status_code == 409
            assert blocked_probe.json()["detail"]["code"] == "managed_process_not_running"
            blocked_enable = client.post(f"/api/v1/engines/{profile_id}/enable")
            assert blocked_enable.status_code == 409
            assert blocked_enable.json()["detail"]["code"] == "managed_process_not_running"

            started = client.post(f"/api/v1/engines/{profile_id}/start")
            assert started.status_code == 200, started.text
            assert started.json()["managed_process_running"] is True
            assert started.json()["status"] == "enabled"
            assert captured["working_directory"] == tmp_path
            arguments = captured["arguments"]
            assert isinstance(arguments, list)
            assert arguments[arguments.index("--model") + 1] == str(model)
            assert arguments[arguments.index("--ctx-size") + 1] == "4096"
            assert arguments[arguments.index("--parallel") + 1] == "2"
            assert arguments[arguments.index("--gpu-layers") + 1] == "all"
            assert client.get("/api/v1/engines").json()[0]["managed_process_running"] is True

            stopped = client.post(f"/api/v1/engines/{profile_id}/stop")
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["managed_process_running"] is False
            assert stopped.json()["status"] == "disabled"

            second_profile = client.post(
                "/api/v1/engines",
                json={
                    "label": "Managed llama shutdown",
                    "adapter_id": LLAMA_CPP_ADAPTER_ID,
                    "integration_mode": "managed_process",
                    "endpoint_url": "http://127.0.0.1:9932",
                    "engine_extensions": {
                        "llama_cpp": {
                            "executable_path": str(executable),
                            "model_path": str(model),
                            "working_directory": str(tmp_path),
                            "startup_timeout_seconds": 1,
                        }
                    },
                },
            )
            assert second_profile.status_code == 201, second_profile.text
            adapter._port_available = lambda _port: False  # type: ignore[method-assign]
            port_conflict = client.post(f"/api/v1/engines/{second_profile.json()['id']}/start")
            assert port_conflict.status_code == 409
            assert "port is already in use" in port_conflict.json()["detail"]["message"]
            adapter._port_available = lambda _port: True  # type: ignore[method-assign]
            assert (
                client.post(f"/api/v1/engines/{second_profile.json()['id']}/start").status_code
                == 200
            )
            assert not processes[-1].terminated

            missing_model = client.post(
                "/api/v1/engines",
                json={
                    "label": "Managed llama missing model",
                    "adapter_id": LLAMA_CPP_ADAPTER_ID,
                    "integration_mode": "managed_process",
                    "endpoint_url": "http://127.0.0.1:9933",
                    "engine_extensions": {
                        "llama_cpp": {
                            "executable_path": str(executable),
                            "model_path": str(tmp_path / "absent.gguf"),
                        }
                    },
                },
            )
            assert missing_model.status_code == 201, missing_model.text
            rejected = client.post(f"/api/v1/engines/{missing_model.json()['id']}/start")
            assert rejected.status_code == 409
            assert rejected.json()["detail"]["code"] == "managed_start_invalid"
            # The adapter's reason reaches the user instead of a generic checklist.
            assert (
                "Select an existing local .gguf model file." in rejected.json()["detail"]["message"]
            )

        assert len(catalogs) == 1
        assert processes[0].terminated
        assert processes[1].terminated

    asyncio.run(run_test())


def test_startup_disables_managed_profile_without_an_owned_process(tmp_path) -> None:
    first_client, first_app, _, _ = _client(tmp_path)
    with first_client:
        _authorize_mutations(first_client)
        profile = _create_profile(first_client, integration_mode="managed_process")

    connection = open_database(first_app.state.database_path)
    try:
        repository = RecordRepository(connection, EngineProfile)
        saved = repository.get(UUID(profile["id"]))
        assert saved is not None
        repository.update(
            EngineProfile.model_validate(
                saved.model_dump(mode="python") | {"status": EngineProfileStatus.ENABLED}
            )
        )
    finally:
        connection.close()

    second_client, _, _, _ = _client(tmp_path)
    with second_client:
        recovered = second_client.get(f"/api/v1/engines/{profile['id']}")
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "disabled"
        assert recovered.json()["managed_process_running"] is False


def test_machine_profile_in_use_cannot_be_deleted(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    with client:
        _authorize_mutations(client)
        machine = client.post("/api/v1/machine-profiles", json={"label": "Manual GPU host"})
        assert machine.status_code == 200
        engine = _create_profile(client, machine_profile_id=machine.json()["id"])
        conflict = client.delete(f"/api/v1/machine-profiles/{machine.json()['id']}")
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "machine_profile_in_use"

        assert client.delete(f"/api/v1/engines/{engine['id']}").status_code == 204
        assert client.delete(f"/api/v1/machine-profiles/{machine.json()['id']}").status_code == 204
