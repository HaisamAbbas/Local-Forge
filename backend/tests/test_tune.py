"""The launch-settings tuner: plan checks, search behaviour, and the command."""

import json
from pathlib import Path
from typing import Any

import pytest

from localforge import tune
from localforge.headless import BenchError, PlanError
from localforge.tune import (
    coordinate_search,
    default_search_space,
    parse_tune_plan,
    tune_main,
)

PLAN = {
    "label": "tune test",
    "engine": {
        "adapter": "localforge.llama_cpp",
        "mode": "managed_process",
        "extensions": {"llama_cpp": {"executable_path": "llama-server", "model_path": "m.gguf"}},
    },
}


def _result(throughput: float | None, ttft: float = 100.0) -> dict[str, Any]:
    metrics: dict[str, Any] = {"ttft_ms.p50": {"value": ttft}}
    if throughput is not None:
        metrics["generation_throughput_tokens_per_second.run_rate"] = {"value": throughput}
        metrics["request_latency_ms.p50"] = {"value": 1000.0 / throughput}
    return {"metrics": metrics}


def test_plan_needs_a_managed_llama_cpp_with_an_executable_and_a_model() -> None:
    plan, options = parse_tune_plan(PLAN)
    assert plan["workload"]["requests"] == 6
    assert options["objective"] == "throughput"
    with pytest.raises(PlanError, match="managed llama.cpp"):
        parse_tune_plan({"engine": {"adapter": "localforge.ollama"}})
    with pytest.raises(PlanError, match="executable_path"):
        parse_tune_plan({"engine": {"adapter": "localforge.llama_cpp", "mode": "managed_process"}})
    with pytest.raises(PlanError, match="objective"):
        parse_tune_plan({**PLAN, "tune": {"objective": "vibes"}})
    with pytest.raises(PlanError, match="whole numbers"):
        parse_tune_plan({**PLAN, "tune": {"threads": ["many"]}})
    with pytest.raises(PlanError, match="unknown tune"):
        parse_tune_plan({**PLAN, "tune": {"colour": 1}})


def test_default_space_depends_on_cores_and_gpu() -> None:
    cpu = default_search_space(logical_cores=14, physical_cores=12, has_gpu=False)
    assert cpu["threads"] == [6, 12, 14]
    assert "gpu_layers" not in cpu
    gpu = default_search_space(logical_cores=8, physical_cores=4, has_gpu=True)
    assert gpu["gpu_layers"] == [0, tune.GPU_LAYER_ALL]


def test_a_clear_win_is_adopted_and_later_settings_build_on_it() -> None:
    def evaluate(settings: dict[str, Any]) -> dict[str, Any]:
        speed = 20.0 + (5.0 if settings.get("threads") == 8 else 0.0)
        speed += 10.0 if settings.get("gpu_layers") == 999 else 0.0
        return _result(speed)

    outcome = coordinate_search(
        {"threads": [4, 8], "gpu_layers": [0, 999], "batch_size": [512]},
        evaluate,
        objective="throughput",
        min_gain_percent=5,
    )
    assert outcome["best_settings"] == {"threads": 8, "gpu_layers": 999}
    assert outcome["baseline_score"] == 20.0 and outcome["best_score"] == 35.0
    # Later trials carry the earlier winner.
    assert {"threads": 8, "gpu_layers": 999} in [item["settings"] for item in outcome["trials"]]


def test_noise_below_the_margin_keeps_the_defaults() -> None:
    scores = iter([20.0, 20.4, 19.8, 20.3, 20.2])
    outcome = coordinate_search(
        {"threads": [4, 8], "batch_size": [512, 1024]},
        lambda settings: _result(next(scores)),
        objective="throughput",
        min_gain_percent=5,
    )
    assert outcome["best_settings"] == {}
    assert outcome["changes"] == []


def test_lower_is_better_objectives_pick_the_smaller_value() -> None:
    def evaluate(settings: dict[str, Any]) -> dict[str, Any]:
        return _result(20.0, ttft=60.0 if settings.get("batch_size") == 1024 else 100.0)

    outcome = coordinate_search(
        {"batch_size": [512, 1024]},
        evaluate,
        objective="ttft",
        min_gain_percent=5,
    )
    assert outcome["best_settings"] == {"batch_size": 1024}
    assert outcome["best_score"] == 60.0


def test_a_failing_candidate_is_recorded_and_skipped() -> None:
    def evaluate(settings: dict[str, Any]) -> dict[str, Any]:
        if settings.get("gpu_layers") == 999:
            raise BenchError("out of memory")
        return _result(20.0)

    outcome = coordinate_search(
        {"gpu_layers": [999]}, evaluate, objective="throughput", min_gain_percent=5
    )
    failed = [item for item in outcome["trials"] if item["error"]]
    assert [item["error"] for item in failed] == ["out of memory"]
    assert outcome["best_settings"] == {}


def test_a_failing_baseline_stops_the_search() -> None:
    def evaluate(settings: dict[str, Any]) -> dict[str, Any]:
        raise BenchError("the engine did not start")

    outcome = coordinate_search(
        {"threads": [4]}, evaluate, objective="throughput", min_gain_percent=5
    )
    assert outcome["error"] == "the engine did not start"
    assert len(outcome["trials"]) == 1


def test_result_and_summary_name_the_recommended_settings() -> None:
    plan, options = parse_tune_plan(PLAN)
    search = {
        "trials": [
            {"settings": {}, "changed": None, "score": 20.0, "error": None},
            {"settings": {"threads": 8}, "changed": "threads", "score": 25.0, "error": None},
        ],
        "best_settings": {"threads": 8},
        "baseline_score": 20.0,
        "best_score": 25.0,
        "changes": [{"setting": "threads", "value": 8, "from_score": 20.0, "to_score": 25.0}],
        "error": None,
    }
    result = tune.build_result(plan, options, search)
    assert result["improvement_percent"] == 25.0
    assert result["recommended_launch_settings"]["threads"] == 8
    assert result["recommended_launch_settings"]["model_path"] == "m.gguf"
    text = tune.render_summary(result)
    assert "Best: threads=8" in text and "+25.0%" in text


def test_command_rejects_a_bad_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"engine": {"adapter": "localforge.ollama"}}), encoding="utf-8")
    assert tune_main([str(plan)]) == 2
    assert "managed llama.cpp" in capsys.readouterr().err
    assert tune_main([str(tmp_path / "missing.json")]) == 2
