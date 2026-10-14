"""Run deletion and cleanup, plan cloning, saved-report listing, and the model inventory."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkRun,
    BenchmarkRunStatus,
    EngineIntegrationMode,
    EngineProfile,
    ExperimentGroup,
    ExperimentGroupStatus,
    ExperimentTrial,
    ExperimentTrialStatus,
    MetricSample,
    MetricSource,
    ModelArtifact,
    ModelArtifactStatus,
    RequestOutcome,
    RequestSample,
    Workload,
)
from localforge.storage.database import open_database, transaction
from localforge.storage.repositories import (
    MetricSampleRepository,
    RecordRepository,
    RequestSampleRepository,
)
from tests.test_experiments_api import _seed_runs
from tests.test_workload_api import _authorize, _client, _ready_engine, _set_model_context


def _insert_run(
    app,
    *,
    status: BenchmarkRunStatus = BenchmarkRunStatus.COMPLETED,
    created_at: datetime | None = None,
    with_children: bool = True,
) -> UUID:
    created = created_at or datetime.now(UTC)
    run = BenchmarkRun(
        plan_id=uuid4(),
        status=status,
        created_at=created,
        updated_at=created,
    )
    connection = open_database(app.state.database_path)
    try:
        with transaction(connection):
            RecordRepository(connection, BenchmarkRun).create(run)
            if with_children:
                RequestSampleRepository(connection).create(
                    RequestSample(
                        run_id=run.id,
                        sequence_number=0,
                        outcome=RequestOutcome.SUCCEEDED,
                        started_at=created,
                        completed_at=created,
                    )
                )
                MetricSampleRepository(connection).create(
                    MetricSample(
                        run_id=run.id,
                        metric_name="latency_p50",
                        value=1.0,
                        unit="ms",
                        source=MetricSource.DERIVED,
                        aggregation="run_percentile",
                        sample_count=1,
                    )
                )
                connection.execute(
                    """INSERT INTO run_events
                       (run_id, sequence_number, event_type, monotonic_offset_ms, occurred_at,
                        payload_json)
                       VALUES (?, 0, 'run_completed', 0, ?, '{}')""",
                    (str(run.id), created.isoformat()),
                )
    finally:
        connection.close()
    return run.id


def _row_counts(app, run_id: UUID) -> dict[str, int]:
    connection = open_database(app.state.database_path)
    try:
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (str(run_id),)
            ).fetchone()[0]
            for table in ("request_samples", "metric_samples", "run_events")
        }
        counts["run"] = connection.execute(
            "SELECT COUNT(*) FROM domain_records WHERE entity_type = 'BenchmarkRun' AND id = ?",
            (str(run_id),),
        ).fetchone()[0]
        return counts
    finally:
        connection.close()


def _app(tmp_path):
    app = create_app(
        AppSettings(data_directory=tmp_path, session_token_factory=lambda: "retention-session")
    )
    return app, TestClient(app, base_url="http://127.0.0.1")


def test_delete_run_removes_owned_rows_and_requires_session(tmp_path) -> None:
    app, client = _app(tmp_path)
    with client:
        run_id = _insert_run(app)
        assert _row_counts(app, run_id) == {
            "request_samples": 1,
            "metric_samples": 1,
            "run_events": 1,
            "run": 1,
        }
        unauthorized = client.delete(f"/api/v1/runs/{run_id}")
        assert unauthorized.status_code == 403
        _authorize(client)
        response = client.delete(f"/api/v1/runs/{run_id}")
        assert response.status_code == 204, response.text
        assert set(_row_counts(app, run_id).values()) == {0}
        assert client.get(f"/api/v1/runs/{run_id}").status_code == 404

        missing = client.delete(f"/api/v1/runs/{run_id}")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "run_not_found"


def test_delete_run_rejects_active_runs(tmp_path) -> None:
    app, client = _app(tmp_path)
    with client:
        _authorize(client)
        for status in (
            BenchmarkRunStatus.QUEUED,
            BenchmarkRunStatus.RUNNING,
            BenchmarkRunStatus.CANCELLING,
        ):
            run_id = _insert_run(app, status=status)
            response = client.delete(f"/api/v1/runs/{run_id}")
            assert response.status_code == 409
            detail = response.json()["detail"]
            assert detail["code"] == "run_active"
            assert detail["run_status"] == status.value
            assert _row_counts(app, run_id)["run"] == 1
            assert _row_counts(app, run_id)["request_samples"] == 1


def test_deleted_run_becomes_a_failed_trial_without_breaking_experiment_views(tmp_path) -> None:
    app, client = _app(tmp_path)
    run_ids, plan_ids, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
    )
    group = ExperimentGroup(
        name="Temperature sweep",
        status=ExperimentGroupStatus.COMPLETED,
        baseline_plan_id=plan_ids[0],
        independent_variable="sampling_config.temperature",
        baseline_value=0.2,
        run_ids=run_ids,
        trials=[
            ExperimentTrial(
                order=index,
                variant_name="Baseline" if index == 0 else "Hot",
                variant_value=0.2 if index == 0 else 0.8,
                repetition=1,
                plan_id=plan_ids[index],
                run_id=run_ids[index],
                status=ExperimentTrialStatus.SUCCEEDED,
            )
            for index in range(2)
        ],
    )
    connection = open_database(app.state.database_path)
    try:
        RecordRepository(connection, ExperimentGroup).create(group)
    finally:
        connection.close()

    with client:
        _authorize(client)
        assert client.delete(f"/api/v1/runs/{run_ids[1]}").status_code == 204
        saved = client.get(f"/api/v1/experiments/{group.id}")
        assert saved.status_code == 200, saved.text
        trials = saved.json()["trials"]
        assert trials[0]["run_id"] == str(run_ids[0])
        assert trials[0]["status"] == "succeeded"
        assert trials[1]["run_id"] is None
        assert trials[1]["status"] == "failed"
        assert str(run_ids[1]) in trials[1]["error"]
        assert "deleted" in trials[1]["error"]
        assert saved.json()["run_ids"] == [str(run_ids[0])]
        summary = client.get(f"/api/v1/experiments/{group.id}/summary")
        assert summary.status_code == 200, summary.text
        assert len(summary.json()["failed_trials"]) == 1
        comparison = client.get(f"/api/v1/experiments/{group.id}/comparison")
        assert comparison.status_code == 409


def test_cleanup_dry_run_previews_and_real_run_deletes(tmp_path) -> None:
    app, client = _app(tmp_path)
    with client:
        _authorize(client)
        now = datetime.now(UTC)
        old_completed = _insert_run(app, created_at=now - timedelta(days=10))
        old_failed = _insert_run(
            app, status=BenchmarkRunStatus.FAILED, created_at=now - timedelta(days=9)
        )
        recent_failed = _insert_run(
            app, status=BenchmarkRunStatus.FAILED, created_at=now - timedelta(hours=1)
        )
        old_active = _insert_run(
            app, status=BenchmarkRunStatus.RUNNING, created_at=now - timedelta(days=20)
        )

        everything = client.post("/api/v1/runs/cleanup", json={})
        assert everything.status_code == 200, everything.text
        assert everything.json() == {
            "run_ids": [str(recent_failed), str(old_failed), str(old_completed)],
            "count": 3,
            "dry_run": True,
        }
        assert _row_counts(app, old_completed)["run"] == 1

        before = (now - timedelta(days=1)).isoformat()
        old_only = client.post("/api/v1/runs/cleanup", json={"before": before})
        assert old_only.json()["run_ids"] == [str(old_failed), str(old_completed)]

        failed_old = client.post(
            "/api/v1/runs/cleanup",
            json={"before": before, "statuses": ["failed"], "dry_run": True},
        )
        assert failed_old.json()["run_ids"] == [str(old_failed)]

        none_selected = client.post("/api/v1/runs/cleanup", json={"statuses": []})
        assert none_selected.json()["count"] == 0

        deleted = client.post(
            "/api/v1/runs/cleanup",
            json={"before": before, "statuses": None, "keep_latest": None, "dry_run": False},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {
            "run_ids": [str(old_failed), str(old_completed)],
            "count": 2,
            "dry_run": False,
        }
        assert set(_row_counts(app, old_completed).values()) == {0}
        assert set(_row_counts(app, old_failed).values()) == {0}
        assert _row_counts(app, recent_failed)["run"] == 1
        assert _row_counts(app, old_active)["run"] == 1


def test_cleanup_keep_latest_protects_newest_terminal_runs(tmp_path) -> None:
    app, client = _app(tmp_path)
    with client:
        _authorize(client)
        now = datetime.now(UTC)
        newest_active = _insert_run(app, status=BenchmarkRunStatus.QUEUED, created_at=now)
        terminal = [
            _insert_run(app, created_at=now - timedelta(minutes=index + 1)) for index in range(4)
        ]
        kept = client.post("/api/v1/runs/cleanup", json={"keep_latest": 2})
        assert kept.json()["run_ids"] == [str(terminal[2]), str(terminal[3])]
        zero = client.post("/api/v1/runs/cleanup", json={"keep_latest": 0})
        assert zero.json()["count"] == 4
        all_kept = client.post("/api/v1/runs/cleanup", json={"keep_latest": 10})
        assert all_kept.json()["count"] == 0

        real = client.post("/api/v1/runs/cleanup", json={"keep_latest": 3, "dry_run": False})
        assert real.json()["run_ids"] == [str(terminal[3])]
        remaining = {run["id"] for run in client.get("/api/v1/runs").json()}
        assert remaining == {str(newest_active), *(str(item) for item in terminal[:3])}


def test_cleanup_rejects_invalid_filters(tmp_path) -> None:
    _, client = _app(tmp_path)
    with client:
        _authorize(client)
        for body in (
            {"statuses": ["running"]},
            {"keep_latest": -1},
            {"before": "not a date"},
            {"unexpected": True},
        ):
            response = client.post("/api/v1/runs/cleanup", json=body)
            assert response.status_code == 422, body
            assert response.json()["detail"]["code"] == "invalid_request"


def _clone_fixture(client: TestClient, app) -> tuple[str, str, dict]:
    engine_id, model_id = _ready_engine(client)
    _set_model_context(app, model_id, 4096)
    workload = client.post(
        "/api/v1/workloads",
        json={
            "name": "Chat load",
            "kind": "chat",
            "request_count": 8,
            "concurrency": 2,
            "warmup_count": 1,
            "max_output_tokens": 32,
            "sampling_config": {"temperature": 0.2},
        },
    )
    assert workload.status_code == 201, workload.text
    created = client.post(
        "/api/v1/benchmark-plans",
        json={
            "name": "Baseline plan",
            "engine_profile_id": engine_id,
            "model_artifact_id": model_id,
            "workload_id": workload.json()["id"],
        },
    )
    assert created.status_code == 201, created.text
    return engine_id, model_id, created.json()["plan"]


def test_clone_plan_without_changes_repeats_it(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        _, _, source = _clone_fixture(client, app)
        response = client.post(f"/api/v1/benchmark-plans/{source['id']}/clone", json={})
        assert response.status_code == 201, response.text
        body = response.json()
        assert set(body) == {"plan", "preflight", "changed_fields"}
        assert body["changed_fields"] == []
        assert body["plan"]["name"] == "Baseline plan (repeat)"
        assert body["plan"]["id"] != source["id"]
        assert body["plan"]["workload_id"] == source["workload_id"]
        assert body["plan"]["status"] == "ready"
        assert body["preflight"]["valid"] is True
        named = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"name": "Custom name", "changes": {}},
        )
        assert named.json()["plan"]["name"] == "Custom name"


def test_clone_plan_workload_changes_copy_the_workload(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        _, _, source = _clone_fixture(client, app)
        source_workload = client.get(f"/api/v1/workloads/{source['workload_id']}").json()
        cases: list[tuple[dict[str, object], str, dict[str, object]]] = [
            ({"concurrency": 4}, "concurrency=4", {"concurrency": 4}),
            ({"max_output_tokens": 64}, "max_output_tokens=64", {"max_output_tokens": 64}),
            ({"warmup_count": 0}, "warmup_count=0", {"warmup_count": 0}),
            ({"request_count": 3}, "request_count=3", {"request_count": 3}),
            (
                {"temperature": 0.7},
                "temperature=0.7",
                {"sampling_config": {"temperature": 0.7}},
            ),
            ({"temperature": None}, "temperature=default", {"sampling_config": {}}),
        ]
        for changes, label, expected in cases:
            response = client.post(
                f"/api/v1/benchmark-plans/{source['id']}/clone", json={"changes": changes}
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["changed_fields"] == list(changes)
            assert body["plan"]["name"] == f"Baseline plan ({label})"
            assert body["plan"]["workload_id"] != source["workload_id"]
            assert body["plan"]["status"] == "ready"
            workload = client.get(f"/api/v1/workloads/{body['plan']['workload_id']}").json()
            for key, value in expected.items():
                assert workload[key] == value
            snapshot_workload = body["plan"]["configuration_snapshot"]["workload"]
            assert snapshot_workload["id"] == workload["id"]
        concurrency_clone = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"changes": {"concurrency": 6, "request_count": 12}},
        ).json()
        assert concurrency_clone["plan"]["concurrency"] == 6
        assert concurrency_clone["plan"]["measured_requests"] == 12
        assert concurrency_clone["changed_fields"] == ["concurrency", "request_count"]

        assert client.get(f"/api/v1/workloads/{source['workload_id']}").json() == source_workload
        plans = {plan["id"]: plan for plan in client.get("/api/v1/benchmark-plans").json()}
        assert plans[source["id"]] == source


def test_clone_plan_plan_level_changes(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id, source = _clone_fixture(client, app)
        timeout = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"changes": {"request_timeout_seconds": 30}},
        )
        assert timeout.status_code == 201, timeout.text
        assert timeout.json()["plan"]["timeout_seconds"] == 30
        assert timeout.json()["plan"]["name"] == "Baseline plan (request_timeout_seconds=30)"
        assert timeout.json()["plan"]["workload_id"] == source["workload_id"]

        default_model = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"changes": {"model_artifact_id": None}},
        )
        assert default_model.status_code == 201, default_model.text
        assert default_model.json()["plan"]["model_artifact_id"] is None
        assert default_model.json()["changed_fields"] == ["model_artifact_id"]

        same_model = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"changes": {"model_artifact_id": model_id}},
        ).json()
        assert same_model["plan"]["model_artifact_id"] == model_id
        assert "model=" in same_model["plan"]["name"]

        other_engine_id, other_model_id = _ready_engine(client)
        _set_model_context(app, other_model_id, 4096)
        moved = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"changes": {"engine_profile_id": other_engine_id}},
        )
        assert moved.status_code == 201, moved.text
        moved_plan = moved.json()["plan"]
        assert moved_plan["engine_profile_id"] == other_engine_id
        # The source model ref is matched on the new engine instead of keeping a foreign model.
        assert moved_plan["model_artifact_id"] == other_model_id
        assert moved_plan["status"] == "ready"
        assert moved.json()["changed_fields"] == ["engine_profile_id"]

        missing_engine = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"changes": {"engine_profile_id": str(uuid4())}},
        )
        assert missing_engine.status_code == 201
        assert missing_engine.json()["plan"]["status"] == "blocked"
        assert "engine_profile_missing" in {
            item["code"] for item in missing_engine.json()["preflight"]["blockers"]
        }

        plans = {plan["id"]: plan for plan in client.get("/api/v1/benchmark-plans").json()}
        assert plans[source["id"]] == source
        assert engine_id != other_engine_id


def test_clone_plan_rejects_unknown_or_invalid_changes(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        _, _, source = _clone_fixture(client, app)
        before_plans = client.get("/api/v1/benchmark-plans").json()
        before_workloads = client.get("/api/v1/workloads").json()

        unknown = client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone",
            json={"changes": {"concurrency": 2, "top_p": 0.5, "name": "x"}},
        )
        assert unknown.status_code == 422
        detail = unknown.json()["detail"]
        assert detail["code"] == "invalid_request"
        fields = {item["field"] for item in detail["fields"]}
        assert fields == {"body.changes.top_p", "body.changes.name"}

        for changes in (
            {"concurrency": 0},
            {"concurrency": None},
            {"request_timeout_seconds": 0},
            {"temperature": -1},
        ):
            invalid = client.post(
                f"/api/v1/benchmark-plans/{source['id']}/clone", json={"changes": changes}
            )
            assert invalid.status_code == 422, changes
            assert invalid.json()["detail"]["code"] == "invalid_request"

        missing = client.post(f"/api/v1/benchmark-plans/{uuid4()}/clone", json={})
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "benchmark_plan_not_found"

        assert client.get("/api/v1/benchmark-plans").json() == before_plans
        assert client.get("/api/v1/workloads").json() == before_workloads


def test_clone_plan_rejects_concurrency_change_on_a_sweep(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        _set_model_context(app, model_id, 8192)
        sweep = client.post(
            "/api/v1/workloads/presets",
            json={"name": "Sweep", "preset": "concurrency_sweep", "request_count": 4},
        )
        assert sweep.status_code == 201, sweep.text
        plan = client.post(
            "/api/v1/benchmark-plans",
            json={
                "name": "Sweep plan",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": sweep.json()["id"],
            },
        ).json()["plan"]
        response = client.post(
            f"/api/v1/benchmark-plans/{plan['id']}/clone", json={"changes": {"concurrency": 3}}
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_request"
        assert detail["fields"][0]["field"] == "body.changes.concurrency"


def test_clone_plan_uses_the_workload_snapshot_when_the_record_is_gone(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        _, _, source = _clone_fixture(client, app)
        connection = open_database(app.state.database_path)
        try:
            RecordRepository(connection, Workload).delete(UUID(source["workload_id"]))
        finally:
            connection.close()
        response = client.post(f"/api/v1/benchmark-plans/{source['id']}/clone", json={})
        assert response.status_code == 201, response.text
        plan = response.json()["plan"]
        assert plan["workload_id"] != source["workload_id"]
        assert plan["status"] == "ready"
        workload = client.get(f"/api/v1/workloads/{plan['workload_id']}").json()
        assert workload["concurrency"] == 2
        assert workload["name"] == "Chat load (repeat)"


def _create_report(client: TestClient, run_ids: list[UUID], name: str) -> dict:
    response = client.post(
        "/api/v1/reports",
        json={"name": name, "format": "json", "run_ids": [str(item) for item in run_ids]},
    )
    assert response.status_code == 201, response.text
    return response.json()["report"]


def test_report_list_download_and_delete_survive_deleted_runs(tmp_path) -> None:
    app, client = _app(tmp_path)
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
    )
    with client:
        _authorize(client)
        first = _create_report(client, run_ids, "First report")
        second = _create_report(client, run_ids, "Second report")

        listed = client.get("/api/v1/reports")
        assert listed.status_code == 200, listed.text
        items = listed.json()
        assert [item["id"] for item in items] == [second["id"], first["id"]]
        newest = items[0]
        assert newest["title"] == "Second report"
        assert newest["format"] == "json"
        assert newest["run_ids"] == [str(item) for item in run_ids]
        assert newest["available"] is True
        assert (
            newest["size_bytes"] == (tmp_path / "reports" / f"{second['id']}.json").stat().st_size
        )
        assert newest["missing_run_ids"] == []
        assert newest["download_url"] == f"/api/v1/reports/{second['id']}/download"
        for key in ("id", "created_at", "title", "format", "run_ids", "size_bytes", "available"):
            assert key in newest

        assert client.delete(f"/api/v1/runs/{run_ids[0]}").status_code == 204
        after = client.get("/api/v1/reports").json()
        assert after[0]["missing_run_ids"] == [str(run_ids[0])]
        assert after[0]["available"] is True
        downloaded = client.get(f"/api/v1/reports/{second['id']}/download")
        assert downloaded.status_code == 200
        assert str(run_ids[0]) in json.dumps(downloaded.json())

        (tmp_path / "reports" / f"{first['id']}.json").unlink()
        missing_file = {item["id"]: item for item in client.get("/api/v1/reports").json()}
        assert missing_file[first["id"]]["available"] is False
        assert missing_file[first["id"]]["size_bytes"] is None
        assert missing_file[first["id"]]["download_url"] is None
        assert client.get(f"/api/v1/reports/{first['id']}/download").status_code == 404

        unauthorized = TestClient(app, base_url="http://127.0.0.1").delete(
            f"/api/v1/reports/{second['id']}"
        )
        assert unauthorized.status_code == 403
        deleted = client.delete(f"/api/v1/reports/{second['id']}")
        assert deleted.status_code == 204, deleted.text
        assert not (tmp_path / "reports" / f"{second['id']}.json").exists()
        assert [item["id"] for item in client.get("/api/v1/reports").json()] == [first["id"]]
        assert client.delete(f"/api/v1/reports/{first['id']}").status_code == 204
        again = client.delete(f"/api/v1/reports/{first['id']}")
        assert again.status_code == 404
        assert again.json()["detail"]["code"] == "report_not_found"
        assert client.get("/api/v1/reports").json() == []
        assert client.get(f"/api/v1/runs/{run_ids[1]}").status_code == 200


def test_report_delete_never_touches_files_outside_its_own_path(tmp_path) -> None:
    app, client = _app(tmp_path)
    run_ids, _, _ = _seed_runs(
        app,
        tmp_path,
        [{"sampling_config": {"temperature": 0.2}}, {"sampling_config": {"temperature": 0.8}}],
    )
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with client:
        _authorize(client)
        report = _create_report(client, run_ids, "Tampered")
        connection = open_database(app.state.database_path)
        try:
            from localforge.core.models import ReportMetadata

            repository = RecordRepository(connection, ReportMetadata)
            saved = repository.get(UUID(report["id"]))
            assert saved is not None
            repository.update(saved.model_copy(update={"export_ref": "../outside.json"}))
        finally:
            connection.close()
        listed = client.get("/api/v1/reports").json()
        assert listed[0]["available"] is False
        assert client.delete(f"/api/v1/reports/{report['id']}").status_code == 204
    assert outside.exists()


def test_models_list_spans_engines_and_reports_unknowns_as_null(tmp_path) -> None:
    app, client = _app(tmp_path)
    with client:
        connection = open_database(app.state.database_path)
        try:
            with transaction(connection):
                zeta = EngineProfile(
                    label="Zeta engine",
                    adapter_id="localforge.ollama",
                    integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
                )
                alpha = EngineProfile(
                    label="alpha engine",
                    adapter_id="localforge.openai_compatible",
                    integration_mode=EngineIntegrationMode.ATTACHED_ENDPOINT,
                )
                for engine in (zeta, alpha):
                    RecordRepository(connection, EngineProfile).create(engine)
                models = [
                    ModelArtifact(
                        display_name="Llama",
                        engine_profile_id=zeta.id,
                        model_ref="llama3:8b",
                        format="gguf",
                        size_bytes=4_000_000_000,
                        status=ModelArtifactStatus.AVAILABLE,
                        engine_extensions={"ollama": {"quantization_level": "Q4_K_M"}},
                    ),
                    ModelArtifact(
                        display_name="Bert",
                        engine_profile_id=alpha.id,
                        model_ref="b-model",
                        revision="r1",
                        tokenizer_identity="tok",
                        max_context_tokens=4096,
                    ),
                    ModelArtifact(
                        display_name="Aardvark", engine_profile_id=alpha.id, model_ref="a-model"
                    ),
                    ModelArtifact(display_name="Orphan", engine_profile_id=None, model_ref=None),
                ]
                for model in models:
                    RecordRepository(connection, ModelArtifact).create(model)
        finally:
            connection.close()

        response = client.get("/api/v1/models")
        assert response.status_code == 200, response.text
        items = response.json()
        assert [item["model_ref"] for item in items] == ["a-model", "b-model", "llama3:8b", None]
        llama = items[2]
        assert llama["engine_label"] == "Zeta engine"
        assert llama["adapter_id"] == "localforge.ollama"
        assert llama["quantization"] == "Q4_K_M"
        assert llama["format"] == "gguf"
        assert llama["size_bytes"] == 4_000_000_000
        assert llama["status"] == "available"
        assert set(llama) >= {
            "id",
            "model_ref",
            "display_name",
            "status",
            "engine_profile_id",
            "engine_label",
            "adapter_id",
            "format",
            "quantization",
            "size_bytes",
            "max_context_tokens",
            "revision",
            "tokenizer_identity",
            "updated_at",
        }
        bert = items[1]
        assert bert["quantization"] is None
        assert bert["size_bytes"] is None
        assert bert["max_context_tokens"] == 4096
        assert bert["status"] == "unknown"
        orphan = items[3]
        assert orphan["engine_label"] is None
        assert orphan["adapter_id"] is None
        assert orphan["engine_profile_id"] is None


def test_models_list_is_empty_without_artifacts(tmp_path) -> None:
    _, client = _app(tmp_path)
    with client:
        assert client.get("/api/v1/models").json() == []


def test_clone_plan_keeps_plan_records_intact(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        _, _, source = _clone_fixture(client, app)
        client.post(
            f"/api/v1/benchmark-plans/{source['id']}/clone", json={"changes": {"concurrency": 3}}
        )
        connection = open_database(app.state.database_path)
        try:
            stored = RecordRepository(connection, BenchmarkPlan).get(UUID(source["id"]))
        finally:
            connection.close()
        assert stored is not None
        assert stored.model_dump(mode="json") == source
