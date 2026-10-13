"""The local API exposes the shared adapter manifest catalog."""

from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app


def test_engine_catalog_route_lists_all_registered_manifests(tmp_path) -> None:
    with TestClient(create_app(AppSettings(data_directory=tmp_path))) as client:
        response = client.get("/api/v1/engines/catalog")

    assert response.status_code == 200
    manifests = response.json()
    assert len(manifests) == 3
    fake = next(item for item in manifests if item["adapter_id"] == "localforge.fake")
    assert fake["adapter_version"] == "1.0.0"
    assert fake["category"] == "test_fixture"
    assert "managed_process" in fake["declared_capabilities"]
    assert "Does not load or evaluate a language model." in fake["known_limitations"]
    compatible = next(
        item for item in manifests if item["adapter_id"] == "localforge.openai_compatible"
    )
    assert compatible["category"] == "openai_compatible"
    assert compatible["integration_modes"] == ["attached_endpoint"]
    llama_cpp = next(item for item in manifests if item["adapter_id"] == "localforge.llama_cpp")
    assert llama_cpp["integration_modes"] == ["attached_endpoint", "managed_process"]
    assert "b11580" in llama_cpp["description"]
