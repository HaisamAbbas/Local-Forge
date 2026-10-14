import asyncio
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from localforge.adapters.catalog import default_adapter_catalog
from localforge.adapters.contracts import AdapterCategory, AdapterManifest
from localforge.api.main import AppSettings, create_app
from localforge.core.models import (
    AcceleratorInfo,
    BenchmarkPlan,
    BenchmarkRun,
    BenchmarkRunStatus,
    EngineIntegrationMode,
    EngineProfile,
    HardwareSnapshot,
    MachineProfile,
    MachineProfileStatus,
    MetricSample,
    MetricSource,
)
from localforge.discovery import DiscoveryCandidate
from localforge.recommendations import (
    ENGINE_RULES,
    GIB,
    RECOMMENDATION_METHOD,
    RULES_BY_ADAPTER,
    EngineRecommendation,
    InstallEvidence,
    LocalRun,
    MachineTraits,
    assess_engine,
    best_fit_line,
    build_recommendations,
    detect_installed,
    fit_tier,
    machine_summary,
    machine_traits,
    measured_read_gib_per_second,
    measured_runs_by_adapter,
    model_size_guidance,
    rank_engines,
    read_stored_context,
    render_text,
)
from localforge.storage.database import initialize_database, open_database
from localforge.storage.repositories import MetricSampleRepository, RecordRepository
from localforge.system.inspection import CpuFeatures, detect_cpu_features, parse_linux_cpu_flags

CUDA_ONLY = {
    "localforge.vllm",
    "localforge.sglang",
    "localforge.tensorrt_llm",
    "localforge.lmdeploy",
    "localforge.lightllm",
    "localforge.nvidia_dynamo",
    "localforge.tgi",
}


def _manifests() -> list[AdapterManifest]:
    return default_adapter_catalog().list_manifests()


def _windows_intel_laptop() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Windows",
        os_version="10.0.26200",
        architecture="AMD64",
        cpu_model="Intel(R) Core(TM) Ultra 7 155U",
        cpu_logical_cores=14,
        cpu_physical_cores=12,
        memory_total_bytes=33_785_430_016,
        accelerators=[
            AcceleratorInfo(
                name="Intel(R) Graphics",
                vendor="Intel",
                integrated=True,
                memory_kind="shared",
            )
        ],
    )


def _linux_a100() -> HardwareSnapshot:
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
                runtime="CUDA",
                integrated=False,
                memory_kind="dedicated",
            )
        ],
    )


def _apple_silicon_mac() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Darwin",
        architecture="arm64",
        cpu_model="Apple M3 Pro",
        memory_total_bytes=36 * GIB,
        accelerators=[
            AcceleratorInfo(
                name="Apple M3 Pro", vendor="Apple", integrated=True, memory_kind="shared"
            )
        ],
    )


def _rank(
    hardware: HardwareSnapshot, features: CpuFeatures, **kwargs: object
) -> list[EngineRecommendation]:
    return rank_engines(_manifests(), machine_traits(hardware, features), **kwargs)  # type: ignore[arg-type]


def _by_id(items: list[EngineRecommendation]) -> dict[str, EngineRecommendation]:
    return {item.adapter_id: item for item in items}


def test_every_implemented_user_facing_adapter_has_a_rule() -> None:
    manifests = _manifests()
    expected = {
        manifest.adapter_id
        for manifest in manifests
        if manifest.implementation_status == "implemented"
        and manifest.category is not AdapterCategory.TEST_FIXTURE
    }
    assert expected == set(RULES_BY_ADAPTER)
    ranked = _rank(_windows_intel_laptop(), CpuFeatures(avx2=True, avx512=False))
    ids = [item.adapter_id for item in ranked]
    assert set(ids) == expected
    assert "localforge.fake" not in ids
    assert "localforge.exllamav3" not in ids
    assert [item.rank for item in ranked] == list(range(1, len(ranked) + 1))


def test_rule_table_values_stay_in_documented_ranges() -> None:
    for rule in ENGINE_RULES:
        assert all(0 <= points <= 65 for points in rule.hardware.values()), rule.adapter_id
        assert all(0 <= points <= 15 for points in rule.setup.values()), rule.adapter_id
        assert set(rule.strengths) >= set(rule.hardware), rule.adapter_id
        assert rule.install, rule.adapter_id
        if any(key not in {"cpu"} for key in rule.hardware) and "cpu" not in rule.hardware:
            assert rule.hardware_requirement, rule.adapter_id


