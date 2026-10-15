"""Comparability of engine runtime settings, model weights, and host power state."""

from typing import Any
from uuid import UUID

from localforge.api.main import AppSettings, create_app
from localforge.comparisons import build_run_comparison
from localforge.core.models import BenchmarkRun
from localforge.run_conditions.comparability import describe_differences
from localforge.storage.database import open_database, transaction
from localforge.storage.repositories import RecordRepository
from tests.test_experiments_api import _seed_runs

SHA_A = "29d8c98fa6b098e200069bfb88b9508dc3e85586d20cba59f8dda9a808165104"
SHA_B = "5ee4f07cdb9beadbbb293e85803c569b01bd37ed059d2715faa7bb405f31caa6"
SAME_WORKLOAD = [{"sampling_config": {"temperature": 0.2}}] * 2


def _settings(**values: object) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    values = {"compute_backends": ["CPU"], **values}
    for name in (
        "compute_backends",
        "threads",
        "threads_batch",
        "gpu_layers",
        "gpu_offload_share",
        "context_size",
        "parallel_slots",
        "batch_size",
        "ubatch_size",
        "flash_attention",
        "kv_cache_type",
        "backend_devices",
    ):
        value = values.get(name)
        fields[name] = (
            {"value": value, "source": "process_command_line"}
            if value is not None
            else {"value": None, "source": None, "status": "not_reported"}
        )
    return {"schema_version": 1, "fields": fields}


def _identity(sha: str | None, basis: str = "file_hash") -> dict[str, Any]:
    return {"sha256": sha, "basis": basis if sha else "unknown", "size_bytes": 986048576}


def _power(ac_power: bool | None, *, changed: bool = False, mode: str = "Best performance"):
    start = {"platform": "windows", "ac_power": ac_power, "power_mode": mode}
    return {"start": start, "end": start, "changed_during_run": changed, "changed_fields": []}


def _compare(
    tmp_path,
    updates: list[dict[str, Any]],
    *,
    declared_variable: str | None = None,
    model_refs: list[str] | None = None,
) -> dict[str, Any]:
    app = create_app(AppSettings(data_directory=tmp_path))
    run_ids, _, _ = _seed_runs(app, tmp_path, SAME_WORKLOAD, model_refs=model_refs)
    connection = open_database(app.state.database_path)
    try:
        repository = RecordRepository(connection, BenchmarkRun)
        with transaction(connection):
            for run_id, update in zip(run_ids, updates, strict=True):
                run = repository.get(run_id)
                assert run is not None
                snapshot = dict(run.profile_snapshot)
                engine = dict(snapshot["engine"])
                if "adapter_id" in update:
                    engine["adapter_id"] = update.pop("adapter_id")
                snapshot["engine"] = engine
                snapshot.update(update)
                repository.update(run.model_copy(update={"profile_snapshot": snapshot}))
        return build_run_comparison(
            connection, [UUID(str(item)) for item in run_ids], declared_variable=declared_variable
        )
    finally:
        connection.close()


def _dimension(comparison: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in comparison["dimensions"] if item["key"] == key)


def test_koboldcpp_six_threads_vs_llama_cpp_twelve_threads_is_flagged(tmp_path) -> None:
    comparison = _compare(
        tmp_path,
        [
            {
                "adapter_id": "localforge.koboldcpp",
                "runtime_settings": _settings(
                    threads=6, gpu_layers=0, context_size=4096, parallel_slots=1
                ),
                "model_identity": _identity(SHA_A),
            },
            {
                "adapter_id": "localforge.llama_cpp",
                "runtime_settings": _settings(
                    threads=12, gpu_layers=0, context_size=4096, parallel_slots=1
                ),
                "model_identity": _identity(SHA_A, "ollama_digest"),
            },
        ],
    )

    settings = _dimension(comparison, "runtime_settings")
    assert settings["state"] == "mismatch"
    assert [item["field"] for item in settings["differing_fields"]] == ["threads"]
    assert settings["differing_fields"][0]["values"] == [6, 12]
    assert "gpu_layers" in settings["matched_fields"]
    # A different, undeclared engine plus different settings cannot be attributed to either.
    assert comparison["grade"] == "not_comparable"
    assert comparison["rationale"].startswith(
        "These runs also differ in CPU threads (6 vs 12); the speed difference may come "
        "from that, not the engine."
    )
    assert _dimension(comparison, "model_weights")["state"] == "verified_identical"


