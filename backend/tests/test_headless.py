import json
from pathlib import Path
from typing import Any

import pytest

from localforge import headless
from localforge.headless import (
    PlanError,
    bench_main,
    compare_to_baseline,
    failures_from,
    parse_plan,
    private_server,
    run_plan,
)


def _metrics(**values: float | None) -> dict[str, Any]:
    return {
        "metrics": {
            key.replace("__", "."): None if value is None else {"value": value, "flags": []}
            for key, value in values.items()
        }
    }


# --- plan validation ---------------------------------------------------------------------


def test_plan_defaults_are_filled_in() -> None:
    plan = parse_plan(
        {"engine": {"adapter": "localforge.ollama", "endpoint": "http://127.0.0.1:1"}}
    )
    assert plan["model"] == "*"
    assert plan["workload"] == {
        "preset": "mixed_chat",
        "requests": 20,
        "concurrency": 1,
        "max_output_tokens": 128,
        "warmup": 2,
    }
    assert plan["engine"]["mode"] == "attached_endpoint"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "must be a JSON object"),
        ({}, '"engine" is required'),
        ({"engine": {"adapter": "ollama"}}, "must be an adapter id"),
        ({"engine": {"adapter": "localforge.ollama", "mode": "docker"}}, "engine.mode"),
        ({"engine": {"adapter": "localforge.ollama", "port": 1}}, "unknown engine field"),
        ({"engine": {"adapter": "localforge.ollama"}, "typo": 1}, "unknown plan field"),
        (
            {"engine": {"adapter": "localforge.ollama"}, "workload": {"requests": 0}},
            "workload.requests",
        ),
        (
            {"engine": {"adapter": "localforge.ollama"}, "workload": {"requests": True}},
            "workload.requests",
        ),
        (
            {"engine": {"adapter": "localforge.ollama"}, "workload": {"colour": 1}},
            "unknown workload field",
        ),
        ({"engine": {"adapter": "localforge.ollama"}, "run_timeout_seconds": 1}, "at least 10"),
    ],
)
def test_bad_plans_say_what_to_fix(raw: object, message: str) -> None:
    with pytest.raises(PlanError, match=message):
        parse_plan(raw)


# --- baseline comparison -------------------------------------------------------------------


def test_regressions_respect_direction_and_tolerance() -> None:
    baseline = _metrics(
        ttft_ms__p50=1000.0,
        generation_throughput_tokens_per_second__run_rate=20.0,
    )
    slower_first_token = _metrics(
        ttft_ms__p50=1150.0, generation_throughput_tokens_per_second__run_rate=20.0
    )
    checks = {c["metric"]: c for c in compare_to_baseline(slower_first_token, baseline, 10)}
    assert checks["ttft_ms.p50"]["status"] == "regressed"  # +15% on a lower-is-better metric
    assert checks["ttft_ms.p50"]["change_percent"] == 15.0
    assert checks["generation_throughput_tokens_per_second.run_rate"]["status"] == "ok"

    lower_speed = _metrics(
        ttft_ms__p50=900.0, generation_throughput_tokens_per_second__run_rate=17.0
    )
    checks = {c["metric"]: c for c in compare_to_baseline(lower_speed, baseline, 10)}
    assert checks["ttft_ms.p50"]["status"] == "ok"  # faster is never a regression
    assert checks["generation_throughput_tokens_per_second.run_rate"]["status"] == "regressed"

    within = _metrics(ttft_ms__p50=1099.0, generation_throughput_tokens_per_second__run_rate=19.0)
    assert {c["status"] for c in compare_to_baseline(within, baseline, 10)} == {"ok"}


def test_a_metric_the_baseline_has_but_this_run_lacks_fails() -> None:
    baseline = _metrics(ttft_ms__p50=1000.0, ttft_ms__p95=2000.0)
    current = _metrics(ttft_ms__p50=1000.0, ttft_ms__p95=None)
    checks = compare_to_baseline(current, baseline, 10)
    missing = [c for c in checks if c["status"] == "missing"]
    assert [c["metric"] for c in missing] == ["ttft_ms.p95"]
    problems = failures_from({"run": {"status": "completed"}, **current}, checks)
    assert any("was not measured but the baseline has it" in p for p in problems)


def test_metrics_missing_from_the_baseline_are_not_compared() -> None:
    assert compare_to_baseline(_metrics(ttft_ms__p50=5.0), _metrics(), 10) == []
    assert compare_to_baseline(_metrics(ttft_ms__p50=5.0), _metrics(ttft_ms__p50=None), 10) == []


