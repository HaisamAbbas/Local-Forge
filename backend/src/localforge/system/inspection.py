"""Best-effort hardware inspection that reports unknown values honestly."""

import csv
import ctypes
import json
import os
import platform
import re
import shlex
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from localforge.core.models import (
    AcceleratorInfo,
    FieldProvenance,
    HardwareSnapshot,
    MachineProfile,
    MachineProfileStatus,
    ProvenanceSource,
    utc_now,
)

# Returns stdout for a successful command, or None when it is missing, fails, or times out.
CommandRunner = Callable[[Sequence[str], float], str | None]
MemoryKind = Literal["dedicated", "shared"]

WINDOWS_DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)
WINDOWS_CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
WINDOWS_VIDEO_CONTROLLER_COMMAND = (
    "Get-CimInstance Win32_VideoController | "
    "Select-Object Name,AdapterCompatibility,DriverVersion,PNPDeviceID | "
    "ConvertTo-Json -Compress"
)
WINDOWS_COMMAND_TIMEOUT_SECONDS = 8.0
PCI_VENDOR_NAMES = {
    "8086": "Intel",
    "10de": "NVIDIA",
    "1002": "AMD",
    "1022": "AMD",
    "5143": "Qualcomm",
    "106b": "Apple",
    "1414": "Microsoft",
}
# Software or remote display adapters are not compute accelerators.
_NON_ACCELERATOR_ADAPTERS = re.compile(
    r"microsoft basic (display|render)|microsoft remote display|remote desktop|"
    r"hyper-v video|virtual display|indirect display",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MachineFacts:
    os_name: str | None
    os_version: str | None
    architecture: str | None
    cpu_model: str | None
    cpu_model_confidence: float | None = None


@dataclass(frozen=True)
class DisplayRegistryEntry:
    """Driver-reported values from one Windows display-class registry key."""

    driver_description: str | None
    matching_device_id: str | None
    dedicated_memory_bytes: int | None


def _run_command(arguments: Sequence[str], timeout: float) -> str | None:
    """Run a fixed argument list without a shell and return stdout only on success."""

    options: dict[str, object] = {}
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            list(arguments),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
            **options,  # type: ignore[call-overload]
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return str(result.stdout)


def _windows_cpu_name() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINDOWS_CPU_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "ProcessorNameString")
    except OSError:
        return None
    return " ".join(str(value).split()) or None


def parse_linux_cpuinfo(text: str) -> tuple[str | None, int | None]:
    """Return the CPU model name and physical core count from /proc/cpuinfo text."""

    model: str | None = None
    cores: set[tuple[str, str]] = set()
    physical_id: str | None = None
    core_id: str | None = None
    for line in [*text.splitlines(), ""]:
        if not line.strip():
            if physical_id is not None and core_id is not None:
                cores.add((physical_id, core_id))
            physical_id = core_id = None
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "model name" and model is None and value:
            model = value
        elif key == "physical id":
            physical_id = value
        elif key == "core id":
            core_id = value
    return model, (len(cores) or None)


def _read_linux_cpuinfo() -> tuple[str | None, int | None]:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    return parse_linux_cpuinfo(text)


def read_machine_facts() -> MachineFacts:
    """Read values reported directly by the Python runtime and operating system."""

    cpu_model: str | None = None
    confidence: float | None = None
    if sys.platform == "win32":
        cpu_model = _windows_cpu_name()
        confidence = 0.95 if cpu_model else None
    elif sys.platform.startswith("linux"):
        cpu_model = _read_linux_cpuinfo()[0]
        confidence = 0.95 if cpu_model else None
    elif sys.platform == "darwin":
        output = _run_command(["sysctl", "-n", "machdep.cpu.brand_string"], 2)
        cpu_model = output.strip() if output and output.strip() else None
        confidence = 0.95 if cpu_model else None
    if cpu_model is None:
        # platform.processor() is often only a family/model/stepping string.
        cpu_model = platform.processor() or None
        confidence = 0.5 if cpu_model else None
    return MachineFacts(
        os_name=platform.system() or None,
        os_version=platform.version() or platform.release() or None,
        architecture=platform.machine() or None,
        cpu_model=cpu_model,
        cpu_model_confidence=confidence,
    )


