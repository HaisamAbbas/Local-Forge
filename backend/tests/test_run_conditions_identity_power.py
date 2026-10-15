"""Model weight identity, the file-hash cache, and host power state readers."""

import hashlib
import os
from pathlib import Path

from localforge.run_conditions.model_identity import (
    FileHashCache,
    ollama_blob_digest,
    resolve_model_identity,
    weights_state,
)
from localforge.run_conditions.power import (
    power_changes,
    power_identity,
    power_snapshot,
    read_linux_power,
    read_macos_power,
    read_windows_power,
)

BLOB = "29d8c98fa6b098e200069bfb88b9508dc3e85586d20cba59f8dda9a808165104"


def _model_file(tmp_path: Path, content: bytes = b"GGUF fixture weights") -> Path:
    path = tmp_path / "models" / "model.gguf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_file_hash_is_computed_once_and_cached_in_the_data_directory(tmp_path) -> None:
    model = _model_file(tmp_path)
    expected = hashlib.sha256(model.read_bytes()).hexdigest()
    cache = FileHashCache(tmp_path / "data")

    assert cache.sha256(model) == (expected, model.stat().st_size)
    assert cache.sha256(model) == (expected, model.stat().st_size)
    assert cache.computed == 1
    assert (tmp_path / "data" / "cache" / "model-hash-cache.json").is_file()
    # A fresh cache object (another worker process) reuses the stored digest.
    second = FileHashCache(tmp_path / "data")
    assert second.sha256(model) == (expected, model.stat().st_size)
    assert second.computed == 0


