"""Host power state: laptops throttle on battery and in power-saving modes.

Each platform reader returns only what the operating system reported. Unavailable values stay
``None`` and are shown as unknown.
"""

import ctypes
import re
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from localforge.core.models import JsonValue

POWER_IDENTITY_FIELDS: tuple[str, ...] = (
    "ac_power",
    "battery_saver",
    "power_plan",
    "power_mode",
    "platform_profile",
    "cpu_governor",
    "low_power_mode",
)
POWER_FIELD_LABELS: dict[str, str] = {
    "ac_power": "mains power",
    "battery_saver": "battery saver",
    "power_plan": "power plan",
    "power_mode": "power mode",
    "platform_profile": "platform power profile",
    "cpu_governor": "CPU frequency governor",
    "low_power_mode": "low power mode",
}
_COMMAND_TIMEOUT_SECONDS = 2.0
# Windows 10/11 power-mode slider overlays (PowerGetEffectiveOverlayScheme).
_WINDOWS_POWER_MODES = {
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "Best power efficiency",
    "00000000-0000-0000-0000-000000000000": "Balanced",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "Better performance",
    "ded574b5-45a0-4f42-8737-46345c09c238": "Best performance",
}

CommandRunner = Callable[[list[str]], str | None]


def _run_command(arguments: list[str]) -> str | None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _empty_state(platform_name: str) -> dict[str, JsonValue]:
    state: dict[str, JsonValue] = {
        "platform": platform_name,
        "captured_at": datetime.now(UTC).isoformat(),
        "battery_percent": None,
        "battery_present": None,
    }
    for field in POWER_IDENTITY_FIELDS:
        state[field] = None
    state["sources"] = []
    return state


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def _windows_power_status() -> dict[str, int] | None:
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    status = _SystemPowerStatus()
    try:
        if not windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    return {
        "ac_line_status": int(status.ACLineStatus),
        "battery_flag": int(status.BatteryFlag),
        "battery_life_percent": int(status.BatteryLifePercent),
        "system_status_flag": int(status.SystemStatusFlag),
    }


def _windows_overlay_scheme() -> str | None:
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    buffer = (ctypes.c_ubyte * 16)()
    try:
        function = windll.powrprof.PowerGetEffectiveOverlayScheme
        if function(ctypes.byref(buffer)) != 0:
            return None
    except (AttributeError, OSError):
        return None
    return str(uuid.UUID(bytes_le=bytes(buffer)))


def read_windows_power(
    *,
    power_status: Callable[[], dict[str, int] | None] = _windows_power_status,
    overlay_scheme: Callable[[], str | None] = _windows_overlay_scheme,
    run_command: CommandRunner = _run_command,
) -> dict[str, JsonValue]:
    state = _empty_state("windows")
    sources = cast(list[JsonValue], state["sources"])
    status = power_status()
    if status is not None:
        sources.append("GetSystemPowerStatus")
        line = status.get("ac_line_status")
        state["ac_power"] = True if line == 1 else False if line == 0 else None
        battery_flag = status.get("battery_flag", 255)
        if battery_flag == 128:
            state["battery_present"] = False
        elif battery_flag != 255:
            state["battery_present"] = True
            percent = status.get("battery_life_percent", 255)
            state["battery_percent"] = percent if 0 <= percent <= 100 else None
        state["battery_saver"] = status.get("system_status_flag") == 1
    scheme = run_command(["powercfg", "/getactivescheme"])
    if scheme:
        match = re.search(r"\(([^)]+)\)\s*$", scheme.strip())
        if match:
            state["power_plan"] = match.group(1).strip()[:120]
            sources.append("powercfg")
    overlay = overlay_scheme()
    if overlay is not None:
        state["power_mode"] = _WINDOWS_POWER_MODES.get(overlay.casefold(), overlay)
        sources.append("PowerGetEffectiveOverlayScheme")
    return state


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