def _windows_physical_core_count() -> int | None:
    if sys.platform != "win32":
        return None
    relation_processor_core = 0
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel32.GetLogicalProcessorInformationEx
        query.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        query.restype = ctypes.c_int
        length = ctypes.c_ulong(0)
        query(relation_processor_core, None, ctypes.byref(length))
        if length.value == 0:
            return None
        buffer = ctypes.create_string_buffer(length.value)
        if not query(relation_processor_core, buffer, ctypes.byref(length)):
            return None
    except (AttributeError, OSError, TypeError):
        return None
    raw = buffer.raw[: length.value]
    count = 0
    offset = 0
    while offset + 8 <= len(raw):
        relationship, size = struct.unpack_from("<II", raw, offset)
        if size == 0:
            break
        if relationship == relation_processor_core:
            count += 1
        offset += size
    return count or None


def detect_physical_cores() -> int | None:
    """Count physical CPU cores through OS interfaces; return None when unavailable."""

    if sys.platform == "win32":
        return _windows_physical_core_count()
    if sys.platform.startswith("linux"):
        return _read_linux_cpuinfo()[1]
    if sys.platform == "darwin":
        output = _run_command(["sysctl", "-n", "hw.physicalcpu"], 2)
        if output and output.strip().isdigit() and int(output.strip()) > 0:
            return int(output.strip())
    return None


def detect_total_memory_bytes() -> int | None:
    """Read total physical memory using standard OS interfaces without elevation."""

    try:
        sysconf = getattr(os, "sysconf", None)
        if sysconf is None:
            raise AttributeError("sysconf is unavailable")
        page_count = int(sysconf("SC_PHYS_PAGES"))
        page_size = int(sysconf("SC_PAGE_SIZE"))
        if page_count > 0 and page_size > 0:
            return page_count * page_size
    except (AttributeError, OSError, ValueError):
        pass

    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        try:
            memory_status = MemoryStatus()
            memory_status.dwLength = ctypes.sizeof(MemoryStatus)
            win_dll = ctypes.WinDLL
            kernel32 = win_dll("kernel32", use_last_error=True)
            if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
                return int(memory_status.ullTotalPhys)
        except (AttributeError, OSError, TypeError):
            return None
    return None


@dataclass(frozen=True)
class CpuFeatures:
    """SIMD support relevant to CPU inference; None means it could not be determined."""

    avx2: bool | None = None
    avx512: bool | None = None


# IsProcessorFeaturePresent constants from winnt.h.
PF_AVX2_INSTRUCTIONS_AVAILABLE = 40
PF_AVX512F_INSTRUCTIONS_AVAILABLE = 41
_X86_ARCHITECTURES = frozenset({"x86_64", "amd64", "x64", "i386", "i686", "x86"})


def parse_linux_cpu_flags(text: str) -> CpuFeatures:
    """Read AVX2/AVX-512F from the first ``flags`` line of /proc/cpuinfo text."""

    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "flags":
            flags = set(value.split())
            return CpuFeatures(avx2="avx2" in flags, avx512="avx512f" in flags)
    return CpuFeatures()


def _sysctl_flag(name: str, runner: CommandRunner) -> bool | None:
    output = runner(["sysctl", "-n", name], 1)
    value = output.strip() if output else ""
    return value == "1" if value in {"0", "1"} else None


def detect_cpu_features(
    *,
    platform_name: str | None = None,
    architecture: str | None = None,
    command_runner: CommandRunner | None = None,
) -> CpuFeatures:
    """Detect AVX2/AVX-512F cheaply without running inference code; unknown stays None."""

    system = platform_name or sys.platform
    machine = (architecture if architecture is not None else platform.machine()).casefold()
    if machine and machine not in _X86_ARCHITECTURES:
        return CpuFeatures()  # AVX does not apply to ARM CPUs such as Apple silicon.
    if system == "win32":
        try:
            # WinDLL exists only on Windows; looking it up by name keeps type checking on
            # other platforms (where this branch never runs) quiet.
            load_windows_library = getattr(ctypes, "WinDLL", None)
            if load_windows_library is None:
                return CpuFeatures()
            kernel32 = load_windows_library("kernel32")
            present = kernel32.IsProcessorFeaturePresent
            return CpuFeatures(
                avx2=bool(present(PF_AVX2_INSTRUCTIONS_AVAILABLE)),
                avx512=bool(present(PF_AVX512F_INSTRUCTIONS_AVAILABLE)),
            )
        except (AttributeError, OSError, TypeError):
            return CpuFeatures()
    if system.startswith("linux"):
        try:
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return CpuFeatures()
        return parse_linux_cpu_flags(text)
    if system == "darwin":
        runner = command_runner or _run_command
        return CpuFeatures(
            avx2=_sysctl_flag("hw.optional.avx2_0", runner),
            avx512=_sysctl_flag("hw.optional.avx512f", runner),
        )
    return CpuFeatures()


