"""The local API exposes the shared adapter manifest catalog."""

from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app


def test_engine_catalog_route_lists_fake_manifest(tmp_path) -> None:
    with TestClient(create_app(AppSettings(data_directory=tmp_path))) as client:
        response = client.get("/api/v1/engines/catalog")

    assert response.status_code == 200
    manifests = response.json()
    assert len(manifests) == 1
    assert manifests[0]["adapter_id"] == "localforge.fake"
    assert manifests[0]["adapter_version"] == "1.0.0"
    assert manifests[0]["category"] == "test_fixture"
    assert "managed_process" in manifests[0]["declared_capabilities"]
    assert "Does not load or evaluate a language model." in manifests[0]["known_limitations"]