def test_windows_intel_laptop_prefers_cpu_friendly_engines() -> None:
    ranked = _rank(_windows_intel_laptop(), CpuFeatures(avx2=True, avx512=False))
    by_id = _by_id(ranked)
    top_four = {item.adapter_id for item in ranked[:4]}
    assert top_four == {
        "localforge.ollama",
        "localforge.llama_cpp",
        "localforge.koboldcpp",
        "localforge.openvino_genai",
    }
    assert all(item.fit == "best" for item in ranked[:4])
    for adapter_id in CUDA_ONLY | {"localforge.mlx_lm"}:
        assert by_id[adapter_id].fit == "unsuitable"
        assert by_id[adapter_id].score == 0
    assert by_id["localforge.vllm"].reasons == [
        "Needs an NVIDIA GPU with CUDA (CPU builds need AVX-512)"
    ]
    assert by_id["localforge.mlx_lm"].reasons == ["Needs a Mac with Apple silicon"]
    suitable_count = sum(item.fit != "unsuitable" for item in ranked)
    assert all(item.fit == "unsuitable" for item in ranked[suitable_count:])
    assert by_id["localforge.ollama"].install.command == "winget install Ollama.Ollama"
    assert any("WSL2" in caution for caution in by_id["localforge.localai"].cautions)


def test_vllm_cpu_build_is_only_possible_with_avx512() -> None:
    hardware = HardwareSnapshot(
        os_name="Linux", architecture="x86_64", cpu_model="Intel Xeon", accelerators=[]
    )
    without = _by_id(_rank(hardware, CpuFeatures(avx2=True, avx512=False)))
    with_avx512 = _by_id(_rank(hardware, CpuFeatures(avx2=True, avx512=True)))
    assert without["localforge.vllm"].fit == "unsuitable"
    assert with_avx512["localforge.vllm"].fit == "possible"
    assert with_avx512["localforge.sglang"].fit == "unsuitable"


def test_linux_a100_prefers_serving_engines() -> None:
    ranked = _rank(_linux_a100(), CpuFeatures(avx2=True, avx512=True))
    assert [item.adapter_id for item in ranked[:3]] == [
        "localforge.vllm",
        "localforge.sglang",
        "localforge.tensorrt_llm",
    ]
    assert all(item.fit == "best" for item in ranked[:3])
    assert _by_id(ranked)["localforge.mlx_lm"].fit == "unsuitable"
    assert _by_id(ranked)["localforge.vllm"].install.command == "pip install vllm"


def test_small_nvidia_gpu_penalizes_large_gpu_serving_engines() -> None:
    hardware = _linux_a100().model_copy(
        update={
            "accelerators": [
                AcceleratorInfo(
                    name="NVIDIA GeForce RTX 3060",
                    vendor="NVIDIA",
                    memory_bytes=8 * GIB,
                    integrated=False,
                    memory_kind="dedicated",
                )
            ]
        }
    )
    by_id = _by_id(_rank(hardware, CpuFeatures(avx2=True, avx512=False)))
    assert by_id["localforge.vllm"].score_breakdown.hardware == 55
    assert any("8.0 GB of GPU memory" in caution for caution in by_id["localforge.vllm"].cautions)


def test_apple_silicon_prefers_mlx_and_rejects_cuda_engines() -> None:
    ranked = _rank(_apple_silicon_mac(), CpuFeatures())
    assert ranked[0].adapter_id == "localforge.mlx_lm"
    assert ranked[0].fit == "best"
    by_id = _by_id(ranked)
    for adapter_id in CUDA_ONLY:
        assert by_id[adapter_id].fit == "unsuitable"
    assert by_id["localforge.llama_cpp"].fit == "best"


def test_cpu_without_avx2_gets_a_caution_and_lower_fit() -> None:
    hardware = HardwareSnapshot(
        os_name="Linux", architecture="x86_64", cpu_model="Intel Core i5-2500", accelerators=[]
    )
    modern = _by_id(_rank(hardware, CpuFeatures(avx2=True, avx512=False)))
    old = _by_id(_rank(hardware, CpuFeatures(avx2=False, avx512=False)))
    assert old["localforge.llama_cpp"].score == modern["localforge.llama_cpp"].score - 10
    assert any("AVX2" in caution for caution in old["localforge.llama_cpp"].cautions)
    assert not any("AVX2" in caution for caution in modern["localforge.llama_cpp"].cautions)


