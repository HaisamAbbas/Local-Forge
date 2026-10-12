"""Best-effort hardware inspection that reports unknown values honestly."""

import csv
import ctypes
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from localforge.core.models import (
    AcceleratorInfo,
    FieldProvenance,
    HardwareSnapshot,
    MachineProfile,
    MachineProfileStatus,
    ProvenanceSource,
    utc_now,
)


@dataclass(frozen=True)
class MachineFacts:
    os_name: str | None
    os_version: str | None
    architecture: str | None
    cpu_model: str | None


def read_machine_facts() -> MachineFacts:
    """Read values reported directly by the Python runtime and operating system."""

    return MachineFacts(
        os_name=platform.system() or None,
        os_version=platform.version() or platform.release() or None,
        architecture=platform.machine() or None,
        cpu_model=platform.processor() or None,
    )


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


def detect_accelerators() -> list[AcceleratorInfo] | None:
    """Read NVIDIA model, memory, and driver from nvidia-smi when it is installed."""

    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    accelerators: list[AcceleratorInfo] = []
    for row in csv.reader(result.stdout.splitlines(), skipinitialspace=True):
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
                )
            )
    return accelerators


def inspect_local_machine(
    *,
    facts_provider: Callable[[], MachineFacts] = read_machine_facts,
    cpu_count_provider: Callable[[], int | None] = os.cpu_count,
    memory_provider: Callable[[], int | None] = detect_total_memory_bytes,
    accelerator_provider: Callable[[], list[AcceleratorInfo] | None] = detect_accelerators,
) -> MachineProfile:
    """Return a provenance-aware snapshot of only values the host can detect."""

    facts = facts_provider()
    observed_at = utc_now()
    cpu_cores = cpu_count_provider()
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
    for key, value in detected_values.items():
        if value is not None:
            provenance[key] = FieldProvenance(
                source=ProvenanceSource.DETECTED,
                confidence=1.0 if key in {"os_name", "architecture"} else 0.9,
                observed_at=observed_at,
            )

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
            memory_total_bytes=total_memory,
            accelerators=accelerators,
        ),
        provenance=provenance,
    )