def test_declared_engine_comparison_drops_to_directional_on_a_settings_mismatch(
    tmp_path,
) -> None:
    common = {"model_identity": _identity(SHA_A), "power_state": _power(True)}
    matched = _compare(
        tmp_path / "matched",
        [
            {
                "adapter_id": "localforge.koboldcpp",
                "runtime_settings": _settings(
                    threads=6, gpu_layers=0, context_size=4096, parallel_slots=1
                ),
                **common,
            },
            {
                "adapter_id": "localforge.llama_cpp",
                "runtime_settings": _settings(
                    threads=6, gpu_layers=0, context_size=4096, parallel_slots=1
                ),
                **common,
            },
        ],
        declared_variable="engine",
    )
    assert matched["grade"] == "A"
    assert _dimension(matched, "runtime")["state"] == "declared_change"
    assert _dimension(matched, "runtime_settings")["state"] == "match"
    assert matched["declared_variable"]["path"] == "engine"

    mismatched = _compare(
        tmp_path / "mismatched",
        [
            {
                "adapter_id": "localforge.koboldcpp",
                "runtime_settings": _settings(threads=6, gpu_layers=0, parallel_slots=1),
                **common,
            },
            {
                "adapter_id": "localforge.llama_cpp",
                "runtime_settings": _settings(threads=12, gpu_layers=0, parallel_slots=4),
                **common,
            },
        ],
        declared_variable="engine",
    )
    assert mismatched["grade"] == "C"
    assert mismatched["controlled_comparison_badge"] is False
    assert mismatched["confounders"] == [
        "These runs also differ in CPU threads (6 vs 12) and parallel slots (1 vs 4); "
        "the speed difference may come from that, not the engine."
    ]


def test_cpu_versus_gpu_backend_is_a_settings_mismatch(tmp_path) -> None:
    comparison = _compare(
        tmp_path,
        [
            {"runtime_settings": _settings(threads=4, compute_backends=["CPU"])},
            {"runtime_settings": _settings(threads=4, compute_backends=["Vulkan"])},
        ],
    )
    settings = _dimension(comparison, "runtime_settings")
    assert settings["state"] == "mismatch"
    assert [item["field"] for item in settings["differing_fields"]] == ["compute_backends"]
    assert "compute backend" in comparison["rationale"]


def test_same_engine_with_different_threads_is_directional(tmp_path) -> None:
    comparison = _compare(
        tmp_path,
        [
            {"runtime_settings": _settings(threads=4)},
            {"runtime_settings": _settings(threads=8)},
        ],
    )
    assert comparison["grade"] == "C"
    assert "differ in engine runtime settings: CPU threads (4 vs 8)" in comparison["rationale"]


def test_unknown_settings_and_old_runs_keep_their_grade(tmp_path) -> None:
    comparison = _compare(tmp_path, [{}, {}])
    assert comparison["grade"] == "A"
    assert _dimension(comparison, "runtime_settings")["state"] == "unknown"
    assert _dimension(comparison, "model_weights")["state"] == "unverified"
    assert _dimension(comparison, "power_state")["state"] == "unknown"
    assert comparison["confounders"] == []

    partial = _compare(
        tmp_path / "partial",
        [
            {"runtime_settings": _settings(threads=6)},
            {"runtime_settings": _settings()},
        ],
    )
    settings = _dimension(partial, "runtime_settings")
    assert settings["state"] == "unknown"
    assert "threads" in settings["unknown_fields"]


def test_verified_identical_weights_resolve_differing_model_names(tmp_path) -> None:
    comparison = _compare(
        tmp_path,
        [{"model_identity": _identity(SHA_A)}, {"model_identity": _identity(SHA_A)}],
        model_refs=["qwen2.5-coder:1.5b", "D:/models/qwen2.5-coder-1.5b-q4_k_m.gguf"],
    )
    artifact = _dimension(comparison, "model_artifact")
    assert artifact["state"] == "match"
    assert artifact["resolved_by"] == "model_weights"
    assert comparison["grade"] != "not_comparable"


def test_different_weights_are_not_comparable(tmp_path) -> None:
    comparison = _compare(
        tmp_path,
        [{"model_identity": _identity(SHA_A)}, {"model_identity": _identity(SHA_B)}],
    )
    assert _dimension(comparison, "model_weights")["state"] == "different"
    assert comparison["grade"] == "not_comparable"
    assert "SHA-256 differs" in comparison["rationale"]

    unverified = _compare(
        tmp_path / "unverified",
        [{"model_identity": _identity(SHA_A)}, {"model_identity": _identity(None)}],
    )
    assert _dimension(unverified, "model_weights")["state"] == "unverified"
    assert unverified["grade"] == "A"


def test_power_state_mismatch_and_mid_run_change_are_directional(tmp_path) -> None:
    matched = _compare(
        tmp_path / "matched",
        [{"power_state": _power(True)}, {"power_state": _power(True)}],
    )
    assert _dimension(matched, "power_state")["state"] == "match"
    assert matched["grade"] == "A"

    battery = _compare(
        tmp_path / "battery",
        [{"power_state": _power(True)}, {"power_state": _power(False)}],
    )
    power = _dimension(battery, "power_state")
    assert power["state"] == "mismatch"
    assert battery["grade"] == "C"
    assert "mains power (on vs off)" in battery["rationale"]

    changed = _compare(
        tmp_path / "changed",
        [{"power_state": _power(True)}, {"power_state": _power(True, changed=True)}],
    )
    assert _dimension(changed, "power_state")["changed_during_run"] == [1]
    assert changed["grade"] == "C"


def test_describe_differences_reads_plainly() -> None:
    assert (
        describe_differences(
            [
                {"label": "CPU threads", "values": [6, 12]},
                {"label": "GPU layers", "values": [0, 99]},
                {"label": "flash attention", "values": [True, False]},
            ]
        )
        == "CPU threads (6 vs 12), GPU layers (0 vs 99) and flash attention (on vs off)"
    )
