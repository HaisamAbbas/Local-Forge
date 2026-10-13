"""Cross-vendor accelerator and CPU detection parses fixed OS output without shelling out."""

import pytest

from localforge.core.models import AcceleratorInfo, MachineProfileStatus, ProvenanceSource
from localforge.system.inspection import (
    DisplayRegistryEntry,
    MachineFacts,
    classify_integrated,
    detect_accelerators,
    inspect_local_machine,
    merge_accelerators,
    parse_linux_cpuinfo,
)

WINDOWS_INTEL_ONLY = (
    '{"Name":"Intel(R) Graphics","AdapterCompatibility":"Intel Corporation",'
    '"DriverVersion":"32.0.101.8801",'
    '"PNPDeviceID":"PCI\\\\VEN_8086&DEV_7D45&SUBSYS_8C84103C&REV_08\\\\3&11583659&0&10"}'
)
WINDOWS_HYBRID = (
    '[{"Name":"Intel(R) UHD Graphics 770","AdapterCompatibility":"Intel Corporation",'
    '"DriverVersion":"31.0.101.4502","PNPDeviceID":"PCI\\\\VEN_8086&DEV_4680\\\\3&1"},'
    '{"Name":"NVIDIA GeForce RTX 4070","AdapterCompatibility":"NVIDIA",'
    '"DriverVersion":"32.0.15.6094","PNPDeviceID":"PCI\\\\VEN_10DE&DEV_2786&SUBSYS_1\\\\4&2"},'
    '{"Name":"AMD Radeon RX 7900 XTX","AdapterCompatibility":"Advanced Micro Devices, Inc.",'
    '"DriverVersion":"31.0.24027.1012","PNPDeviceID":"PCI\\\\VEN_1002&DEV_744C\\\\4&3"},'
    '{"Name":"Microsoft Basic Display Adapter","AdapterCompatibility":"(Standard display types)",'
    '"DriverVersion":"10.0.26100.1","PNPDeviceID":"ROOT\\\\BASICDISPLAY\\\\0000"}]'
)


def _scripted_runner(outputs: dict[str, str | None], calls: list[list[str]]):
    def runner(arguments, timeout):
        del timeout
        calls.append(list(arguments))
        for needle, output in outputs.items():
            if needle in " ".join(arguments):
                return output
        return None

    return runner


def _which(available: set[str]):
    return lambda name: name if name in available else None


