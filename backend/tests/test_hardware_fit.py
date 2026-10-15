"""Hardware fit: quantization choice, run mode, grades, and the read-only API route."""

from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app
from localforge.core.models import AcceleratorInfo, HardwareSnapshot
from localforge.hardware_fit import build_hardware_fit, memory_pools
from localforge.model_catalog import load_model_catalog, suggestion_machine

GIB = 1024**3


def _hardware(ram_gib: int, gpu_gib: int | None = None, vendor: str = "NVIDIA") -> HardwareSnapshot:
    accelerators: list[AcceleratorInfo] = []
    if gpu_gib is not None:
        accelerators.append(
            AcceleratorInfo(
                name=f"{vendor} test GPU",
                vendor=vendor,
                memory_bytes=gpu_gib * GIB,
                integrated=False,
                memory_kind="dedicated",
            )
        )
    return HardwareSnapshot(
        cpu_model="Test CPU",
        memory_total_bytes=ram_gib * GIB,
        accelerators=accelerators,
    )


def _fit(hardware: HardwareSnapshot, read_gib_per_second: float | None = None):
    machine = suggestion_machine(hardware, read_gib_per_second)
    return build_hardware_fit(hardware, load_model_catalog(), detected_by="test", machine=machine)


def _by_id(response, model_id: str):
    return next(item for item in response.models if item.model_id == model_id)


def test_big_gpu_runs_small_models_at_high_quality_in_gpu_memory() -> None:
    response = _fit(_hardware(64, 24))
    small = _by_id(response, "qwen2.5-0.5b-instruct")
    assert response.machine.compute_path == "discrete_gpu"
    assert small.run_mode == "gpu"
    assert small.tier in {"perfect", "good"}
    assert small.quantization == "Q8_0"
    assert not small.lower_quality


def test_model_larger_than_gpu_memory_spills_into_ram_and_is_marginal() -> None:
    response = _fit(_hardware(128, 8))
    big = [item for item in response.models if item.run_mode == "gpu_and_ram"]
    assert big, "a GPU of 8 GB must push some catalog model into system RAM"
    assert all(item.tier == "marginal" for item in big)
    assert all("system RAM" in item.explanation for item in big)


def test_cpu_only_machine_never_grades_perfect() -> None:
    response = _fit(_hardware(16))
    assert response.machine.compute_path == "cpu_only"
    assert response.machine.gpu_budget_gb is None
    assert all(item.tier != "perfect" for item in response.models)
    assert all(item.run_mode in {"cpu", "none"} for item in response.models)


def test_models_that_do_not_fit_are_too_tight_and_sorted_last() -> None:
    response = _fit(_hardware(8))
    tiers = [item.tier for item in response.models]
    assert "too_tight" in tiers
    first_bad = tiers.index("too_tight")
    assert all(tier == "too_tight" for tier in tiers[first_bad:])
    bad = response.models[first_bad]
    assert bad.run_mode == "none" and bad.tokens_per_second is None


def test_a_measured_slow_machine_downgrades_the_grade() -> None:
    quick = _by_id(_fit(_hardware(32), read_gib_per_second=40.0), "qwen2.5-3b-instruct")
    slow = _by_id(_fit(_hardware(32), read_gib_per_second=1.0), "qwen2.5-3b-instruct")
    assert quick.tokens_per_second and slow.tokens_per_second
    assert quick.tokens_per_second > slow.tokens_per_second
    assert slow.tier == "marginal"
    assert quick.tier == "good"


def test_unknown_gpu_detection_is_said_plainly() -> None:
    hardware = HardwareSnapshot(
        cpu_model="Test CPU", memory_total_bytes=16 * GIB, accelerators=None
    )
    machine = _fit(hardware).machine
    assert machine.accelerators_detected is False
    assert any("could not be detected" in note for note in machine.notes)


def test_integrated_gpu_does_not_add_memory() -> None:
    hardware = HardwareSnapshot(
        memory_total_bytes=32 * GIB,
        accelerators=[
            AcceleratorInfo(
                name="Intel Graphics", vendor="Intel", integrated=True, memory_kind="shared"
            )
        ],
    )
    pools = memory_pools(hardware)
    assert pools.compute_path == "cpu_only"
    assert pools.gpu_budget_gib is None
    assert any("integrated GPU" in note for note in _fit(hardware).machine.notes)


def test_route_returns_every_catalog_model(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/hardware-fit", params={"refresh": "true"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["models"]) == len(load_model_catalog().models)
    assert payload["detected_by"] == "a fresh scan of this machine"
    assert {"summary", "compute_path", "accelerators"} <= set(payload["machine"])


def test_route_refuses_a_non_loopback_host(tmp_path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    with TestClient(app, base_url="http://example.test") as client:
        assert client.get("/api/v1/hardware-fit").status_code == 403


def test_unmeasured_cpu_grades_big_models_marginal_and_recommends_four_bit() -> None:
    response = _fit(_hardware(32))
    seven_b = _by_id(response, "qwen2.5-7b-instruct")
    tiny = _by_id(response, "qwen2.5-0.5b-instruct")
    assert seven_b.tokens_per_second is None
    assert seven_b.tier == "marginal"
    assert seven_b.quantization == "Q4_K_M"
    assert "benchmark" in seven_b.explanation
    assert tiny.tier == "good"
    assert tiny.quantization == "Q8_0"


def test_low_quality_quantization_is_used_only_when_nothing_better_fits() -> None:
    response = _fit(_hardware(32))
    for item in response.models:
        if item.lower_quality:
            assert item.size_gb * 1e9 / GIB * 1.1 + 0.5 <= 26.0
            four_bit = item.size_gb * 4.85 / item.quantization_bits
            assert four_bit * 1e9 / GIB * 1.1 + 0.5 > 25.5
