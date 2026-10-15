"""Model catalog schema, model suggestions for different machines, and downloaded detection."""

import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from localforge import cli
from localforge.api.main import AppSettings, create_app
from localforge.core.models import (
    AcceleratorInfo,
    EngineIntegrationMode,
    EngineProfile,
    EngineProfileStatus,
    HardwareSnapshot,
    MachineProfile,
    MachineProfileStatus,
    ModelArtifact,
    ModelArtifactStatus,
)
from localforge.model_catalog import (
    GIB,
    CatalogModel,
    ModelCatalog,
    ModelSuggestion,
    build_model_suggestions,
    fetch_ollama_tags,
    gguf_file_name,
    installed_from_inventory,
    load_model_catalog,
    normalize_ollama_tag,
    render_model_suggestions_text,
    suggest_models,
    suggestion_machine,
)
from localforge.storage.database import initialize_database, open_database
from localforge.storage.repositories import RecordRepository

CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "localforge" / "data" / "model_catalog.json"
)


def _laptop() -> HardwareSnapshot:
    """The development laptop: 31.5 GB RAM, Intel integrated GPU, no discrete GPU."""

    return HardwareSnapshot(
        os_name="Windows",
        os_version="10.0.26200",
        architecture="AMD64",
        cpu_model="Intel(R) Core(TM) Ultra 7 155U",
        memory_total_bytes=33_785_430_016,
        accelerators=[
            AcceleratorInfo(
                name="Intel(R) Graphics", vendor="Intel", integrated=True, memory_kind="shared"
            )
        ],
    )


def _a100_box() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Linux",
        architecture="x86_64",
        cpu_model="AMD EPYC 7763 64-Core Processor",
        memory_total_bytes=512 * GIB,
        accelerators=[
            AcceleratorInfo(
                name="NVIDIA A100-SXM4-80GB",
                vendor="NVIDIA",
                memory_bytes=80 * GIB,
                integrated=False,
                memory_kind="dedicated",
            )
        ],
    )


def _small_laptop() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Windows",
        architecture="AMD64",
        cpu_model="Intel(R) Core(TM) i5-8250U",
        memory_total_bytes=8 * GIB,
        accelerators=[],
    )


def _by_id(items: list[ModelSuggestion]) -> dict[str, ModelSuggestion]:
    return {item.model_id: item for item in items}


# --- Catalog schema -----------------------------------------------------------------------


def test_packaged_catalog_is_valid_and_covers_sizes_and_families() -> None:
    catalog = load_model_catalog()
    assert catalog.schema_version == 1
    assert 25 <= len(catalog.models) <= 45
    params = [model.params_b for model in catalog.models]
    assert min(params) <= 0.6 and max(params) >= 70
    families = {model.family for model in catalog.models}
    for expected in (
        "Qwen2.5",
        "Qwen2.5-Coder",
        "Qwen3",
        "Llama 3.2",
        "Gemma 2",
        "Gemma 3",
        "Phi-4",
        "Mistral",
        "Mixtral",
        "DeepSeek-R1 distill",
        "SmolLM2",
    ):
        assert expected in families
    for model in catalog.models:
        assert model.sources.ollama or model.sources.gguf, model.id
        # Typical Q4 size is roughly 0.5-0.8 GB per billion parameters (plus vision encoders).
        assert 0.4 <= model.q4_size_gb / model.params_b <= 0.95, model.id
        assert model.license
    raw = json.loads(CATALOG_PATH.read_text("utf-8"))
    for entry in raw["models"]:  # never claims benchmark quality scores
        assert not any("score" in key or "benchmark" in key for key in entry)


def test_catalog_rejects_bad_entries() -> None:
    raw = json.loads(CATALOG_PATH.read_text("utf-8"))
    entry = raw["models"][0]

    def invalid(**changes: object) -> None:
        with pytest.raises(ValidationError):
            CatalogModel.model_validate({**entry, **changes})

    invalid(id="Not A Slug")
    invalid(strengths=["chat", "telepathy"])
    invalid(strengths=[])
    invalid(q4_size_gb=0)
    invalid(active_params_b=entry["params_b"] + 1)
    invalid(unexpected="field")
    invalid(sources={"ollama": "qwen2.5"})  # tags must be explicit
    invalid(sources={"gguf": {"repo": "not a repo", "file": "x.gguf"}})
    invalid(sources={"gguf": {"repo": "owner/name", "file": "model.bin"}})
    duplicate = {**raw, "models": [entry, entry]}
    with pytest.raises(ValidationError):
        ModelCatalog.model_validate(duplicate)
    with pytest.raises(ValidationError):
        ModelCatalog.model_validate({**raw, "schema_version": 2})