def test_zero_baselines_do_not_divide_by_zero() -> None:
    baseline = _metrics(error_rate_percent__run_rate=0.0)
    worse = compare_to_baseline(_metrics(error_rate_percent__run_rate=5.0), baseline, 10)
    assert worse[0]["status"] == "regressed" and worse[0]["change_percent"] is None
    same = compare_to_baseline(_metrics(error_rate_percent__run_rate=0.0), baseline, 10)
    assert same[0]["status"] == "ok"


def test_a_zero_time_baseline_is_skipped_and_a_zero_error_baseline_is_not() -> None:
    # Zero errors is a real baseline; a zero time is noise and has no relative change.
    noisy = compare_to_baseline(
        _metrics(tpot_ms_per_token__p50=0.0003), _metrics(tpot_ms_per_token__p50=0.0), 10
    )
    assert noisy[0]["status"] == "skipped"
    erroring = compare_to_baseline(
        _metrics(error_rate_percent__run_rate=5.0), _metrics(error_rate_percent__run_rate=0.0), 10
    )
    problems = failures_from({"run": {"status": "completed"}}, erroring)
    assert problems == ["error_rate_percent.run_rate regressed (0 -> 5)"]


def test_failures_report_failed_runs_and_failed_requests() -> None:
    result = {
        "run": {"status": "failed", "error_message": "engine went away"},
        **_metrics(requests_failed__run_total=2.0, requests_attempted__run_total=10.0),
    }
    problems = failures_from(result, [])
    assert "run failed: engine went away" in problems
    assert "2 of 10 measured requests failed" in problems


# --- command line ----------------------------------------------------------------------------


def test_missing_plan_and_bad_arguments_exit_with_usage_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bench_main([str(tmp_path / "nope.json")]) == headless.EXIT_USAGE
    assert "plan file not found" in capsys.readouterr().err

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert bench_main([str(broken)]) == headless.EXIT_USAGE
    assert "not valid JSON" in capsys.readouterr().err

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"engine": {"adapter": "localforge.fake"}}), encoding="utf-8")
    assert bench_main([str(plan), "--tolerance", "-1"]) == headless.EXIT_USAGE
    assert bench_main([str(plan), "--baseline", str(tmp_path / "gone.json")]) == headless.EXIT_USAGE


# --- end to end with the fake engine (a real private API and run worker) ----------------------

FAKE_PLAN = {
    "label": "headless fake",
    "engine": {"adapter": "localforge.fake"},
    "workload": {"requests": 3, "warmup": 1, "max_output_tokens": 16},
    "run_timeout_seconds": 120,
}


def test_a_plan_runs_end_to_end_against_a_private_api(tmp_path: Path) -> None:
    with private_server(tmp_path / "data") as api:
        result = run_plan(headless.ApiClient(api), parse_plan(FAKE_PLAN))
    assert result["schema"] == headless.RESULT_SCHEMA
    assert result["run"]["status"] == "completed"
    assert result["engine"]["probe"] == "succeeded"
    assert result["metrics"]["requests_succeeded.run_total"]["value"] == 3
    assert result["metrics"]["requests_failed.run_total"]["value"] == 0
    assert result["metrics"]["request_latency_ms.p50"]["value"] is not None


def test_the_run_records_this_machine_so_comparisons_can_check_hardware(tmp_path: Path) -> None:
    with private_server(tmp_path / "data") as api:
        client = headless.ApiClient(api)
        run_plan(client, parse_plan(FAKE_PLAN))
        machines = client.call("GET", "/machine-profiles")
        engines = client.call("GET", "/engines")
    assert len(machines) == 1
    assert [engine["machine_profile_id"] for engine in engines] == [machines[0]["id"]]


def test_bench_command_writes_the_result_and_gates_on_a_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(FAKE_PLAN), encoding="utf-8")
    first = tmp_path / "first.json"
    assert bench_main([str(plan), "--out", str(first), "--data-dir", str(tmp_path / "a")]) == 0
    saved = json.loads(first.read_text(encoding="utf-8"))
    assert saved["ok"] is True and saved["checks"] == []
    assert "PASS" in capsys.readouterr().out

    # Pretend the baseline was ten times faster: the new run must be flagged as a regression.
    baseline = json.loads(first.read_text(encoding="utf-8"))
    for key in ("request_latency_ms.p50", "ttft_ms.p50"):
        entry = baseline["metrics"].get(key)
        if entry and entry["value"]:
            entry["value"] = entry["value"] / 10
    strict = tmp_path / "strict.json"
    strict.write_text(json.dumps(baseline), encoding="utf-8")
    code = bench_main(
        [str(plan), "--baseline", str(strict), "--data-dir", str(tmp_path / "b"), "--json"]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == headless.EXIT_FAILED
    assert output["ok"] is False
    assert any("regressed" in problem for problem in output["failures"])