def test_unknown_gpu_detection_is_a_caution_not_a_verdict() -> None:
    traits = MachineTraits(
        os="linux",
        arch="x86_64",
        classes=frozenset({"cpu"}),
        avx2=True,
        avx512=False,
        gpu_detection_ran=False,
    )
    assessment = assess_engine(RULES_BY_ADAPTER["localforge.sglang"], traits)
    assert assessment.suitable
    assert fit_tier(assessment) == "possible"
    assert any("GPU detection did not complete" in caution for caution in assessment.cautions)


def test_model_size_guidance_from_system_memory() -> None:
    guidance = model_size_guidance(HardwareSnapshot(memory_total_bytes=32 * GIB))
    assert guidance.basis == "system_memory"
    assert guidance.memory_budget_bytes == 26 * GIB
    assert guidance.comfortable_params_billion_q4 == 30.0  # 26 * 0.7 / 0.6 = 30.3
    assert guidance.max_params_billion_q4 == 43.0  # 26 / 0.6 = 43.3
    assert "32.0 GB of RAM" in guidance.text
    assert "about 30B" in guidance.text

    tiny = model_size_guidance(HardwareSnapshot(memory_total_bytes=4 * GIB))
    assert tiny.memory_budget_bytes == 0
    assert tiny.max_params_billion_q4 == 0.0

    unknown = model_size_guidance(HardwareSnapshot())
    assert unknown.memory_budget_bytes is None
    assert unknown.comfortable_params_billion_q4 is None


def test_cpu_only_guidance_separates_memory_fit_from_speed() -> None:
    laptop = HardwareSnapshot(memory_total_bytes=32 * GIB)

    unmeasured = model_size_guidance(laptop)
    assert unmeasured.speed_basis == "not_measured"
    assert "fit in memory" in unmeasured.text
    assert "fit comfortably" not in unmeasured.text
    assert "start with 1-3B models" in unmeasured.text

    # About 20 GB/s streamed: 20 / (10 tok/s * 0.6 GB per B) = 3.3B at a readable speed.
    measured = model_size_guidance(laptop, 20.0)
    assert measured.speed_basis == "measured"
    assert measured.measured_read_gib_per_second == 20.0
    assert measured.readable_params_billion_q4 == 3.3
    assert "about 10 tokens/s up to roughly 3.3B" in measured.text
    assert "about 5 tokens/s at 7B" in measured.text

    # Discrete GPUs keep memory-based guidance; CPU speed estimates do not apply there.
    gpu = model_size_guidance(_linux_a100(), 20.0)
    assert gpu.speed_basis == "not_applicable"
    assert gpu.readable_params_billion_q4 is None


def test_read_speed_is_the_median_over_runs_with_known_model_sizes() -> None:
    def run(size_gib: float | None, rate: float | None) -> LocalRun:
        return LocalRun(
            run_id=str(uuid4()),
            adapter_id="localforge.ollama",
            model_ref="model",
            model_size_bytes=None if size_gib is None else int(size_gib * GIB),
            generation_tokens_per_second=rate,
        )

    assert measured_read_gib_per_second([]) is None
    assert measured_read_gib_per_second([run(None, 20.0), run(1.0, None)]) is None
    # 0.92 GiB x 22 = 20.2, 1.8 x 11 = 19.8, 1.2 x 30 = 36 (outlier) -> median 20.2.
    assert measured_read_gib_per_second([run(0.92, 22), run(1.8, 11), run(1.2, 30)]) == 20.2


def test_model_size_guidance_prefers_discrete_gpu_memory() -> None:
    guidance = model_size_guidance(_linux_a100())
    assert guidance.basis == "gpu_memory"
    assert guidance.memory_budget_bytes == int(80 * GIB * 0.85)
    assert guidance.comfortable_params_billion_q4 == 79.0  # 68 * 0.7 / 0.6 = 79.3
    assert guidance.max_params_billion_q4 == 113.0
    small = model_size_guidance(
        HardwareSnapshot(
            memory_total_bytes=16 * GIB,
            accelerators=[
                AcceleratorInfo(
                    name="NVIDIA GeForce RTX 3050",
                    vendor="NVIDIA",
                    memory_bytes=4 * GIB,
                    integrated=False,
                    memory_kind="dedicated",
                )
            ],
        )
    )
    assert small.comfortable_params_billion_q4 == 4.0  # 3.4 * 0.7 / 0.6 = 3.97
    assert small.max_params_billion_q4 == 5.7