def test_tag_and_file_normalization() -> None:
    catalog = load_model_catalog()
    assert normalize_ollama_tag("Llama3.2") == "llama3.2:latest"
    assert normalize_ollama_tag("hf.co/owner/repo:Q4_K_M") == "hf.co/owner/repo:q4_k_m"
    llama = catalog.by_ollama_tag("llama3.2")  # bare name == :latest, an alias of 3b
    assert llama is not None and llama.id == "llama-3.2-3b-instruct"
    assert catalog.by_ollama_tag("qwen2.5:3b") is not None
    assert catalog.by_ollama_tag("not-in:catalog") is None
    assert gguf_file_name(r"D:\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf") == (
        "qwen2.5-7b-instruct-q4_k_m.gguf"
    )
    assert gguf_file_name("/srv/models/x.GGUF") == "x.gguf"
    assert gguf_file_name("qwen2.5:3b") is None


# --- Suggestions ------------------------------------------------------------------------------


def test_laptop_with_measured_read_speed_prefers_small_models_and_warns_about_7b() -> None:
    machine = suggestion_machine(_laptop(), read_gib_per_second=19.6)
    assert machine.memory_basis == "system_memory"
    chat = suggest_models(load_model_catalog(), "chat", machine)
    top = chat[0]
    assert top.fit == "fits"
    assert top.params_b <= 4.5
    assert top.speed.basis == "measured"
    assert top.speed.label == "estimated from your runs"
    assert top.speed.tokens_per_second is not None and top.speed.tokens_per_second >= 8
    by_id = _by_id(chat)
    qwen3b = by_id["qwen2.5-3b-instruct"]
    assert qwen3b.speed.tokens_per_second == pytest.approx(19.6 / (1.9e9 / GIB), abs=0.1)
    seven = by_id["qwen2.5-7b-instruct"]
    assert seven.fit == "fits"  # memory is not the limit here ...
    assert seven.rank > qwen3b.rank  # ... speed is
    assert any("usable for batch work, slow for chat" in text for text in seven.cautions)
    seventy = by_id["llama-3.3-70b-instruct"]
    assert seventy.fit == "too_large"
    assert all(item.fit != "too_large" for item in chat[: len(chat) - 3])
    # Models that do not fit are always ranked last.
    fits = [item.fit == "too_large" for item in chat]
    assert fits == sorted(fits)

    coding = suggest_models(load_model_catalog(), "coding", machine)
    assert coding[0].strengths[0] == "coding"
    assert coding[0].params_b <= 4
    assert "Trained specifically for code" in coding[0].reasons


def test_a100_box_suggests_70b_class_models_with_rough_gpu_speed() -> None:
    machine = suggestion_machine(_a100_box())
    assert machine.memory_basis == "gpu_memory"
    assert machine.gpu_bandwidth_gb_s == 2039
    chat = suggest_models(load_model_catalog(), "chat", machine)
    top = chat[0]
    assert top.params_b >= 70
    assert top.fit == "fits"
    assert top.speed.basis == "gpu_bandwidth_rough"
    assert "rough" in top.speed.label and "A100" in top.speed.label
    assert top.speed.tokens_per_second is not None and top.speed.tokens_per_second > 15
    assert all(item.fit != "too_large" for item in chat)
    coding = suggest_models(load_model_catalog(), "coding", machine)
    assert coding[0].model_id == "qwen2.5-coder-32b-instruct"


def test_8gb_laptop_without_runs_gets_tiny_models_and_unknown_speed() -> None:
    machine = suggestion_machine(_small_laptop())
    chat = suggest_models(load_model_catalog(), "chat", machine)
    usable = [item for item in chat if item.fit != "too_large"]
    assert usable and all(item.params_b <= 2 for item in usable)
    assert chat[0].fit == "fits"
    assert chat[0].speed.basis == "unknown" and chat[0].speed.tokens_per_second is None
    assert any("Speed unknown" in text for text in chat[0].cautions)
    seven = _by_id(chat)["qwen2.5-7b-instruct"]
    assert seven.fit == "too_large"
    assert any("Does not fit" in text for text in seven.cautions)