def test_windows_integrated_gpu_reports_shared_memory_without_adapter_ram(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("localforge.system.inspection.shutil.which", _which({"powershell"}))
    registry = [
        DisplayRegistryEntry(
            driver_description="Intel(R) Graphics",
            matching_device_id="PCI\\VEN_8086&DEV_7D45&SUBSYS_8C84103C",
            dedicated_memory_bytes=None,
        )
    ]

    accelerators = detect_accelerators(
        platform_name="win32",
        command_runner=_scripted_runner({"Win32_VideoController": WINDOWS_INTEL_ONLY}, calls),
        registry_reader=lambda: registry,
    )

    assert accelerators == [
        AcceleratorInfo(
            name="Intel(R) Graphics",
            vendor="Intel",
            memory_bytes=None,
            runtime=None,
            driver_version="32.0.101.8801",
            integrated=True,
            memory_kind="shared",
            detected_by="windows-video-controller",
        )
    ]
    assert calls[0][:3] == ["powershell", "-NoProfile", "-NonInteractive"]


def test_windows_listing_merges_with_nvidia_smi_and_uses_registry_memory(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "localforge.system.inspection.shutil.which", _which({"powershell", "nvidia-smi"})
    )
    registry = [
        DisplayRegistryEntry("NVIDIA GeForce RTX 4070", "pci\\ven_10de&dev_2786", 12 * 1024**3),
        DisplayRegistryEntry("AMD Radeon RX 7900 XTX", "pci\\ven_1002&dev_744c", 24 * 1024**3),
        DisplayRegistryEntry("Intel(R) UHD Graphics 770", "pci\\ven_8086&dev_4680", 128 * 1024**2),
    ]

    accelerators = detect_accelerators(
        platform_name="win32",
        command_runner=_scripted_runner(
            {
                "nvidia-smi": "NVIDIA GeForce RTX 4070, 12282, 560.94\n",
                "Win32_VideoController": WINDOWS_HYBRID,
            },
            calls,
        ),
        registry_reader=lambda: registry,
    )

    assert accelerators is not None
    by_name = {item.name: item for item in accelerators}
    assert list(by_name) == [
        "NVIDIA GeForce RTX 4070",
        "Intel(R) UHD Graphics 770",
        "AMD Radeon RX 7900 XTX",
    ]
    nvidia = by_name["NVIDIA GeForce RTX 4070"]
    assert nvidia.detected_by == "nvidia-smi"
    assert nvidia.runtime == "CUDA"
    assert nvidia.memory_bytes == 12282 * 1024**2
    assert nvidia.integrated is False
    intel = by_name["Intel(R) UHD Graphics 770"]
    assert intel.integrated is True
    assert intel.memory_bytes is None
    assert intel.memory_kind == "shared"
    amd = by_name["AMD Radeon RX 7900 XTX"]
    assert amd.vendor == "AMD"
    assert amd.integrated is False
    assert amd.memory_bytes == 24 * 1024**3
    assert amd.memory_kind == "dedicated"
    assert calls[0][0] == "nvidia-smi"


def test_windows_nvidia_without_nvidia_smi_stays_listed_once(monkeypatch) -> None:
    monkeypatch.setattr("localforge.system.inspection.shutil.which", _which({"powershell"}))
    output = (
        '{"Name":"NVIDIA RTX A2000","AdapterCompatibility":"NVIDIA","DriverVersion":"1.0",'
        '"PNPDeviceID":"PCI\\\\VEN_10DE&DEV_2531\\\\1"}'
    )

    accelerators = detect_accelerators(
        platform_name="win32",
        command_runner=_scripted_runner({"Win32_VideoController": output}, []),
        registry_reader=lambda: [],
    )

    assert accelerators is not None and len(accelerators) == 1
    assert accelerators[0].vendor == "NVIDIA"
    assert accelerators[0].memory_bytes is None
    assert accelerators[0].memory_kind is None
    assert accelerators[0].runtime is None


def test_detection_is_unknown_when_no_source_runs(monkeypatch) -> None:
    monkeypatch.setattr("localforge.system.inspection.shutil.which", _which({"powershell"}))

    def detect(output: str | None):
        outputs = {} if output is None else {"Win32_VideoController": output}
        return detect_accelerators(
            platform_name="win32",
            command_runner=_scripted_runner(outputs, []),
            registry_reader=lambda: [],
        )

    assert detect(None) is None
    assert detect("not json") is None
    assert detect("") == []


def test_merge_removes_only_matching_nvidia_duplicates() -> None:
    from_smi = [AcceleratorInfo(name="NVIDIA GeForce RTX 3090", vendor="NVIDIA")]
    listed = [
        AcceleratorInfo(name="NVIDIA GeForce RTX 3090", vendor="NVIDIA"),
        AcceleratorInfo(name="NVIDIA GeForce RTX 3090", vendor="NVIDIA"),
    ]

    merged = merge_accelerators(from_smi, listed)

    assert merged is not None and len(merged) == 2
    assert merge_accelerators(None, None) is None


def test_linux_lspci_and_cpuinfo_parsing(monkeypatch) -> None:
    monkeypatch.setattr("localforge.system.inspection.shutil.which", _which({"lspci"}))
    lspci = (
        '00:02.0 "VGA compatible controller" "Intel Corporation" "Alder Lake-P GT2 [Iris Xe '
        'Graphics]" -r0c "Lenovo" "Device 3b4a"\n'
        '01:00.0 "3D controller" "NVIDIA Corporation" "GA107M [GeForce RTX 3050 Mobile]" '
        '-ra1 "Lenovo" "Device 3b4a"\n'
        '00:1f.3 "Audio device" "Intel Corporation" "Alder Lake PCH-P High Definition Audio" '
        '-r01 "" ""\n'
    )

    accelerators = detect_accelerators(
        platform_name="linux",
        command_runner=_scripted_runner({"lspci": lspci}, []),
        registry_reader=lambda: [],
    )
    model, cores = parse_linux_cpuinfo(
        "processor\t: 0\nmodel name\t: AMD Ryzen 7 7840U\nphysical id\t: 0\ncore id\t: 0\n\n"
        "processor\t: 1\nmodel name\t: AMD Ryzen 7 7840U\nphysical id\t: 0\ncore id\t: 0\n\n"
        "processor\t: 2\nmodel name\t: AMD Ryzen 7 7840U\nphysical id\t: 0\ncore id\t: 1\n"
    )

    assert accelerators is not None
    assert [(item.vendor, item.integrated) for item in accelerators] == [
        ("Intel", True),
        ("NVIDIA", False),
    ]
    assert all(item.memory_bytes is None for item in accelerators)
    assert (model, cores) == ("AMD Ryzen 7 7840U", 2)
    assert parse_linux_cpuinfo("processor\t: 0\nmodel name\t: ARM core\n") == ("ARM core", None)


def test_macos_unified_memory_is_reported_as_shared(monkeypatch) -> None:
    monkeypatch.setattr("localforge.system.inspection.shutil.which", _which({"system_profiler"}))
    output = (
        '{"SPDisplaysDataType":[{"_name":"Apple M3 Pro","sppci_model":"Apple M3 Pro",'
        '"spdisplays_vendor":"sppci_vendor_Apple","sppci_cores":"18"},'
        '{"sppci_model":"AMD Radeon Pro 5500M","spdisplays_vendor":"sppci_vendor_AMD",'
        '"spdisplays_vram":"8 GB"}]}'
    )

    accelerators = detect_accelerators(
        platform_name="darwin",
        command_runner=_scripted_runner({"SPDisplaysDataType": output}, []),
        registry_reader=lambda: [],
    )

    assert accelerators is not None
    apple, amd = accelerators
    assert (apple.vendor, apple.integrated, apple.memory_kind, apple.memory_bytes) == (
        "Apple",
        True,
        "shared",
        None,
    )
    assert (amd.integrated, amd.memory_kind, amd.memory_bytes) == (False, "dedicated", 8 * 1024**3)


@pytest.mark.parametrize(
    ("vendor", "name", "expected"),
    [
        ("Intel", "Intel(R) Arc(TM) A770 Graphics", False),
        ("Intel", "Intel(R) Arc(TM) Graphics", True),
        ("Intel", "Intel(R) Iris(R) Xe Graphics", True),
        ("AMD", "AMD Radeon(TM) Graphics", True),
        ("AMD", "AMD Radeon 780M Graphics", True),
        ("AMD", "AMD Radeon RX 6600", False),
        ("Matrox", "Matrox G200eW3", None),
    ],
)
def test_integrated_classification_is_conservative(vendor, name, expected) -> None:
    assert classify_integrated(vendor, name) is expected


def test_inspection_records_physical_cores_and_cpu_name_confidence() -> None:
    profile = inspect_local_machine(
        facts_provider=lambda: MachineFacts(
            "Windows", "10", "AMD64", "Intel(R) Core(TM) Ultra 7 155U", 0.95
        ),
        cpu_count_provider=lambda: 14,
        physical_core_provider=lambda: 12,
        memory_provider=lambda: 32 * 1024**3,
        accelerator_provider=lambda: [],
    )

    assert profile.hardware is not None
    assert profile.hardware.cpu_physical_cores == 12
    assert profile.provenance["cpu_physical_cores"].source is ProvenanceSource.DETECTED
    assert profile.provenance["cpu_model"].confidence == 0.95

    unknown = inspect_local_machine(
        facts_provider=lambda: MachineFacts("Windows", "10", "AMD64", "Family 6 Model 170", 0.5),
        cpu_count_provider=lambda: 14,
        physical_core_provider=lambda: None,
        memory_provider=lambda: 32 * 1024**3,
        accelerator_provider=lambda: [],
    )
    assert unknown.status is MachineProfileStatus.DETECTED
    assert unknown.hardware is not None and unknown.hardware.cpu_physical_cores is None
    assert "cpu_physical_cores" not in unknown.provenance
    assert unknown.provenance["cpu_model"].confidence == 0.5