def test_machine_summary_matches_contract_example() -> None:
    assert machine_summary(_windows_intel_laptop()) == (
        "Windows 11 · Intel Core Ultra 7 155U · 31.5 GB RAM · Intel integrated GPU (shared memory)"
    )
    assert "NVIDIA A100-SXM4-80GB (80.0 GB)" in machine_summary(_linux_a100())
    assert machine_summary(HardwareSnapshot(os_name="Linux", accelerators=[])) == (
        "Linux · no GPU detected"
    )


def test_status_merging_running_installed_and_not_installed() -> None:
    profile_id = uuid4()
    running = [
        DiscoveryCandidate(
            adapter_id="localforge.ollama",
            display_name="Ollama",
            endpoint_url="http://127.0.0.1:11434",
            port=11434,
            engine_version="0.40.3",
            models=["qwen2.5:3b", "llama3.2:1b"],
            evidence="GET /api/version returned an Ollama version",
            existing_profile_ids=[profile_id],
        )
    ]
    installed = {
        "localforge.ollama": InstallEvidence("installed", "Found ollama"),
        "localforge.llama_cpp": InstallEvidence("installed", "Found llama-server"),
        "localforge.koboldcpp": InstallEvidence("not_installed", "Not found"),
    }
    by_id = _by_id(
        _rank(
            _windows_intel_laptop(),
            CpuFeatures(avx2=True, avx512=False),
            installed=installed,
            running=running,
        )
    )
    ollama = by_id["localforge.ollama"]
    assert ollama.status == "running"
    assert ollama.endpoint_url == "http://127.0.0.1:11434"
    assert ollama.status_detail == (
        "Answering at http://127.0.0.1:11434 (version 0.40.3) with 2 models"
    )
    assert ollama.existing_profile_ids == [profile_id]
    assert ollama.score_breakdown.status == 15
    assert by_id["localforge.llama_cpp"].status == "installed"
    assert by_id["localforge.llama_cpp"].score_breakdown.status == 10
    assert by_id["localforge.koboldcpp"].status == "not_installed"
    assert by_id["localforge.mistral_rs"].status == "unknown"
    assert ollama.score > by_id["localforge.llama_cpp"].score - 10


def test_running_engine_is_never_called_unsuitable() -> None:
    running = [
        DiscoveryCandidate(
            adapter_id="localforge.vllm",
            display_name="vLLM",
            endpoint_url="http://127.0.0.1:8000",
            port=8000,
            engine_version=None,
            models=[],
            evidence="owned_by vllm",
            existing_profile_ids=[],
        )
    ]
    vllm = _by_id(
        _rank(_windows_intel_laptop(), CpuFeatures(avx2=True, avx512=False), running=running)
    )["localforge.vllm"]
    assert vllm.fit == "possible"
    assert vllm.status == "running"
    assert "Measure before relying on it" in vllm.cautions[0]