def test_use_cases_filter_and_explain() -> None:
    machine = suggestion_machine(_laptop(), read_gib_per_second=19.6)
    catalog = load_model_catalog()
    reasoning = suggest_models(catalog, "reasoning", machine)
    assert all("reasoning" in item.strengths for item in reasoning)
    slow = [item for item in reasoning if (item.speed.tokens_per_second or 99) < 15]
    assert slow and any("thinking tokens" in text for text in slow[0].cautions)
    documents = suggest_models(catalog, "document_qa", machine)
    assert all(item.context_tokens >= 32768 for item in documents)
    small = suggest_models(catalog, "small_fast", machine)
    assert all(item.params_b <= 4.5 for item in small)
    moe = _by_id(suggest_models(catalog, "chat", machine))["qwen3-30b-a3b"]
    assert moe.speed.tokens_per_second is not None and moe.speed.tokens_per_second > 8
    assert any("Mixture of experts" in text for text in moe.reasons)


# --- Downloaded detection -----------------------------------------------------------------


def _profile(label: str, adapter_id: str, endpoint: str | None, **extra: object) -> EngineProfile:
    return EngineProfile(
        label=label,
        adapter_id=adapter_id,
        integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
        endpoint_url=endpoint,
        status=EngineProfileStatus.ENABLED,
        **extra,  # type: ignore[arg-type]
    )


def test_downloaded_detection_from_live_tags_inventories_and_gguf_paths() -> None:
    ollama = _profile("Local Ollama", "localforge.ollama", "http://127.0.0.1:11434")
    remote = _profile("Remote Ollama", "localforge.ollama", "https://gpu.example.com")
    llama = _profile("llama.cpp", "localforge.llama_cpp", "http://127.0.0.1:8080")
    kobold = _profile(
        "KoboldCpp",
        "localforge.koboldcpp",
        "http://127.0.0.1:5001",
        engine_extensions={"koboldcpp": {"model_path": "/models/gemma-2-2b-it-Q4_K_M.gguf"}},
    )
    artifacts = [
        # Stale saved inventory of the local Ollama; the live tag list wins.
        ModelArtifact(display_name="old", engine_profile_id=ollama.id, model_ref="qwen2.5:14b"),
        ModelArtifact(display_name="remote", engine_profile_id=remote.id, model_ref="qwen2.5:32b"),
        ModelArtifact(
            display_name="gguf",
            engine_profile_id=llama.id,
            model_ref=r"D:\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf",
            status=ModelArtifactStatus.AVAILABLE,
        ),
        ModelArtifact(
            display_name="gone",
            engine_profile_id=llama.id,
            model_ref="/m/Phi-3.5-mini-instruct-Q4_K_M.gguf",
            status=ModelArtifactStatus.MISSING,
        ),
    ]
    installed = installed_from_inventory(
        [ollama, remote, llama, kobold],
        artifacts,
        {"http://127.0.0.1:11434": ["qwen2.5:3b", "llama3.2:latest"]},
    )
    machine = suggestion_machine(_laptop(), read_gib_per_second=19.6)
    chat = _by_id(suggest_models(load_model_catalog(), "chat", machine, installed=installed))
    assert [item.engine_label for item in chat["qwen2.5-3b-instruct"].downloaded] == [
        "Local Ollama"
    ]
    assert chat["llama-3.2-3b-instruct"].downloaded[0].model_ref == "llama3.2:latest"
    assert chat["qwen2.5-7b-instruct"].downloaded[0].source == "gguf"
    assert chat["gemma-2-2b-it"].downloaded[0].engine_label == "KoboldCpp"
    assert chat["qwen2.5-14b-instruct"].downloaded == []  # stale inventory ignored
    assert chat["qwen2.5-32b-instruct"].downloaded == []  # remote machines do not count
    assert chat["phi-3.5-mini-instruct"].downloaded == []  # missing file
    assert chat["qwen2.5-3b-instruct"].reasons[0].startswith("Already downloaded")

    # Without a live list the saved inventory is used.
    offline = installed_from_inventory([ollama], artifacts[:1], {})
    assert offline.locations_for(load_model_catalog().by_ollama_tag("qwen2.5:14b"))  # type: ignore[arg-type]


def test_fetch_ollama_tags_is_read_only_and_skips_unreachable_endpoints() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, str(request.url)))
        if request.url.port == 11434:
            return httpx2.Response(200, json={"models": [{"name": "qwen2.5:3b"}, {"x": 1}]})
        raise httpx2.ConnectError("refused", request=request)

    result = asyncio.run(
        fetch_ollama_tags(
            ["http://127.0.0.1:11434", "http://127.0.0.1:9999"],
            transport=httpx2.MockTransport(handler),
        )
    )
    assert result == {"http://127.0.0.1:11434": ["qwen2.5:3b"]}
    assert {method for method, _url in seen} == {"GET"}


# --- API and CLI ----------------------------------------------------------------------------


