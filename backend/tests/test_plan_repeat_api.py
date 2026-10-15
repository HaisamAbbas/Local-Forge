"""Repeat-a-plan endpoint, run-conditions snapshot from a real worker, and repeat spread."""

import time

from tests.test_workload_api import _authorize, _client, _ready_engine


def _wait(client, run_id: str) -> dict:
    deadline = time.monotonic() + 60
    current = client.get(f"/api/v1/runs/{run_id}").json()
    while time.monotonic() < deadline and current["status"] not in {
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }:
        time.sleep(0.05)
        current = client.get(f"/api/v1/runs/{run_id}").json()
    return current


def test_repeat_plan_runs_n_times_and_summarizes_spread(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        workload = client.post(
            "/api/v1/workloads/presets",
            json={
                "name": "Repeat fixture",
                "preset": "fixed_decode",
                "request_count": 2,
                "warmup_count": 1,
                "max_output_tokens": 2,
            },
        )
        assert workload.status_code == 201, workload.text
        plan = client.post(
            "/api/v1/benchmark-plans",
            json={
                "name": "Repeat baseline",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": workload.json()["id"],
            },
        )
        assert plan.status_code == 201, plan.text
        plan_id = plan.json()["plan"]["id"]

        too_many = client.post(f"/api/v1/benchmark-plans/{plan_id}/repeat", json={"count": 11})
        assert too_many.status_code == 422
        created = client.post(f"/api/v1/benchmark-plans/{plan_id}/repeat", json={"count": 3})
        assert created.status_code == 201, created.text
        group = created.json()["group"]
        assert created.json()["estimated_run_count"] == 3
        assert group["independent_variable"] is None
        assert group["variants"] == []
        assert [trial["repetition"] for trial in group["trials"]] == [1, 2, 3]
        assert {trial["plan_id"] for trial in group["trials"]} == {plan_id}

        started = client.post(
            f"/api/v1/experiments/{group['id']}/start", json={"confirm_estimated_runtime": True}
        )
        assert started.status_code == 200, started.text
        run_ids = []
        for _ in range(3):
            launched = client.post(f"/api/v1/experiments/{group['id']}/next", json={})
            assert launched.status_code == 200, launched.text
            run = _wait(client, launched.json()["run"]["id"])
            assert run["status"] == "completed", run
            run_ids.append(run["id"])

        snapshot = client.get(f"/api/v1/runs/{run_ids[0]}").json()["profile_snapshot"]
        assert snapshot["runtime_settings"]["fields"]["threads"]["status"] == "not_reported"
        assert snapshot["model_identity"]["basis"] == "unknown"
        assert set(snapshot["power_state"]) >= {"start", "end", "changed_during_run"}
        # Unique synthetic prompts: the warmup does not prime a cache for the measured requests.
        assert snapshot["prompt_cache"]["affected_requests"] == 0

        summary = client.get(f"/api/v1/experiments/{group['id']}/summary").json()
        baseline = summary["variants"][0]
        assert baseline["succeeded_count"] == 3
        metric = next(
            item
            for item in baseline["metrics"]
            if item["metric_name"] == "request_latency_ms" and item["aggregation"] == "mean"
        )
        assert metric["repeat_count"] == 3
        assert metric["spread_min"] <= metric["median"] <= metric["spread_max"]
        assert metric["cv"] is not None
        assert isinstance(metric["unstable"], bool)
        comparison = summary["comparison"]
        assert {item["key"] for item in comparison["dimensions"]} >= {
            "runtime_settings",
            "model_weights",
            "power_state",
        }


def test_repeat_rejects_a_missing_plan(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        missing = client.post(
            "/api/v1/benchmark-plans/00000000-0000-0000-0000-000000000000/repeat",
            json={"count": 2},
        )
        assert missing.status_code == 404
