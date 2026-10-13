"""Dataset import, deterministic workloads, and benchmark-plan preflight behavior."""

import subprocess
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from localforge.adapters.catalog import AdapterCatalog
from localforge.adapters.fake import (
    FAKE_ADAPTER_ID,
    FakeEngineAdapter,
    FakeEngineScenario,
)
from localforge.api.main import AppSettings, create_app
from localforge.core.models import (
    CapabilitySnapshot,
    CapabilitySupport,
    CapabilityValue,
    EngineProfile,
    ModelArtifact,
)
from localforge.storage.database import open_database
from localforge.storage.repositories import RecordRepository
from tests.path_helpers import create_directory_link


def _client(
    tmp_path,
    *,
    max_upload_bytes: int = 100 * 1024 * 1024,
    scenario: FakeEngineScenario | None = None,
):
    scenario = scenario or FakeEngineScenario()

    def catalog_factory() -> AdapterCatalog:
        catalog = AdapterCatalog()
        catalog.register(FakeEngineAdapter(scenario))
        return catalog

    app = create_app(
        AppSettings(
            data_directory=tmp_path,
            adapter_catalog_factory=catalog_factory,
            session_token_factory=lambda: "workload-test-session",
            max_dataset_upload_bytes=max_upload_bytes,
        )
    )
    return TestClient(app, base_url="http://127.0.0.1"), app


def test_dataset_upload_limit_is_configurable_and_must_be_positive(monkeypatch) -> None:
    monkeypatch.setenv("LOCALFORGE_MAX_DATASET_UPLOAD_BYTES", "4096")
    assert AppSettings.from_environment().max_dataset_upload_bytes == 4096
    monkeypatch.setenv("LOCALFORGE_MAX_DATASET_UPLOAD_BYTES", "0")
    with pytest.raises(ValueError, match="positive integer"):
        AppSettings.from_environment()


def _authorize(client: TestClient) -> None:
    session = client.get("/api/v1/session").json()
    client.headers.update(
        {
            session["header_name"]: session["token"],
            "Origin": "http://127.0.0.1:5173",
        }
    )


