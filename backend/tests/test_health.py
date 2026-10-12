"""Health endpoint startup and response tests."""

from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app


def test_health_reports_api_and_initialized_database(tmp_path) -> None:
    client = TestClient(create_app(AppSettings(data_directory=tmp_path)))

    with client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "api_version": "v1",
        "app_version": "0.1.0",
        "database": {"status": "ready", "schema_version": 4},
    }


def test_versioned_health_route_is_available(tmp_path) -> None:
    with TestClient(create_app(AppSettings(data_directory=tmp_path))) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["database"]["schema_version"] == 4
