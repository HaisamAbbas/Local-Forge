"""Hardware discovery reports source, confidence, and unknown values."""

from types import SimpleNamespace

from localforge.core.models import (
    AcceleratorInfo,
    MachineProfileStatus,
    ProvenanceSource,
)
from localforge.system.inspection import (
    MachineFacts,
    detect_accelerators,
    inspect_local_machine,
)


def test_inspection_records_available_values_with_provenance() -> None:
    accelerator = AcceleratorInfo(
        name="Test GPU",
        vendor="Test vendor",
        memory_bytes=8 * 1024**3,
        runtime="Test runtime",
        driver_version="1.2.3",
    )
    profile = inspect_local_machine(
        facts_provider=lambda: MachineFacts("TestOS", "1.0", "x86_64", "Test CPU"),
        cpu_count_provider=lambda: 8,
        memory_provider=lambda: 32 * 1024**3,
        accelerator_provider=lambda: [accelerator],
    )

    assert profile.status is MachineProfileStatus.DETECTED
    assert profile.hardware is not None
    assert profile.hardware.cpu_model == "Test CPU"
    assert profile.hardware.memory_total_bytes == 32 * 1024**3
    assert profile.hardware.accelerators == [accelerator]
    assert profile.provenance["cpu_model"].source is ProvenanceSource.DETECTED
    assert profile.provenance["cpu_model"].confidence == 0.9
    assert profile.provenance["memory_total_bytes"].observed_at is not None


def test_inspection_leaves_unavailable_values_unknown() -> None:
    profile = inspect_local_machine(
        facts_provider=lambda: MachineFacts("TestOS", None, "arm64", None),
        cpu_count_provider=lambda: None,
        memory_provider=lambda: None,
        accelerator_provider=lambda: None,
    )

    assert profile.status is MachineProfileStatus.PARTIALLY_DETECTED
    assert profile.hardware is not None
    assert profile.hardware.os_name == "TestOS"
    assert profile.hardware.os_version is None
    assert profile.hardware.cpu_model is None
    assert profile.hardware.cpu_logical_cores is None
    assert profile.hardware.memory_total_bytes is None
    assert profile.hardware.accelerators is None
    assert "memory_total_bytes" not in profile.provenance
    assert "accelerators" not in profile.provenance


def test_nvidia_probe_uses_fixed_arguments_and_parses_reported_metadata(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(arguments: list[str], **options: object) -> SimpleNamespace:
        calls.append((arguments, options))
        return SimpleNamespace(
            returncode=0,
            stdout="Test GPU, 8192, 555.1\n",
        )

    monkeypatch.setattr("localforge.system.inspection.shutil.which", lambda _: "nvidia-smi")
    monkeypatch.setattr("localforge.system.inspection.subprocess.run", fake_run)

    accelerators = detect_accelerators()

    assert accelerators is not None
    assert accelerators[0].name == "Test GPU"
    assert accelerators[0].memory_bytes == 8192 * 1024**2
    assert accelerators[0].driver_version == "555.1"
    assert calls[0][0][0] == "nvidia-smi"
    assert calls[0][1].get("shell", False) is False
    assert calls[0][1]["timeout"] == 2