def classify_integrated(vendor: str | None, name: str) -> bool | None:
    """Return True/False only when the vendor and product name make it unambiguous."""

    vendor_key = (vendor or "").casefold()
    lowered = name.casefold()
    if "nvidia" in vendor_key or "nvidia" in lowered:
        return False
    if "apple" in vendor_key or lowered.startswith("apple "):
        return True
    if "qualcomm" in vendor_key or "adreno" in lowered:
        return True
    if "intel" in vendor_key or "intel" in lowered:
        if re.search(r"arc(\(tm\))?\s+(pro\s+)?[ab]\d{2,3}", lowered) or re.search(
            r"iris\(r\)? xe max|data center gpu|\bdg[12]\b", lowered
        ):
            return False
        if re.search(r"graphics|\buhd\b|\bhd\b|iris|arc", lowered):
            return True
        return None
    if "amd" in vendor_key or "advanced micro devices" in vendor_key or "radeon" in lowered:
        if re.search(r"\brx\s*\d|\bpro\s+w\d|instinct|firepro|radeon\s+vii", lowered):
            return False
        if re.search(r"\b\d{3}m\b|vega\s*\d+\s+graphics|radeon(\(tm\))?\s+graphics", lowered):
            return True
        return None
    return None


def _vendor_from_pci_id(device_id: str | None) -> str | None:
    match = re.search(r"ven_([0-9a-f]{4})", device_id or "", re.IGNORECASE)
    return PCI_VENDOR_NAMES.get(match.group(1).casefold()) if match else None


def _normalize_vendor(raw_vendor: str | None) -> str | None:
    if not raw_vendor or not raw_vendor.strip():
        return None
    lowered = raw_vendor.casefold()
    for needle, canonical in (
        ("nvidia", "NVIDIA"),
        ("intel", "Intel"),
        ("advanced micro devices", "AMD"),
        ("amd", "AMD"),
        ("ati technologies", "AMD"),
        ("qualcomm", "Qualcomm"),
        ("apple", "Apple"),
    ):
        if needle in lowered:
            return canonical
    return raw_vendor.strip()


def _registry_memory_value(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, (bytes, bytearray)) and len(value) == 8:
        decoded = int.from_bytes(value, "little")
        return decoded if decoded > 0 else None
    return None


def read_windows_display_registry() -> list[DisplayRegistryEntry]:
    """Read display-driver keys; only the 64-bit qwMemorySize value is trusted for memory."""

    if sys.platform != "win32":
        return []
    import winreg

    entries: list[DisplayRegistryEntry] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINDOWS_DISPLAY_CLASS_KEY)
    except OSError:
        return entries
    with root:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not subkey_name.isdigit():
                continue
            try:
                with winreg.OpenKey(root, subkey_name) as key:
                    values: dict[str, object] = {}
                    for value_name in (
                        "DriverDesc",
                        "MatchingDeviceId",
                        "HardwareInformation.qwMemorySize",
                    ):
                        try:
                            values[value_name] = winreg.QueryValueEx(key, value_name)[0]
                        except OSError:
                            continue
            except OSError:
                continue
            description = values.get("DriverDesc")
            matching_id = values.get("MatchingDeviceId")
            entries.append(
                DisplayRegistryEntry(
                    driver_description=description if isinstance(description, str) else None,
                    matching_device_id=matching_id if isinstance(matching_id, str) else None,
                    dedicated_memory_bytes=_registry_memory_value(
                        values.get("HardwareInformation.qwMemorySize")
                    ),
                )
            )
    return entries


def _registry_entry_for(
    name: str, pnp_device_id: str | None, entries: Sequence[DisplayRegistryEntry]
) -> DisplayRegistryEntry | None:
    device = (pnp_device_id or "").casefold()
    if device:
        for entry in entries:
            matching = (entry.matching_device_id or "").casefold()
            if matching and device.startswith(matching):
                return entry
    for entry in entries:
        if entry.driver_description and entry.driver_description.strip() == name:
            return entry
    return None