def test_detect_installed_uses_path_known_dirs_modules_and_profiles(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    executable.write_text("")
    profile = EngineProfile(
        label="Managed",
        adapter_id="localforge.llama_cpp",
        integration_mode=EngineIntegrationMode.MANAGED_PROCESS,
        engine_extensions={"llama_cpp": {"executable_path": str(executable)}},
    )
    rules = [
        RULES_BY_ADAPTER[key]
        for key in (
            "localforge.ollama",
            "localforge.llama_cpp",
            "localforge.koboldcpp",
            "localforge.openvino_genai",
            "localforge.openai_compatible",
            "localforge.vllm",
        )
    ]
    found_paths: list[str] = []

    def path_exists(path: str) -> bool:
        found_paths.append(path)
        return path.endswith("ollama.exe")

    evidence = detect_installed(
        rules,
        "windows",
        [profile],
        which=lambda name: "C:/bin/koboldcpp.exe" if name == "koboldcpp" else None,
        path_exists=path_exists,
        module_available=lambda name: name == "openvino_genai",
    )
    assert evidence["localforge.ollama"].status == "installed"
    assert evidence["localforge.llama_cpp"].status == "installed"
    assert str(executable) in (evidence["localforge.llama_cpp"].detail or "")
    assert evidence["localforge.koboldcpp"].detail == "Found koboldcpp at C:/bin/koboldcpp.exe"
    assert evidence["localforge.openvino_genai"].status == "installed"
    assert evidence["localforge.openai_compatible"].status == "unknown"
    assert evidence["localforge.vllm"].status == "not_installed"


def _seed_runs(database_path: Path) -> tuple[str, str]:
    initialize_database(database_path)
    connection = open_database(database_path)
    try:
        local = RecordRepository(connection, EngineProfile).create(
            EngineProfile(
                label="Local Ollama",
                adapter_id="localforge.ollama",
                integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
                endpoint_url="http://127.0.0.1:11434",
            )
        )
        remote = RecordRepository(connection, EngineProfile).create(
            EngineProfile(
                label="Remote vLLM",
                adapter_id="localforge.vllm",
                integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
                endpoint_url="https://gpu.example.com",
            )
        )
        RecordRepository(connection, MachineProfile).create(
            MachineProfile(
                label="This machine",
                status=MachineProfileStatus.DETECTED,
                hardware=_windows_intel_laptop(),
            )
        )
        plans = {}
        for profile in (local, remote):
            plans[profile.id] = RecordRepository(connection, BenchmarkPlan).create(
                BenchmarkPlan(name="plan", engine_profile_id=profile.id, workload_id=uuid4())
            )

        def run(profile: EngineProfile, status: BenchmarkRunStatus, rate: float | None) -> str:
            record = RecordRepository(connection, BenchmarkRun).create(
                BenchmarkRun(
                    plan_id=plans[profile.id].id,
                    status=status,
                    profile_snapshot={
                        "engine": {"adapter_id": profile.adapter_id},
                        "model_artifact": {"model_ref": "qwen2.5:3b"},
                        "machine_profile_id": None,
                    },
                )
            )
            if rate is not None:
                MetricSampleRepository(connection).create(
                    MetricSample(
                        run_id=record.id,
                        metric_name="generation_throughput_tokens_per_second",
                        value=rate,
                        unit="tokens/s",
                        source=MetricSource.DERIVED,
                        aggregation="run_rate",
                    )
                )
            return str(record.id)

        run(local, BenchmarkRunStatus.COMPLETED, 12.5)
        best = run(local, BenchmarkRunStatus.COMPLETED, 21.25)
        run(local, BenchmarkRunStatus.COMPLETED, None)
        run(local, BenchmarkRunStatus.FAILED, 99.0)
        run(remote, BenchmarkRunStatus.COMPLETED, 500.0)
        return best, str(local.id)
    finally:
        connection.close()


def test_measured_runs_join_completed_local_runs(tmp_path: Path) -> None:
    database_path = tmp_path / "localforge.sqlite3"
    best_run, local_profile = _seed_runs(database_path)
    connection = open_database(database_path)
    try:
        measured = measured_runs_by_adapter(connection)
    finally:
        connection.close()
    assert set(measured) == {"localforge.ollama"}
    ollama = measured["localforge.ollama"]
    assert ollama.completed_runs == 3
    assert ollama.best_generation_tokens_per_second == 21.25
    assert str(ollama.run_id) == best_run
    assert ollama.model_ref == "qwen2.5:3b"

    context = read_stored_context(database_path)
    assert context.saved_machine is not None
    assert [str(profile.id) for profile in context.saved_profiles][0] == local_profile
    ranked = _by_id(
        _rank(
            _windows_intel_laptop(),
            CpuFeatures(avx2=True, avx512=False),
            measured=context.measured,
            saved_profiles=context.saved_profiles,
            installed={"localforge.ollama": InstallEvidence("not_installed", "Not found")},
        )
    )
    entry = ranked["localforge.ollama"]
    assert entry.score_breakdown.measured == 5
    assert entry.status == "installed"  # completed local runs prove it ran here
    assert any("21.25 tokens/s" in reason for reason in entry.reasons)
    assert [str(item) for item in entry.existing_profile_ids] == [local_profile]
    assert read_stored_context(tmp_path / "missing.sqlite3").saved_profiles == []


def _empty_transport() -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    return httpx2.MockTransport(handler)


def test_build_recommendations_uses_saved_snapshot_without_inspecting() -> None:
    def fail() -> MachineProfile:
        raise AssertionError("the saved snapshot should be reused")

    saved = MachineProfile(
        label="This machine", status=MachineProfileStatus.DETECTED, hardware=_linux_a100()
    )
    response = asyncio.run(
        build_recommendations(
            _manifests(),
            saved_machine=saved,
            discovery_transport=_empty_transport(),
            machine_inspector=fail,
            cpu_features_provider=lambda: CpuFeatures(avx2=True, avx512=True),
            install_detector=lambda rules, family, profiles: {},
        )
    )
    assert response.machine.snapshot_source == "saved_profile"
    assert response.machine.cpu_features.avx512 is True
    assert response.recommendations[0].adapter_id == "localforge.vllm"
    assert response.method == RECOMMENDATION_METHOD
    lines = render_text(response)
    assert lines[1].startswith("  Machine: Linux")
    assert any("Not suitable here:" in line for line in lines)
    assert "MLX-LM: Needs a Mac with Apple silicon" in "\n".join(lines)
    assert best_fit_line(response) == (
        "Best fit for this machine: vLLM (unknown). Run `localforge recommend` for the full list."
    )
    short = render_text(response, limit=2)
    assert sum(line.startswith(("   1 ", "   2 ", "   3 ")) for line in short) == 2
    assert not any("Not suitable here:" in line for line in short)


def test_recommendations_route_shape(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path, session_token_factory=lambda: "t"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.app.state.engine_discovery_transport = _empty_transport()  # type: ignore[attr-defined]
        response = client.get("/api/v1/engines/recommendations")
        assert response.status_code == 200, response.text
        body = response.json()
    assert set(body) == {
        "generated_at",
        "method",
        "machine",
        "model_size_guidance",
        "recommendations",
    }
    assert body["method"] == RECOMMENDATION_METHOD
    assert set(body["machine"]) >= {
        "summary",
        "os",
        "arch",
        "cpu_model",
        "cpu_logical_cores",
        "cpu_physical_cores",
        "memory_total_bytes",
        "accelerators",
        "cpu_features",
    }
    assert set(body["machine"]["cpu_features"]) == {"avx2", "avx512"}
    assert set(body["model_size_guidance"]) == {
        "basis",
        "memory_budget_bytes",
        "comfortable_params_billion_q4",
        "max_params_billion_q4",
        "speed_basis",
        "measured_read_gib_per_second",
        "readable_params_billion_q4",
        "text",
    }
    first = body["recommendations"][0]
    assert set(first) >= {
        "rank",
        "adapter_id",
        "display_name",
        "fit",
        "score",
        "reasons",
        "cautions",
        "status",
        "status_detail",
        "endpoint_url",
        "existing_profile_ids",
        "install",
        "measured",
    }
    assert set(first["install"]) == {"summary", "url", "command"}
    assert first["rank"] == 1
    assert {item["fit"] for item in body["recommendations"]} <= {
        "best",
        "good",
        "possible",
        "unsuitable",
    }


def test_recommendations_route_rejects_remote_hosts(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    with TestClient(app, base_url="http://example.com") as client:
        client.app.state.engine_discovery_transport = _empty_transport()  # type: ignore[attr-defined]
        response = client.get("/api/v1/engines/recommendations")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "loopback_only"


def test_parse_linux_cpu_flags() -> None:
    text = "processor : 0\nflags : fpu sse avx avx2 fma\n\nprocessor : 1\nflags : avx512f\n"
    assert parse_linux_cpu_flags(text) == CpuFeatures(avx2=True, avx512=False)
    assert parse_linux_cpu_flags("model name : x\n") == CpuFeatures()


@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        ({"hw.optional.avx2_0": "1\n", "hw.optional.avx512f": "0\n"}, CpuFeatures(True, False)),
        ({}, CpuFeatures(None, None)),
    ],
)
def test_detect_cpu_features_on_intel_macs(outputs: dict[str, str], expected: CpuFeatures) -> None:
    def runner(arguments: object, timeout: float) -> str | None:
        return outputs.get(list(arguments)[-1])  # type: ignore[call-overload]

    assert (
        detect_cpu_features(platform_name="darwin", architecture="x86_64", command_runner=runner)
        == expected
    )


def test_detect_cpu_features_is_unknown_on_arm() -> None:
    assert detect_cpu_features(platform_name="linux", architecture="aarch64") == CpuFeatures()