def read_linux_power(root: Path = Path("/")) -> dict[str, JsonValue]:
    state = _empty_state("linux")
    sources = cast(list[JsonValue], state["sources"])
    supply_root = root / "sys" / "class" / "power_supply"
    mains_online: list[bool] = []
    try:
        supplies = sorted(supply_root.iterdir()) if supply_root.is_dir() else []
    except OSError:
        supplies = []
    for supply in supplies:
        kind = (_read_text(supply / "type") or "").casefold()
        if kind in {"mains", "usb", "usb_c", "usb_pd"}:
            online = _read_text(supply / "online")
            if online in {"0", "1"}:
                mains_online.append(online == "1")
        elif kind == "battery":
            state["battery_present"] = True
            capacity = _read_text(supply / "capacity")
            if capacity is not None and capacity.isdigit():
                state["battery_percent"] = int(capacity)
    if mains_online:
        state["ac_power"] = any(mains_online)
        sources.append("/sys/class/power_supply")
    elif state["battery_present"]:
        sources.append("/sys/class/power_supply")
    profile = _read_text(root / "sys" / "firmware" / "acpi" / "platform_profile")
    if profile is not None:
        state["platform_profile"] = profile[:80]
        sources.append("/sys/firmware/acpi/platform_profile")
    governor = _read_text(
        root / "sys" / "devices" / "system" / "cpu" / "cpu0" / "cpufreq" / "scaling_governor"
    )
    if governor is not None:
        state["cpu_governor"] = governor[:80]
        sources.append("cpufreq scaling_governor")
    return state


def read_macos_power(run_command: CommandRunner = _run_command) -> dict[str, JsonValue]:
    state = _empty_state("macos")
    sources = cast(list[JsonValue], state["sources"])
    battery = run_command(["pmset", "-g", "batt"])
    if battery:
        if "'AC Power'" in battery:
            state["ac_power"] = True
        elif "'Battery Power'" in battery:
            state["ac_power"] = False
        percent = re.search(r"(\d{1,3})%", battery)
        if percent:
            state["battery_present"] = True
            state["battery_percent"] = int(percent.group(1))
        sources.append("pmset -g batt")
    settings = run_command(["pmset", "-g"])
    if settings:
        low_power = re.search(r"^\s*lowpowermode\s+(\d)", settings, re.MULTILINE)
        if low_power:
            state["low_power_mode"] = low_power.group(1) == "1"
            sources.append("pmset -g")
    return state


def capture_power_state(platform_name: str | None = None) -> dict[str, JsonValue]:
    """Read the host power state; never raises."""

    platform_name = platform_name or sys.platform
    try:
        if platform_name == "win32":
            return read_windows_power()
        if platform_name.startswith("linux"):
            return read_linux_power()
        if platform_name == "darwin":
            return read_macos_power()
    except Exception:
        # Power context is diagnostic only; it must never fail a benchmark.
        pass
    return _empty_state(platform_name)


def power_identity(state: object) -> dict[str, JsonValue] | None:
    """The comparable part of a power state, or None when nothing was observed."""

    if not isinstance(state, Mapping):
        return None
    identity = {
        field: cast(JsonValue, state.get(field))
        for field in POWER_IDENTITY_FIELDS
        if state.get(field) is not None
    }
    return identity or None


def power_changes(start: object, end: object) -> list[str]:
    """Fields observed at both run start and end whose values differ."""

    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        return []
    return [
        field
        for field in POWER_IDENTITY_FIELDS
        if start.get(field) is not None
        and end.get(field) is not None
        and start.get(field) != end.get(field)
    ]


def power_snapshot(start: dict[str, JsonValue], end: dict[str, JsonValue] | None) -> JsonValue:
    changes = power_changes(start, end) if end is not None else []
    return {
        "start": start,
        "end": end,
        "changed_during_run": bool(changes) if end is not None else None,
        "changed_fields": cast(JsonValue, changes),
    }
