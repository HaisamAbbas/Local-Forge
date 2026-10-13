"""Persisted workspace settings, storage status, and test-fixture engine gating."""

from collections import namedtuple

import pytest
from fastapi.testclient import TestClient

from localforge.adapters.catalog import AdapterCatalog, default_adapter_catalog
from localforge.adapters.fake import FAKE_ADAPTER_ID, FakeEngineAdapter, FakeEngineScenario
from localforge.api.main import AppSettings, create_app
from localforge.workspace_settings import TEST_FIXTURE_DEFAULT_ENVIRONMENT

DiskUsage = namedtuple("DiskUsage", "total used free")


@pytest.fixture(autouse=True)
def _fixtures_hidden_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TEST_FIXTURE_DEFAULT_ENVIRONMENT, raising=False)


def _fake_catalog() -> AdapterCatalog:
    catalog = AdapterCatalog()
    catalog.register(FakeEngineAdapter(FakeEngineScenario()))
    return catalog


def _client(tmp_path, catalog_factory=_fake_catalog) -> TestClient:
    app = create_app(
        AppSettings(
            data_directory=tmp_path,
            adapter_catalog_factory=catalog_factory,
            session_token_factory=lambda: "settings-test-session",
        )
    )
    return TestClient(app, base_url="http://127.0.0.1")


def _authorize(client: TestClient) -> None:
    session = client.get("/api/v1/session").json()
    client.headers.update(
        {session["header_name"]: session["token"], "Origin": "http://127.0.0.1:5173"}
    )