def test_file_hash_cache_rehashes_when_the_file_changes(tmp_path) -> None:
    model = _model_file(tmp_path)
    cache = FileHashCache(tmp_path / "data")
    first = cache.sha256(model)
    model.write_bytes(b"different weights, different size")
    stat = model.stat()
    os.utime(model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    second = cache.sha256(model)
    assert first != second
    assert second is not None and second[0] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert cache.computed == 2
    assert cache.sha256(tmp_path / "missing.gguf") is None


def test_ollama_digest_comes_from_the_modelfile_from_line() -> None:
    payload = {
        "modelfile": f"# generated\n# FROM qwen2.5-coder:1.5b\nFROM C:\\blobs\\sha256-{BLOB}\n",
        "details": {"quantization_level": "Q4_K_M", "parameter_size": "1.5B"},
    }
    assert ollama_blob_digest(payload) == BLOB
    assert ollama_blob_digest({"modelfile": "FROM qwen2.5-coder:1.5b"}) is None
    identity = resolve_model_identity(
        adapter_id="localforge.ollama",
        endpoint_url="http://127.0.0.1:11434",
        observed={"ollama_show": payload},
        cache=FileHashCache(Path("unused")),
    )
    assert identity["sha256"] == BLOB
    assert identity["basis"] == "ollama_digest"
    assert identity["quantization_level"] == "Q4_K_M"


def test_ollama_blob_and_local_gguf_with_the_same_bytes_are_verified_identical(tmp_path) -> None:
    content = b"identical GGUF bytes"
    model = _model_file(tmp_path, content)
    digest = hashlib.sha256(content).hexdigest()
    llama = resolve_model_identity(
        adapter_id="localforge.llama_cpp",
        endpoint_url="http://127.0.0.1:8080",
        observed={"model_path": str(model)},
        cache=FileHashCache(tmp_path / "data"),
    )
    ollama = resolve_model_identity(
        adapter_id="localforge.ollama",
        endpoint_url="http://127.0.0.1:11434",
        observed={"ollama_show": {"modelfile": f"FROM /blobs/sha256-{digest}"}},
        cache=FileHashCache(tmp_path / "data"),
    )
    assert llama["basis"] == "file_hash"
    assert llama["file_name"] == "model.gguf"
    assert "models" not in str(llama["file_name"])
    assert weights_state([llama, ollama]) == ("verified_identical", [digest, digest])
    assert weights_state([llama, {"sha256": BLOB}])[0] == "different"
    assert weights_state([llama, {"sha256": None}])[0] == "unverified"


def test_unhashable_models_are_unknown(tmp_path) -> None:
    cache = FileHashCache(tmp_path / "data")
    remote = resolve_model_identity(
        adapter_id="localforge.llama_cpp",
        endpoint_url="https://gpu-box.example",
        observed={"model_path": str(_model_file(tmp_path))},
        cache=cache,
    )
    assert remote["basis"] == "unknown" and remote["sha256"] is None
    assert "another machine" in str(remote["note"])
    named = resolve_model_identity(
        adapter_id="localforge.koboldcpp",
        endpoint_url="http://127.0.0.1:5001",
        observed={"model_path": "koboldcpp/qwen2.5-coder-1.5b-q4_k_m"},
        cache=cache,
    )
    assert named["sha256"] is None
    managed = resolve_model_identity(
        adapter_id="localforge.llama_cpp",
        endpoint_url="http://127.0.0.1:8080",
        observed={"managed_model_path": str(_model_file(tmp_path))},
        cache=cache,
    )
    assert managed["basis"] == "file_hash"
    assert managed["source"] == "managed_launch_arguments"


def test_windows_power_reads_ac_battery_saver_plan_and_mode() -> None:
    commands: list[list[str]] = []

    def run_command(arguments: list[str]) -> str | None:
        commands.append(arguments)
        return "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n"

    state = read_windows_power(
        power_status=lambda: {
            "ac_line_status": 0,
            "battery_flag": 0,
            "battery_life_percent": 65,
            "system_status_flag": 1,
        },
        overlay_scheme=lambda: "ded574b5-45a0-4f42-8737-46345c09c238",
        run_command=run_command,
    )
    assert state["ac_power"] is False
    assert state["battery_percent"] == 65
    assert state["battery_saver"] is True
    assert state["power_plan"] == "Balanced"
    assert state["power_mode"] == "Best performance"
    assert commands == [["powercfg", "/getactivescheme"]]


def test_windows_power_unavailable_values_stay_unknown() -> None:
    state = read_windows_power(
        power_status=lambda: {
            "ac_line_status": 255,
            "battery_flag": 128,
            "battery_life_percent": 255,
            "system_status_flag": 0,
        },
        overlay_scheme=lambda: None,
        run_command=lambda arguments: None,
    )
    assert state["ac_power"] is None
    assert state["battery_present"] is False
    assert state["battery_percent"] is None
    assert state["power_plan"] is None and state["power_mode"] is None


def test_linux_power_reads_sysfs(tmp_path) -> None:
    supply = tmp_path / "sys" / "class" / "power_supply"
    (supply / "AC").mkdir(parents=True)
    (supply / "AC" / "type").write_text("Mains\n")
    (supply / "AC" / "online").write_text("1\n")
    (supply / "BAT0").mkdir()
    (supply / "BAT0" / "type").write_text("Battery\n")
    (supply / "BAT0" / "capacity").write_text("80\n")
    profile = tmp_path / "sys" / "firmware" / "acpi"
    profile.mkdir(parents=True)
    (profile / "platform_profile").write_text("balanced\n")
    governor = tmp_path / "sys" / "devices" / "system" / "cpu" / "cpu0" / "cpufreq"
    governor.mkdir(parents=True)
    (governor / "scaling_governor").write_text("powersave\n")

    state = read_linux_power(tmp_path)
    assert state["ac_power"] is True
    assert state["battery_percent"] == 80
    assert state["platform_profile"] == "balanced"
    assert state["cpu_governor"] == "powersave"
    assert read_linux_power(tmp_path / "empty")["ac_power"] is None


def test_macos_power_reads_pmset() -> None:
    outputs = {
        "batt": "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t54%; discharging",
        "": "System-wide power settings:\n lowpowermode         1\n",
    }

    def run_command(arguments: list[str]) -> str | None:
        return outputs["batt" if arguments[-1] == "batt" else ""]

    state = read_macos_power(run_command)
    assert state["ac_power"] is False
    assert state["battery_percent"] == 54
    assert state["low_power_mode"] is True


def test_power_change_during_a_run_is_flagged() -> None:
    start = {"ac_power": True, "power_mode": "Balanced", "battery_percent": 90}
    end = {"ac_power": False, "power_mode": "Balanced", "battery_percent": 88}
    assert power_changes(start, end) == ["ac_power"]
    snapshot = power_snapshot(start, end)
    assert snapshot["changed_during_run"] is True
    assert snapshot["changed_fields"] == ["ac_power"]
    assert power_snapshot(start, None)["changed_during_run"] is None
    # Battery level drains during every run on battery; it is not part of the identity.
    assert power_identity(start) == {"ac_power": True, "power_mode": "Balanced"}


def test_only_gguf_files_are_hashed_whatever_path_the_engine_reports(tmp_path: Path) -> None:
    # A local process posing as an engine could report any readable file.
    document = tmp_path / "private-notes.txt"
    document.write_text("not a model", encoding="utf-8")
    identity = resolve_model_identity(
        adapter_id="localforge.llama_cpp",
        endpoint_url="http://127.0.0.1:8080",
        observed={"model_path": str(document)},
        cache=FileHashCache(tmp_path / "data"),
    )
    assert identity["sha256"] is None
    assert identity["basis"] == "unknown"
    assert "not a GGUF model file" in str(identity["note"])
    assert "private-notes" not in str(identity)
