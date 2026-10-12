"""Machine and engine profile API behavior, privacy, and local-origin protection."""

import asyncio
from contextlib import asynccontextmanager
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
from localforge.api.dependencies import engine_profile_mutation_lock
from localforge.api.main import AppSettings, create_app


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
