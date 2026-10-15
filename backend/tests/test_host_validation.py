from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app

READ_ROUTES = ["/health", "/api/v1/session", "/api/v1/settings", "/api/v1/runs"]


def _client(tmp_path: Path, base_url: str) -> TestClient:
    return TestClient(create_app(AppSettings(data_directory=tmp_path)), base_url=base_url)


@pytest.mark.parametrize("base_url", ["http://127.0.0.1", "http://localhost", "http://[::1]"])
def test_loopback_hosts_are_served(tmp_path: Path, base_url: str) -> None:
    with _client(tmp_path, base_url) as client:
        for route in READ_ROUTES:
            assert client.get(route).status_code == 200, route


@pytest.mark.parametrize(
    "hostname", ["evil.example", "127.0.0.1.evil.example", "localhost.evil.example", "10.0.0.5"]
)
def test_reads_through_a_foreign_hostname_are_refused(tmp_path: Path, hostname: str) -> None:
    """A page that rebinds its own hostname to this port must not read the token or data."""

    with _client(tmp_path, "http://127.0.0.1") as client:
        for route in READ_ROUTES:
            response = client.get(route, headers={"host": hostname})
            assert response.status_code == 403, (hostname, route)
            assert response.json()["detail"]["code"] == "loopback_only"
            assert "token" not in response.text


def test_mutations_through_a_foreign_hostname_keep_their_own_message(tmp_path: Path) -> None:
    with _client(tmp_path, "http://127.0.0.1") as client:
        response = client.post("/api/v1/engines", json={}, headers={"host": "evil.example"})
    assert response.status_code == 403
    assert response.json()["detail"]["message"] == (
        "Mutations are available only through a loopback host."
    )