def parse_windows_video_controllers(
    output: str, registry_entries: Sequence[DisplayRegistryEntry]
) -> list[AcceleratorInfo] | None:
    """Turn Win32_VideoController JSON into accelerators without trusting AdapterRAM."""

    try:
        payload = json.loads(output) if output.strip() else []
    except json.JSONDecodeError:
        return None
    rows = payload if isinstance(payload, list) else [payload]
    accelerators: list[AcceleratorInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("Name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = " ".join(name.split())
        if _NON_ACCELERATOR_ADAPTERS.search(name):
            continue
        pnp_device_id = row.get("PNPDeviceID") if isinstance(row.get("PNPDeviceID"), str) else None
        compatibility = row.get("AdapterCompatibility")
        vendor = _vendor_from_pci_id(pnp_device_id) or _normalize_vendor(
            compatibility if isinstance(compatibility, str) else None
        )
        driver_version = row.get("DriverVersion")
        integrated = classify_integrated(vendor, name)
        memory_bytes: int | None = None
        memory_kind: MemoryKind | None = None
        if integrated is True:
            memory_kind = "shared"
        else:
            entry = _registry_entry_for(name, pnp_device_id, registry_entries)
            if entry is not None and entry.dedicated_memory_bytes is not None:
                memory_bytes = entry.dedicated_memory_bytes
                memory_kind = "dedicated"
        accelerators.append(
            AcceleratorInfo(
                name=name,
                vendor=vendor,
                memory_bytes=memory_bytes,
                runtime=None,
                driver_version=(
                    driver_version.strip()
                    if isinstance(driver_version, str) and driver_version.strip()
                    else None
                ),
                integrated=integrated,
                memory_kind=memory_kind,
                detected_by="windows-video-controller",
            )
        )
    return accelerators


def parse_lspci_display_devices(output: str) -> list[AcceleratorInfo]:
    """Parse `lspci -mm` lines for VGA, 3D, and display controllers."""

    accelerators: list[AcceleratorInfo] = []
    for line in output.splitlines():
        try:
            fields = shlex.split(line)
        except ValueError:
            continue
        if len(fields) < 4:
            continue
        device_class = fields[1].casefold()
        if not any(
            token in device_class for token in ("vga compatible", "3d controller", "display")
        ):
            continue
        vendor = _normalize_vendor(fields[2])
        name = " ".join(fields[3].split())
        if not name:
            continue
        accelerators.append(
            AcceleratorInfo(
                name=name,
                vendor=vendor,
                integrated=classify_integrated(vendor, name),
                detected_by="lspci",
            )
        )
    return accelerators


def _parse_size_bytes(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(MB|GB|TB)\s*", value, re.IGNORECASE)
    if match is None:
        return None
    multiplier = {"mb": 1024**2, "gb": 1024**3, "tb": 1024**4}[match.group(2).casefold()]
    return int(float(match.group(1)) * multiplier)


def parse_macos_displays(output: str) -> list[AcceleratorInfo] | None:
    """Parse `system_profiler SPDisplaysDataType -json` output."""

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    rows = payload.get("SPDisplaysDataType") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    accelerators: list[AcceleratorInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("sppci_model") or row.get("_name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_vendor = row.get("spdisplays_vendor")
        vendor = _normalize_vendor(
            raw_vendor.removeprefix("sppci_vendor_") if isinstance(raw_vendor, str) else None
        )
        if vendor is None and name.startswith("Apple "):
            vendor = "Apple"
        dedicated = _parse_size_bytes(row.get("spdisplays_vram"))
        integrated = classify_integrated(vendor, name)
        memory_kind: MemoryKind | None = None
        if dedicated is not None and integrated is not True:
            memory_kind = "dedicated"
            integrated = False if integrated is None else integrated
        elif row.get("spdisplays_vram_shared") is not None or integrated is True:
            memory_kind = "shared"
            integrated = True
            dedicated = None
        accelerators.append(
            AcceleratorInfo(
                name=" ".join(name.split()),
                vendor=vendor,
                memory_bytes=dedicated,
                integrated=integrated,
                memory_kind=memory_kind,
                detected_by="system_profiler",
            )
        )
    return accelerators


def _query_nvidia_smi(runner: CommandRunner) -> list[AcceleratorInfo] | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    output = runner(
        [
            executable,
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        2,
    )
    if output is None:
        return None

    accelerators: list[AcceleratorInfo] = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row:
            continue
        name = row[0].strip()
        memory_bytes: int | None = None
        driver_version: str | None = None
        if len(row) > 1 and row[1].strip().isdigit():
            memory_bytes = int(row[1].strip()) * 1024 * 1024
        if len(row) > 2 and row[2].strip() not in {"", "N/A"}:
            driver_version = row[2].strip()
        if name:
            accelerators.append(
                AcceleratorInfo(
                    name=name,
                    vendor="NVIDIA",
                    memory_bytes=memory_bytes,
                    runtime="CUDA",
                    driver_version=driver_version,
                    integrated=False,
                    memory_kind="dedicated" if memory_bytes is not None else None,
                    detected_by="nvidia-smi",
                )
            )
    return accelerators


def _query_platform_adapters(
    platform_name: str,
    runner: CommandRunner,
    registry_reader: Callable[[], list[DisplayRegistryEntry]],
) -> list[AcceleratorInfo] | None:
    if platform_name == "win32":
        executable = shutil.which("powershell") or shutil.which("pwsh")
        if executable is None:
            return None
        output = runner(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                WINDOWS_VIDEO_CONTROLLER_COMMAND,
            ],
            WINDOWS_COMMAND_TIMEOUT_SECONDS,
        )
        if output is None:
            return None
        return parse_windows_video_controllers(output, registry_reader())
    if platform_name.startswith("linux"):
        executable = shutil.which("lspci")
        if executable is None:
            return None
        output = runner([executable, "-mm"], 3)
        return parse_lspci_display_devices(output) if output is not None else None
    if platform_name == "darwin":
        executable = shutil.which("system_profiler")
        if executable is None:
            return None
        output = runner([executable, "SPDisplaysDataType", "-json"], 10)
        return parse_macos_displays(output) if output is not None else None
    return None


def _comparable_gpu_name(name: str) -> str:
    lowered = re.sub(r"\((r|tm)\)", "", name.casefold())
    return re.sub(r"[^a-z0-9]", "", lowered.replace("nvidia", ""))


def merge_accelerators(
    nvidia: list[AcceleratorInfo] | None, platform_adapters: list[AcceleratorInfo] | None
) -> list[AcceleratorInfo] | None:
    """Prefer nvidia-smi details and drop the same NVIDIA GPU seen by the OS listing."""

    if nvidia is None and platform_adapters is None:
        return None
    merged = list(nvidia or [])
    remaining_nvidia_names = [_comparable_gpu_name(item.name) for item in merged]
    for adapter in platform_adapters or []:
        if adapter.vendor == "NVIDIA":
            comparable = _comparable_gpu_name(adapter.name)
            if comparable in remaining_nvidia_names:
                remaining_nvidia_names.remove(comparable)
                continue
        merged.append(adapter)
    return merged


def detect_accelerators(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    registry_reader: Callable[[], list[DisplayRegistryEntry]] | None = None,
) -> list[AcceleratorInfo] | None:
    """Combine nvidia-smi with the OS display-adapter listing; None when nothing ran."""

    runner = command_runner or _run_command
    nvidia = _query_nvidia_smi(runner)
    platform_adapters = _query_platform_adapters(
        platform_name or sys.platform,
        runner,
        registry_reader or read_windows_display_registry,
    )
    return merge_accelerators(nvidia, platform_adapters)


def inspect_local_machine(
    *,
    facts_provider: Callable[[], MachineFacts] = read_machine_facts,
    cpu_count_provider: Callable[[], int | None] = os.cpu_count,
    physical_core_provider: Callable[[], int | None] | None = None,
    memory_provider: Callable[[], int | None] = detect_total_memory_bytes,
    accelerator_provider: Callable[[], list[AcceleratorInfo] | None] = detect_accelerators,
) -> MachineProfile:
    """Return a provenance-aware snapshot of only values the host can detect."""

    facts = facts_provider()
    observed_at = utc_now()
    cpu_cores = cpu_count_provider()
    physical_cores = (physical_core_provider or detect_physical_cores)()
    total_memory = memory_provider()
    accelerators = accelerator_provider()
    provenance: dict[str, FieldProvenance] = {}

    detected_values = {
        "os_name": facts.os_name,
        "os_version": facts.os_version,
        "architecture": facts.architecture,
        "cpu_model": facts.cpu_model,
        "cpu_logical_cores": cpu_cores,
        "memory_total_bytes": total_memory,
        "accelerators": accelerators,
    }
    for key, value in {**detected_values, "cpu_physical_cores": physical_cores}.items():
        if value is not None:
            confidence = 1.0 if key in {"os_name", "architecture"} else 0.9
            if key == "cpu_model" and facts.cpu_model_confidence is not None:
                confidence = facts.cpu_model_confidence
            provenance[key] = FieldProvenance(
                source=ProvenanceSource.DETECTED,
                confidence=confidence,
                observed_at=observed_at,
            )

    # Physical cores are supplementary; their absence does not make a profile partial.
    complete = all(value is not None for value in detected_values.values())
    return MachineProfile(
        label="This machine",
        status=(
            MachineProfileStatus.DETECTED if complete else MachineProfileStatus.PARTIALLY_DETECTED
        ),
        hardware=HardwareSnapshot(
            os_name=facts.os_name,
            os_version=facts.os_version,
            architecture=facts.architecture,
            cpu_model=facts.cpu_model,
            cpu_logical_cores=cpu_cores,
            cpu_physical_cores=physical_cores if physical_cores and physical_cores > 0 else None,
            memory_total_bytes=total_memory,
            accelerators=accelerators,
        ),
        provenance=provenance,
    )