def test_settings_defaults_include_storage_status(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/settings")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["saved"] is False
    assert payload["settings"] == {
        "show_test_fixture_engines": False,
        "default_retain_prompt_output": False,
        "default_request_timeout_seconds": 120,
        "default_warmup_count": 2,
    }
    assert payload["defaults"] == payload["settings"]
    assert payload["test_fixture_adapter_ids"] == [FAKE_ADAPTER_ID]
    storage = payload["storage"]
    assert storage["data_directory"] == str(tmp_path.resolve())
    assert storage["database_path"].endswith("localforge.sqlite3")
    assert isinstance(storage["free_bytes"], int)
    assert storage["total_bytes"] >= storage["free_bytes"]
    assert storage["low_space_threshold_bytes"] == 1024**3
    assert storage["low_space"] is (storage["free_bytes"] < 1024**3)


def test_storage_status_flags_low_space_and_reports_unknown(tmp_path, monkeypatch) -> None:
    with _client(tmp_path) as client:
        monkeypatch.setattr(
            "localforge.api.settings.shutil.disk_usage",
            lambda _path: DiskUsage(total=100 * 1024**3, used=0, free=512 * 1024**2),
        )
        low = client.get("/api/v1/settings").json()["storage"]

        def unavailable(_path):
            raise OSError("volume unavailable")

        monkeypatch.setattr("localforge.api.settings.shutil.disk_usage", unavailable)
        unknown = client.get("/api/v1/settings").json()["storage"]

    assert low["free_bytes"] == 512 * 1024**2
    assert low["low_space"] is True
    assert unknown["free_bytes"] is None
    assert unknown["total_bytes"] is None
    assert unknown["low_space"] is None


def test_settings_mutations_require_the_local_session(tmp_path) -> None:
    with _client(tmp_path) as client:
        put = client.put("/api/v1/settings", json={"default_warmup_count": 3})
        patch = client.patch("/api/v1/settings", json={"default_warmup_count": 3})

    assert put.status_code == 403
    assert patch.status_code == 403
    assert put.json()["detail"]["code"] == "local_session_required"


def test_settings_validation_rejects_unknown_and_out_of_range_values(tmp_path) -> None:
    with _client(tmp_path) as client:
        _authorize(client)
        unknown = client.put("/api/v1/settings", json={"telemetry": True})
        timeout = client.patch("/api/v1/settings", json={"default_request_timeout_seconds": 0})
        warmup = client.put("/api/v1/settings", json={"default_warmup_count": -1})
        wrong_type = client.patch("/api/v1/settings", json={"show_test_fixture_engines": "maybe"})
        unchanged = client.get("/api/v1/settings").json()

    for response, field in (
        (unknown, "body.telemetry"),
        (timeout, "body.default_request_timeout_seconds"),
        (warmup, "body.default_warmup_count"),
        (wrong_type, "body.show_test_fixture_engines"),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_request"
        assert field in [item["field"] for item in response.json()["detail"]["fields"]]
    assert unchanged["saved"] is False


def test_settings_patch_merges_put_replaces_and_values_persist(tmp_path) -> None:
    with _client(tmp_path) as client:
        _authorize(client)
        patched = client.patch(
            "/api/v1/settings",
            json={"default_warmup_count": 5, "default_retain_prompt_output": True},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["saved"] is True
        assert patched.json()["settings"]["default_warmup_count"] == 5
        assert patched.json()["settings"]["default_retain_prompt_output"] is True
        assert patched.json()["settings"]["default_request_timeout_seconds"] == 120

        second = client.patch("/api/v1/settings", json={"default_request_timeout_seconds": 30.5})
        assert second.json()["settings"]["default_warmup_count"] == 5
        assert second.json()["settings"]["default_request_timeout_seconds"] == 30.5

    with _client(tmp_path) as restarted:
        persisted = restarted.get("/api/v1/settings").json()["settings"]
        _authorize(restarted)
        replaced = restarted.put("/api/v1/settings", json={"default_warmup_count": 0}).json()

    assert persisted["default_warmup_count"] == 5
    assert persisted["default_request_timeout_seconds"] == 30.5
    assert replaced["settings"] == replaced["defaults"] | {"default_warmup_count": 0}


def test_catalog_hides_test_fixtures_until_enabled(tmp_path) -> None:
    with _client(tmp_path, catalog_factory=default_adapter_catalog) as client:
        _authorize(client)
        hidden = [item["adapter_id"] for item in client.get("/api/v1/engines/catalog").json()]
        client.patch("/api/v1/settings", json={"show_test_fixture_engines": True})
        shown = [item["adapter_id"] for item in client.get("/api/v1/engines/catalog").json()]

    assert FAKE_ADAPTER_ID not in hidden
    assert "localforge.llama_cpp" in hidden
    assert FAKE_ADAPTER_ID in shown
    assert len(shown) == len(hidden) + 1


def test_environment_can_default_fixture_visibility_before_a_save(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(TEST_FIXTURE_DEFAULT_ENVIRONMENT, "1")
    with _client(tmp_path) as client:
        _authorize(client)
        before = client.get("/api/v1/settings").json()["settings"]
        client.patch("/api/v1/settings", json={"show_test_fixture_engines": False})
        after = client.get("/api/v1/settings").json()["settings"]

    assert before["show_test_fixture_engines"] is True
    assert after["show_test_fixture_engines"] is False


def _ready_fake_engine(client: TestClient) -> tuple[str, str]:
    created = client.post(
        "/api/v1/engines",
        json={
            "label": "Local fake",
            "adapter_id": FAKE_ADAPTER_ID,
            "integration_mode": "attached_endpoint",
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    assert client.post(f"/api/v1/engines/{profile_id}/probe", json={}).status_code == 200
    assert client.post(f"/api/v1/engines/{profile_id}/enable").status_code == 200
    model = client.get(f"/api/v1/engines/{profile_id}/models").json()[0]
    return profile_id, model["id"]


def test_fixture_engine_plans_are_blocked_unless_enabled(tmp_path) -> None:
    with _client(tmp_path) as client:
        _authorize(client)
        engine_id, model_id = _ready_fake_engine(client)
        workload = client.post(
            "/api/v1/workloads",
            json={
                "name": "Fixture workload",
                "kind": "chat",
                "input_mode": "synthetic",
                "request_count": 2,
            },
        )
        assert workload.status_code == 201, workload.text
        body = {
            "name": "Fixture plan",
            "engine_profile_id": engine_id,
            "model_artifact_id": model_id,
            "workload_id": workload.json()["id"],
        }

        blocked_preflight = client.post("/api/v1/benchmark-plans/preflight", json=body)
        blocked_plan = client.post("/api/v1/benchmark-plans", json=body)

        client.patch(
            "/api/v1/settings",
            json={"show_test_fixture_engines": True, "default_request_timeout_seconds": 45},
        )
        allowed_preflight = client.post("/api/v1/benchmark-plans/preflight", json=body)
        allowed_plan = client.post("/api/v1/benchmark-plans", json=body)

        client.patch("/api/v1/settings", json={"show_test_fixture_engines": False})
        run = client.post("/api/v1/runs", json={"plan_id": allowed_plan.json()["plan"]["id"]})
        saved_preflight = client.post(
            f"/api/v1/benchmark-plans/{allowed_plan.json()['plan']['id']}/preflight"
        )
        plans = client.get("/api/v1/benchmark-plans")

    assert blocked_preflight.status_code == 200
    assert blocked_preflight.json()["valid"] is False
    assert "test_fixture_engine_disabled" in [
        issue["code"] for issue in blocked_preflight.json()["blockers"]
    ]
    assert blocked_plan.status_code == 201
    assert blocked_plan.json()["plan"]["status"] == "blocked"
    assert blocked_plan.json()["plan"]["timeout_seconds"] == 120

    assert allowed_preflight.json()["valid"] is True, allowed_preflight.text
    assert allowed_plan.json()["plan"]["status"] == "ready"
    assert allowed_plan.json()["plan"]["timeout_seconds"] == 45

    assert run.status_code == 409
    assert run.json()["detail"]["code"] == "test_fixture_engine_disabled"
    assert saved_preflight.json()["valid"] is False
    assert plans.status_code == 200
    assert len(plans.json()) == 2