def _ready_engine(client: TestClient) -> tuple[str, str]:
    created = client.post(
        "/api/v1/engines",
        json={
            "label": "Local fake",
            "adapter_id": FAKE_ADAPTER_ID,
            "integration_mode": "attached_endpoint",
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["id"]
    probe = client.post(f"/api/v1/engines/{profile_id}/probe", json={})
    assert probe.status_code == 200, probe.text
    enabled = client.post(f"/api/v1/engines/{profile_id}/enable")
    assert enabled.status_code == 200, enabled.text
    model = client.get(f"/api/v1/engines/{profile_id}/models").json()[0]
    return profile_id, model["id"]


def _set_model_context(app, model_id: str, limit: int) -> None:
    connection = open_database(app.state.database_path)
    try:
        repository = RecordRepository(connection, ModelArtifact)
        model = repository.get(UUID(model_id))
        assert model is not None
        repository.update(
            ModelArtifact.model_validate(
                model.model_dump(mode="python") | {"max_context_tokens": limit}
            )
        )
    finally:
        connection.close()


def _set_capability(app, engine_id: str, capability_name: str, state: CapabilitySupport) -> None:
    connection = open_database(app.state.database_path)
    try:
        profile = RecordRepository(connection, EngineProfile).get(UUID(engine_id))
        assert profile is not None and profile.capability_snapshot_id is not None
        repository = RecordRepository(connection, CapabilitySnapshot)
        snapshot = repository.get(profile.capability_snapshot_id)
        assert snapshot is not None
        capabilities = snapshot.capabilities | {capability_name: CapabilityValue(state=state)}
        repository.update(
            CapabilitySnapshot.model_validate(
                snapshot.model_dump(mode="python") | {"capabilities": capabilities}
            )
        )
    finally:
        connection.close()


def _upload(client: TestClient, body: str, **data: str):
    values = {
        "name": "Fixture prompts",
        "format": "jsonl",
        "prompt_field": "prompt",
        "row_limit": "100",
        "shuffle_seed": "42",
        "duplicate_policy": "keep",
        "prompt_sizing_method": "characters",
        "retain_content": "false",
    }
    values.update(data)
    return client.post(
        "/api/v1/datasets/import",
        data=values,
        files={"file": ("prompts.jsonl", body.encode("utf-8"), "application/x-ndjson")},
    )


def test_import_is_redacted_bounded_and_deterministically_sampled(tmp_path) -> None:
    content = (
        '{"prompt":"private prompt alpha","answer":"expected one"}\n'
        '{"prompt":"private prompt beta","answer":"expected two"}\n'
        '{"prompt":"private prompt gamma","answer":"expected three"}\n'
        '{"prompt":"private prompt delta","answer":"expected four"}\n'
    )
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        first = _upload(
            client,
            content,
            expected_answer_field="answer",
            row_limit="2",
            retain_content="false",
        )
        second = _upload(
            client,
            content,
            expected_answer_field="answer",
            row_limit="2",
            retain_content="false",
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        payload = first.json()
        assert payload["parsed_rows"] == 4
        assert payload["selected_rows"] == 2
        assert payload["rows_excluded_by_limit"] == 2
        assert payload["dataset"]["content_retained"] is False
        assert payload["dataset"]["storage_ref"] is None
        assert payload["dataset"]["snapshot_hash"] == second.json()["dataset"]["snapshot_hash"]
        assert payload["count_method"] == "unicode_codepoint_count"
        assert len(payload["preview"]) == 2
        assert "private prompt" not in first.text
        assert "private prompt" not in second.text
        assert list((tmp_path / "datasets").glob("*.jsonl")) == []


def test_import_retains_hash_addressed_snapshot_only_after_opt_in(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        imported = _upload(
            client,
            '{"prompt":"retained prompt"}\n',
            retain_content="true",
            prompt_sizing_method="tokenizer",
            tokenizer_name="unknown-test-tokenizer",
        )
        assert imported.status_code == 201, imported.text
        dataset = imported.json()["dataset"]
        assert dataset["content_retained"] is True
        assert dataset["prompt_field"] == "prompt"
        assert dataset["row_limit"] == 100
        assert dataset["shuffle_seed"] == 42
        assert dataset["tokenizer_identity"] == "unknown-test-tokenizer"
        assert dataset["count_method"] == "unknown_tokenizer:unknown-test-tokenizer"
        assert dataset["storage_ref"] == f"datasets/{dataset['snapshot_hash']}.jsonl"
        snapshot = tmp_path / dataset["storage_ref"]
        assert snapshot.read_text(encoding="utf-8") == (
            '{"expected_answer":null,"prompt":"retained prompt"}\n'
        )
        assert "retained prompt" not in imported.text


def test_malformed_and_empty_imports_never_create_partial_datasets(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        malformed = _upload(
            client,
            '{"prompt":"valid"}\n{"prompt":}\n',
            retain_content="true",
        )
        empty = _upload(client, "", retain_content="true")

        assert malformed.status_code == 422
        assert malformed.json()["detail"]["code"] == "malformed_jsonl"
        assert malformed.json()["detail"]["line"] == 2
        assert empty.status_code == 422
        assert empty.json()["detail"]["code"] == "empty_file"
        empty_csv = _upload(client, "", format="csv")
        assert empty_csv.status_code == 422
        assert empty_csv.json()["detail"]["code"] == "empty_file"
        assert client.get("/api/v1/datasets").json() == []
        assert list((tmp_path / "datasets").glob("*.jsonl")) == []


def test_csv_mapping_duplicate_policy_and_missing_rows_are_checked(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        csv_body = "question,answer\nfirst,one\nsecond,two\nfirst,one\n"
        imported = _upload(
            client,
            csv_body,
            format="csv",
            prompt_field="question",
            expected_answer_field="answer",
            duplicate_policy="skip",
            retain_content="true",
        )
        missing = _upload(
            client,
            "question,answer\nfirst\n",
            format="csv",
            prompt_field="question",
            expected_answer_field="answer",
        )
        duplicate_error = _upload(
            client,
            csv_body,
            format="csv",
            prompt_field="question",
            expected_answer_field="answer",
            duplicate_policy="error",
        )

        assert imported.status_code == 201, imported.text
        assert imported.json()["parsed_rows"] == 3
        assert imported.json()["selected_rows"] == 2
        assert imported.json()["duplicate_rows"] == 1
        assert missing.status_code == 422
        assert missing.json()["detail"]["code"] == "missing_field"
        assert missing.json()["detail"]["line"] == 2
        assert duplicate_error.status_code == 422
        assert duplicate_error.json()["detail"]["code"] == "duplicate_row"
        assert duplicate_error.json()["detail"]["line"] == 4
        assert len(client.get("/api/v1/datasets").json()) == 1


def test_import_rejects_path_traversal_and_upload_size_over_limit(tmp_path) -> None:
    client, _ = _client(tmp_path, max_upload_bytes=32)
    with client:
        _authorize(client)
        path_upload = client.post(
            "/api/v1/datasets/import",
            data={"name": "unsafe", "format": "jsonl"},
            files={"file": ("..\\outside.jsonl", b'{"prompt":"x"}\n', "application/jsonl")},
        )
        oversized = _upload(client, '{"prompt":"' + ("x" * 80) + '"}\n')

        assert path_upload.status_code == 422
        assert path_upload.json()["detail"]["code"] == "invalid_filename"
        assert oversized.status_code == 413
        assert oversized.json()["detail"]["code"] == "dataset_too_large"
        assert client.get("/api/v1/datasets").json() == []


def test_import_rejects_a_symlinked_upload_directory(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-upload-outside"
    outside.mkdir()
    (tmp_path / "datasets").mkdir()
    try:
        create_directory_link(tmp_path / "datasets" / "incoming", outside)
    except (NotImplementedError, OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        response = _upload(client, '{"prompt":"private upload"}\n', retain_content="true")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "dataset_storage_unavailable"
    assert list(outside.iterdir()) == []


def test_preset_workload_replays_same_seed_and_rejects_invalid_traffic(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        created = client.post(
            "/api/v1/workloads/presets",
            json={
                "name": "Rate test",
                "preset": "target_request_rate",
                "request_count": 20,
                "arrival_rate_per_second": 2,
                "random_seed": 19,
            },
        )
        assert created.status_code == 201, created.text
        workload = created.json()
        replay = client.post(f"/api/v1/workloads/{workload['id']}/replay")
        invalid = client.post(
            "/api/v1/workloads",
            json={
                "name": "Invalid open loop",
                "kind": "chat",
                "input_mode": "synthetic",
                "traffic_mode": "open_loop",
                "request_count": 10,
            },
        )
        invalid_sampling = client.post(
            "/api/v1/workloads",
            json={
                "name": "Invalid sampling",
                "kind": "chat",
                "input_mode": "synthetic",
                "request_count": 10,
                "sampling_config": {"top_p": 2},
            },
        )
        mismatched_preset = client.post(
            "/api/v1/workloads",
            json={
                "name": "Wrong preset kind",
                "kind": "chat",
                "input_mode": "synthetic",
                "preset": "fixed_prefill",
                "request_count": 10,
            },
        )

        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] != workload["id"]
        assert replay.json()["random_seed"] == workload["random_seed"]
        assert replay.json()["arrival_rate_per_second"] == 2
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "invalid_workload"
        assert invalid_sampling.status_code == 422
        assert invalid_sampling.json()["detail"]["code"] == "invalid_workload"
        assert mismatched_preset.status_code == 422
        assert mismatched_preset.json()["detail"]["code"] == "invalid_workload"
        duration_only = client.post(
            "/api/v1/workloads",
            json={
                "name": "Duration only",
                "kind": "completion",
                "input_mode": "synthetic",
                "duration_seconds": 12,
            },
        )
        assert duration_only.status_code == 201, duration_only.text
        assert duration_only.json()["request_count"] is None
        assert duration_only.json()["duration_seconds"] == 12


def test_all_documented_workload_presets_have_valid_deterministic_shapes(tmp_path) -> None:
    client, _ = _client(tmp_path)
    requests = [
        ("fixed_prefill", {}),
        ("fixed_decode", {}),
        ("mixed_chat", {}),
        ("concurrency_sweep", {}),
        ("target_request_rate", {"arrival_rate_per_second": 3}),
        ("long_context", {}),
    ]
    with client:
        _authorize(client)
        for preset, overrides in requests:
            response = client.post(
                "/api/v1/workloads/presets",
                json={
                    "name": f"{preset} fixture",
                    "preset": preset,
                    "request_count": 12,
                    "random_seed": 101,
                }
                | overrides,
            )
            assert response.status_code == 201, response.text
            workload = response.json()
            assert workload["status"] == "ready"
            assert workload["random_seed"] == 101
            assert workload["request_count"] == 12
            if preset == "concurrency_sweep":
                assert workload["concurrency_levels"] == [1, 2, 4, 8]
            if preset == "target_request_rate":
                assert workload["traffic_mode"] == "open_loop"
                assert workload["arrival_rate_per_second"] == 3


def test_model_probe_metadata_provides_known_context_for_preflight(tmp_path) -> None:
    scenario = FakeEngineScenario(
        model_metadata={
            "max_context_tokens": 1536,
            "tokenizer_identity": "fixture-tokenizer-v1",
            "format": "gguf",
            "size_bytes": 8192,
        }
    )
    client, _ = _client(tmp_path, scenario=scenario)
    with client:
        _authorize(client)
        engine_id, _ = _ready_engine(client)
        model = client.get(f"/api/v1/engines/{engine_id}/models").json()[0]
        assert model["max_context_tokens"] == 1536
        assert model["tokenizer_identity"] == "fixture-tokenizer-v1"
        assert model["format"] == "gguf"
        assert model["size_bytes"] == 8192


def test_preflight_reports_estimates_blocks_known_context_and_unknown_tokenizer(tmp_path) -> None:
    client, app = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        _set_model_context(app, model_id, 2048)
        workload = client.post(
            "/api/v1/workloads/presets",
            json={
                "name": "Rate workload",
                "preset": "target_request_rate",
                "request_count": 20,
                "arrival_rate_per_second": 2,
                "max_output_tokens": 64,
                "warmup_count": 2,
                "sampling_config": {"top_p": 0.7},
            },
        )
        assert workload.status_code == 201, workload.text
        base = {
            "name": "Rate plan",
            "engine_profile_id": engine_id,
            "model_artifact_id": model_id,
            "workload_id": workload.json()["id"],
        }
        good = client.post("/api/v1/benchmark-plans/preflight", json=base)
        created = client.post("/api/v1/benchmark-plans", json=base | {"retain_prompt_output": True})
        assert good.status_code == 200, good.text
        assert good.json()["valid"] is True
        assert good.json()["estimated_request_volume"] == 22
        assert good.json()["estimated_duration_seconds"] == 10
        assert "chat_completion" in good.json()["supported_options"]
        assert "top_p" in good.json()["unknown_options"]
        assert good.json()["workload"]["traffic_mode"] == "open_loop"
        assert good.json()["workload"]["warmup_count"] == 2
        assert good.json()["target"]["model_context_limit"] == 2048
        assert created.json()["plan"]["warmup_requests"] == 2
        assert "warmup_duration_unknown" in {item["code"] for item in good.json()["warnings"]}
        assert created.status_code == 201, created.text
        assert created.json()["plan"]["status"] == "ready"
        assert created.json()["plan"]["retain_prompt_output"] is True

        duration_workload = client.post(
            "/api/v1/workloads/presets",
            json={
                "name": "Duration-bound rate workload",
                "preset": "target_request_rate",
                "duration_seconds": 12,
                "arrival_rate_per_second": 2,
            },
        )
        duration_plan = client.post(
            "/api/v1/benchmark-plans",
            json=base | {"name": "Duration plan", "workload_id": duration_workload.json()["id"]},
        )
        assert duration_workload.status_code == 201, duration_workload.text
        assert duration_workload.json()["request_count"] is None
        assert duration_plan.status_code == 201, duration_plan.text
        assert duration_plan.json()["plan"]["measured_requests"] is None
        assert duration_plan.json()["plan"]["measurement_duration_seconds"] == 12

        long_workload = client.post(
            "/api/v1/workloads/presets",
            json={"name": "Too long", "preset": "long_context", "request_count": 4},
        )
        blocked = client.post(
            "/api/v1/benchmark-plans/preflight",
            json=base | {"name": "Blocked", "workload_id": long_workload.json()["id"]},
        )
        assert blocked.status_code == 200
        assert blocked.json()["valid"] is False
        assert "context_limit_exceeded" in {item["code"] for item in blocked.json()["blockers"]}

        unknown_workload = client.post(
            "/api/v1/workloads",
            json={
                "name": "Tokenizer unknown",
                "kind": "completion",
                "input_mode": "synthetic",
                "request_count": 1,
                "prompt_sizing_method": "tokenizer",
            },
        )
        unknown = client.post(
            "/api/v1/benchmark-plans/preflight",
            json=base | {"name": "Unknown tokenizer", "workload_id": unknown_workload.json()["id"]},
        )
        assert unknown.status_code == 200
        assert unknown.json()["valid"] is True
        assert "tokenizer" in unknown.json()["unknown_options"]
        assert "tokenizer_unknown" in {item["code"] for item in unknown.json()["warnings"]}

        unretained_import = _upload(client, '{"prompt":"private input"}\n')
        assert unretained_import.status_code == 201, unretained_import.text
        unretained_workload = client.post(
            "/api/v1/workloads",
            json={
                "name": "Unretained dataset",
                "kind": "chat",
                "input_mode": "dataset",
                "dataset_id": unretained_import.json()["dataset"]["id"],
                "request_count": 1,
            },
        )
        unretained_preflight = client.post(
            "/api/v1/benchmark-plans/preflight",
            json=base
            | {"name": "Unretained plan", "workload_id": unretained_workload.json()["id"]},
        )
        assert unretained_preflight.status_code == 200
        assert unretained_preflight.json()["valid"] is False
        assert "retained dataset content" in unretained_preflight.json()["missing_prerequisites"]
        assert "dataset_content_not_retained" in {
            item["code"] for item in unretained_preflight.json()["blockers"]
        }

        _set_capability(app, engine_id, "chat_completion", CapabilitySupport.UNSUPPORTED)
        unsupported = client.post("/api/v1/benchmark-plans/preflight", json=base)
        assert unsupported.status_code == 200
        assert unsupported.json()["valid"] is False
        assert "chat_completion" in unsupported.json()["unsupported_options"]
        assert "workload_mode_unsupported" in {
            item["code"] for item in unsupported.json()["blockers"]
        }


def test_saved_plan_preflight_detects_changed_dataset_snapshot(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        imported = _upload(client, '{"prompt":"stable prompt"}\n', retain_content="true")
        assert imported.status_code == 201, imported.text
        dataset = imported.json()["dataset"]
        workload = client.post(
            "/api/v1/workloads",
            json={
                "name": "Imported prompts",
                "kind": "chat",
                "input_mode": "dataset",
                "dataset_id": dataset["id"],
                "request_count": 1,
            },
        )
        assert workload.status_code == 201, workload.text
        response = client.post(
            "/api/v1/benchmark-plans",
            json={
                "name": "Immutable source plan",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": workload.json()["id"],
            },
        )
        assert response.status_code == 201, response.text
        plan = response.json()["plan"]
        assert plan["dataset_snapshot_hash"] == dataset["snapshot_hash"]
        assert plan["status"] == "ready"

        snapshot = tmp_path / dataset["storage_ref"]
        snapshot.write_text('{"expected_answer":null,"prompt":"changed"}\n', encoding="utf-8")
        checked = client.post(f"/api/v1/benchmark-plans/{plan['id']}/preflight")
        assert checked.status_code == 200, checked.text
        assert checked.json()["valid"] is False
        assert "dataset_snapshot_changed" in {item["code"] for item in checked.json()["blockers"]}
        saved = client.get("/api/v1/benchmark-plans").json()[0]
        assert saved["id"] == plan["id"]
        assert saved["status"] == "blocked"


def test_remote_preflight_discloses_prompt_egress_before_any_run_request(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        profile = client.post(
            "/api/v1/engines",
            json={
                "label": "Remote fake target",
                "adapter_id": FAKE_ADAPTER_ID,
                "integration_mode": "attached_endpoint",
                "endpoint_url": "https://inference.example/v1",
            },
        )
        assert profile.status_code == 201, profile.text
        engine_id = profile.json()["id"]
        probe = client.post(
            f"/api/v1/engines/{engine_id}/probe",
            json={"confirm_remote_egress": True},
        )
        assert probe.status_code == 200, probe.text
        enabled = client.post(f"/api/v1/engines/{engine_id}/enable")
        assert enabled.status_code == 200, enabled.text
        model_id = client.get(f"/api/v1/engines/{engine_id}/models").json()[0]["id"]
        workload = client.post(
            "/api/v1/workloads/presets",
            json={"name": "Remote prompt test", "preset": "mixed_chat", "request_count": 1},
        )
        preflight = client.post(
            "/api/v1/benchmark-plans/preflight",
            json={
                "name": "Remote plan",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": workload.json()["id"],
            },
        )

        assert preflight.status_code == 200, preflight.text
        disclosure = preflight.json()["remote_data_egress"]
        assert disclosure["host"] == "inference.example"
        assert disclosure["code"] == "remote_data_egress"
        assert disclosure["request_will_be_sent"] is False
        assert "prompts" in disclosure["message"]


def test_preflight_blocks_input_sizes_the_worker_cannot_honor(tmp_path) -> None:
    client, _ = _client(tmp_path)
    with client:
        _authorize(client)
        engine_id, model_id = _ready_engine(client)
        tokenizer_workload = client.post(
            "/api/v1/workloads",
            json={
                "name": "Unavailable tokenizer size",
                "kind": "chat",
                "input_mode": "synthetic",
                "request_count": 1,
                "target_input_tokens": 32,
                "prompt_sizing_method": "tokenizer",
            },
        )
        tokenizer_preflight = client.post(
            "/api/v1/benchmark-plans/preflight",
            json={
                "name": "Tokenizer size plan",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": tokenizer_workload.json()["id"],
            },
        )
        assert tokenizer_preflight.status_code == 200, tokenizer_preflight.text
        assert tokenizer_preflight.json()["valid"] is False
        assert "synthetic_tokenizer_size_unsupported" in {
            item["code"] for item in tokenizer_preflight.json()["blockers"]
        }

        imported = _upload(client, '{"prompt":"preserved dataset prompt"}\n', retain_content="true")
        dataset_workload = client.post(
            "/api/v1/workloads",
            json={
                "name": "Dataset size override",
                "kind": "chat",
                "input_mode": "dataset",
                "dataset_id": imported.json()["dataset"]["id"],
                "request_count": 1,
                "target_input_tokens": 32,
            },
        )
        dataset_preflight = client.post(
            "/api/v1/benchmark-plans/preflight",
            json={
                "name": "Dataset size plan",
                "engine_profile_id": engine_id,
                "model_artifact_id": model_id,
                "workload_id": dataset_workload.json()["id"],
            },
        )
        assert dataset_preflight.status_code == 200, dataset_preflight.text
        assert dataset_preflight.json()["valid"] is False
        assert "dataset_input_size_override_unsupported" in {
            item["code"] for item in dataset_preflight.json()["blockers"]
        }
        assert "input_size_estimate_approximate" not in {
            item["code"] for item in dataset_preflight.json()["warnings"]
        }