def _tags_transport(tags: list[str]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/tags":
            return httpx2.Response(200, json={"models": [{"name": tag} for tag in tags]})
        return httpx2.Response(404)

    return httpx2.MockTransport(handler)


def _seed_machine(tmp_path: Path, hardware: HardwareSnapshot) -> EngineProfile:
    database = tmp_path / "localforge.sqlite3"
    initialize_database(database)
    connection = open_database(database)
    try:
        RecordRepository(connection, MachineProfile).create(
            MachineProfile(
                label="This machine", status=MachineProfileStatus.DETECTED, hardware=hardware
            )
        )
        return RecordRepository(connection, EngineProfile).create(
            _profile("Ollama here", "localforge.ollama", "http://127.0.0.1:11434")
        )
    finally:
        connection.close()


def test_suggestions_route(tmp_path: Path) -> None:
    profile = _seed_machine(tmp_path, _laptop())
    app = create_app(AppSettings(data_directory=tmp_path, session_token_factory=lambda: "t"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.app.state.ollama_tags_transport = _tags_transport(["qwen2.5-coder:1.5b"])  # type: ignore[attr-defined]
        response = client.get("/api/v1/models/suggestions", params={"use_case": "coding"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert client.get("/api/v1/models/suggestions?use_case=poetry").status_code == 422
    assert body["use_case"] == "coding"
    assert body["use_cases"] == ["chat", "coding", "document_qa", "small_fast", "reasoning"]
    assert body["catalog_version"] == load_model_catalog().catalog_version
    assert body["ollama_reachable"] is True
    assert body["speed_basis"] == "unknown"  # no completed runs in this database
    assert body["pull_targets"] == [
        {
            "engine_profile_id": str(profile.id),
            "label": "Ollama here",
            "endpoint_url": "http://127.0.0.1:11434",
            "enabled": True,
        }
    ]
    coder = next(
        item for item in body["suggestions"] if item["model_id"].startswith("qwen2.5-coder-1.5b")
    )
    assert coder["downloaded"][0]["engine_profile_id"] == str(profile.id)
    assert coder["sources"]["gguf_url"] == (
        "https://huggingface.co/bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF"
    )
    assert "localforge.ollama" in coder["runnable_with"]
    assert set(coder["speed"]) == {"tokens_per_second", "basis", "label", "verdict"}


def test_suggestions_route_is_loopback_only(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    with TestClient(app, base_url="http://example.com") as client:
        response = client.get("/api/v1/models/suggestions")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "loopback_only"


def test_render_model_suggestions_text() -> None:
    machine = suggestion_machine(_laptop(), read_gib_per_second=19.6)
    responses = [build_model_suggestions(use, machine) for use in ("chat", "coding")]
    lines = render_model_suggestions_text(responses, limit=3)
    assert lines[0].startswith("Models to try (catalog ")
    assert "  Chat:" in lines and "  Coding:" in lines
    assert sum(line.startswith("    ") for line in lines) == 6
    assert "tok/s" in lines[2]
    assert "estimated from your completed runs" in lines[-1]


def test_recommend_cli_prints_a_models_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import localforge.model_catalog as model_catalog
    import localforge.recommendations as recommendations
    from localforge.recommendations import RecommendationResponse

    _seed_machine(tmp_path, _laptop())
    canned = RecommendationResponse.model_validate(
        {
            "generated_at": "2026-10-14T00:00:00Z",
            "method": "m",
            "machine": {
                "summary": "Test laptop",
                "os": "Windows 11",
                "os_family": "windows",
                "arch": "x86_64",
                "cpu_model": None,
                "cpu_logical_cores": None,
                "cpu_physical_cores": None,
                "memory_total_bytes": 33_785_430_016,
                "accelerators": [],
                "cpu_features": {"avx2": True, "avx512": False},
                "snapshot_source": "saved_profile",
                "observed_at": None,
            },
            "model_size_guidance": {
                "basis": "system_memory",
                "memory_budget_bytes": None,
                "comfortable_params_billion_q4": None,
                "max_params_billion_q4": None,
                "text": "",
            },
            "recommendations": [],
        }
    )
    monkeypatch.setattr(recommendations, "recommend_for_data_directory", lambda *a, **k: canned)

    async def no_tags(*_args: object, **_kwargs: object) -> dict[str, list[str]]:
        return {"http://127.0.0.1:11434": ["llama3.2:1b"]}

    monkeypatch.setattr(model_catalog, "fetch_ollama_tags", no_tags)
    assert cli.main(["recommend", "--data-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Models to try (catalog" in output
    assert "  Chat:" in output and "  Coding:" in output
    assert "llama3.2:1b" in output and "downloaded" in output
