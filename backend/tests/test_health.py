"""Health endpoint startup and response tests."""

from fastapi.testclient import TestClient

from localforge import __version__
from localforge.api.main import AppSettings, create_app


def test_health_reports_api_and_initialized_database(tmp_path) -> None:
    client = TestClient(
        create_app(AppSettings(data_directory=tmp_path)), base_url="http://127.0.0.1"
    )

    with client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "api_version": "v1",
        "app_version": __version__,
        "database": {"status": "ready", "schema_version": 5},
    }


def test_versioned_health_route_is_available(tmp_path) -> None:
    with TestClient(
        create_app(AppSettings(data_directory=tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["database"]["schema_version"] == 5
