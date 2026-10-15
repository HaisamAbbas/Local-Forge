"""The local API exposes the shared adapter manifest catalog."""

from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app


def test_engine_catalog_route_lists_all_registered_manifests(tmp_path) -> None:
    with TestClient(
        create_app(AppSettings(data_directory=tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.get("/api/v1/engines/catalog")

    assert response.status_code == 200
    manifests = response.json()
    assert len(manifests) == 20
    by_id = {item["adapter_id"]: item for item in manifests}
    assert by_id["localforge.mlx_lm"]["operating_systems"] == ["windows", "linux", "macos"]
    assert by_id["localforge.openvino_genai"]["category"] == "library_pipeline"
    assert by_id["localforge.onnxruntime_genai"]["category"] == "library_pipeline"
    assert by_id["localforge.nvidia_dynamo"]["category"] == "serving_stack"
    assert by_id["localforge.exllamav3"]["implementation_status"] == "planned"
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
    ollama = next(item for item in manifests if item["adapter_id"] == "localforge.ollama")
    assert ollama["integration_modes"] == ["attached_endpoint"]
    assert "server_reported_durations" in ollama["declared_capabilities"]
    vllm = next(item for item in manifests if item["adapter_id"] == "localforge.vllm")
    sglang = next(item for item in manifests if item["adapter_id"] == "localforge.sglang")
    assert vllm["integration_modes"] == ["attached_endpoint"]
    assert sglang["integration_modes"] == ["attached_endpoint"]
    assert vllm["adapter_id"] != sglang["adapter_id"]
    tgi = next(item for item in manifests if item["adapter_id"] == "localforge.tgi")
    assert tgi["maintenance_mode"] is True
    assert tgi["recommended_for_new_managed_install"] is False
    assert tgi["version_tested"] == ["3.3.7 (local API fixture)"]
    triton = next(
        item for item in manifests if item["adapter_id"] == "localforge.triton_tensorrt_llm"
    )
    assert triton["implementation_status"] == "planned"
    assert triton["integration_modes"] == []
