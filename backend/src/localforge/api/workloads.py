"""Local workload, dataset, and benchmark-plan preflight routes."""

import json
import math
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError

from localforge import __version__
from localforge.adapters.catalog import AdapterCatalog, AdapterNotFoundError
from localforge.api.dependencies import DatabaseConnection, LocalAdapterCatalog
from localforge.api.schemas import (
    BenchmarkPlanCloneRequest,
    BenchmarkPlanCloneResponse,
    BenchmarkPlanCreate,
    BenchmarkPlanExport,
    BenchmarkPlanResponse,
    BenchmarkPlanSettings,
    BenchmarkPreflight,
    DatasetImportResponse,
    DatasetPreview,
    PreflightIssue,
    RemoteEgressDisclosure,
    WorkloadCreate,
    WorkloadPresetCreate,
)
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkPlanStatus,
    BenchmarkRun,
    BenchmarkRunStatus,
    CapabilitySnapshot,
    CapabilitySupport,
    Dataset,
    DatasetFormat,
    DatasetSource,
    DatasetStatus,
    DuplicatePolicy,
    EngineProfile,
    EngineProfileStatus,
    JsonValue,
    MachineProfile,
    ModelArtifact,
    ModelArtifactStatus,
    PromptSizingMethod,
    QualityChecks,
    RunTargets,
    TrafficMode,
    Workload,
    WorkloadInputMode,
    WorkloadKind,
    WorkloadPreset,
    WorkloadStatus,
)
from localforge.core.urls import endpoint_is_remote
from localforge.dataset_import import DatasetImportError, parse_dataset
from localforge.dataset_storage import (
    dataset_incoming_directory,
    verify_dataset_snapshot,
    write_snapshot,
)
from localforge.quality import ensure_quality_smoke_dataset
from localforge.storage.database import transaction
from localforge.storage.repositories import RecordRepository
from localforge.targets import USE_CASES_BY_PRESET, UseCasePreset, default_targets_for
from localforge.workspace_settings import (
    TEST_FIXTURE_BLOCKER_CODE,
    TEST_FIXTURE_BLOCKER_MESSAGE,
    is_test_fixture_engine_blocked,
    load_workspace_settings,
)

router = APIRouter(prefix="/api/v1")
MAX_PREVIEW_ROWS = 3


def _http_error(status_code: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **extra},
    )


def _build_workload(values: dict[str, object]) -> Workload:
    try:
        return Workload.model_validate(values | {"status": WorkloadStatus.READY})
    except ValidationError as error:
        fields = [".".join(str(part) for part in issue["loc"]) for issue in error.errors()]
        raise _http_error(
            422,
            "invalid_workload",
            "The workload settings are incompatible. Check the traffic mode and limits.",
            fields=fields,
        ) from error


def _create_workload(connection: sqlite3.Connection, values: dict[str, object]) -> Workload:
    workload = _build_workload(values)
    if workload.dataset_id is not None:
        dataset = RecordRepository(connection, Dataset).get(workload.dataset_id)
        if dataset is None:
            raise _http_error(422, "dataset_not_found", "The selected dataset was not found.")
    RecordRepository(connection, Workload).create(workload)
    return workload


def _apply_duration_only_default(values: dict[str, object], explicitly_set: set[str]) -> None:
    if "duration_seconds" in explicitly_set and "request_count" not in explicitly_set:
        values["request_count"] = None


@router.get("/datasets", response_model=list[Dataset], tags=["datasets"])
def list_datasets(connection: DatabaseConnection) -> list[Dataset]:
    return RecordRepository(connection, Dataset).list(limit=1000)


@router.post(
    "/datasets/import",
    response_model=DatasetImportResponse,
    status_code=201,
    tags=["datasets"],
)
async def import_dataset(
    request: Request,
    connection: DatabaseConnection,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=1, max_length=160)],
    format: Annotated[DatasetFormat, Form()],
    prompt_field: Annotated[str, Form(min_length=1, max_length=120)] = "prompt",
    expected_answer_field: Annotated[str | None, Form(max_length=120)] = None,
    row_limit: Annotated[int, Form(ge=1, le=100_000)] = 10_000,
    shuffle_seed: Annotated[int, Form(ge=0, le=2**63 - 1)] = 0,
    duplicate_policy: Annotated[DuplicatePolicy, Form()] = DuplicatePolicy.KEEP,
    prompt_sizing_method: Annotated[PromptSizingMethod, Form()] = PromptSizingMethod.CHARACTERS,
    tokenizer_name: Annotated[str | None, Form(max_length=160)] = None,
    retain_content: Annotated[bool, Form()] = False,
) -> DatasetImportResponse:
    """Stream a local browser upload into an optional, hash-verified immutable snapshot."""

    filename = file.filename
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 for character in filename)
    ):
        raise _http_error(422, "invalid_filename", "Upload filename must be a plain file name.")
    if not name.strip():
        raise _http_error(422, "invalid_dataset_name", "Dataset name cannot be empty.")

    data_directory = cast(Path, request.app.state.data_directory)
    maximum_bytes = cast(int, request.app.state.max_dataset_upload_bytes)
    try:
        incoming_directory = dataset_incoming_directory(data_directory)
        incoming_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="upload-", suffix=".tmp", dir=incoming_directory
        )
    except (OSError, ValueError) as error:
        raise _http_error(
            500,
            "dataset_storage_unavailable",
            "LOCALFORGE could not prepare its private dataset storage directory.",
        ) from error
    temporary_path = Path(temporary_name)
    size_bytes = 0
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as destination:
            while chunk := await file.read(64 * 1024):
                size_bytes += len(chunk)
                if size_bytes > maximum_bytes:
                    raise _http_error(
                        413,
                        "dataset_too_large",
                        f"Dataset exceeds the configured {maximum_bytes}-byte upload limit.",
                    )
                destination.write(chunk)

        try:
            parsed = parse_dataset(
                temporary_path,
                format=format,
                prompt_field=prompt_field,
                expected_answer_field=expected_answer_field,
                row_limit=row_limit,
                shuffle_seed=shuffle_seed,
                duplicate_policy=duplicate_policy,
                sizing_method=prompt_sizing_method,
                tokenizer_name=tokenizer_name,
            )
        except DatasetImportError as error:
            raise _http_error(
                422,
                error.code,
                str(error),
                line=error.line,
            ) from error

        try:
            storage_ref = write_snapshot(data_directory, parsed) if retain_content else None
        except (OSError, ValueError) as error:
            raise _http_error(
                500,
                "dataset_storage_unavailable",
                "LOCALFORGE could not safely store the retained dataset snapshot.",
            ) from error
        summary = (
            f"{parsed.parsed_rows} source rows; {len(parsed.rows)} selected; "
            f"{parsed.duplicate_rows} duplicates. Prompt content "
            f"{'retained locally' if retain_content else 'was not retained'}."
        )
        dataset = Dataset(
            name=name.strip(),
            source=DatasetSource.IMPORTED_FILE,
            status=DatasetStatus.READY,
            description=f"Imported {format.value} dataset ({size_bytes} bytes).",
            item_count=len(parsed.rows),
            content_hash=parsed.source_hash,
            snapshot_hash=parsed.snapshot_hash,
            content_retained=retain_content,
            format=format,
            prompt_field=prompt_field,
            expected_answer_field=expected_answer_field,
            row_limit=row_limit,
            shuffle_seed=shuffle_seed,
            duplicate_policy=duplicate_policy,
            prompt_sizing_method=prompt_sizing_method,
            tokenizer_identity=(
                tokenizer_name if prompt_sizing_method is PromptSizingMethod.TOKENIZER else None
            ),
            count_method=parsed.count_method,
            duplicate_count=parsed.duplicate_rows,
            storage_ref=storage_ref,
            redacted_summary=summary,
        )
        RecordRepository(connection, Dataset).create(dataset)
        preview = [
            DatasetPreview(
                row_number=row.source_row,
                prompt_preview=(
                    f"[redacted prompt, {len(row.prompt)} characters]"
                    if row.prompt_size is None
                    else f"[redacted prompt, {row.prompt_size} {parsed.count_method}]"
                ),
                prompt_size=row.prompt_size,
                expected_answer_present=row.expected_answer is not None,
            )
            for row in parsed.rows[:MAX_PREVIEW_ROWS]
        ]
        return DatasetImportResponse(
            dataset=dataset,
            preview=preview,
            parsed_rows=parsed.parsed_rows,
            selected_rows=len(parsed.rows),
            duplicate_rows=parsed.duplicate_rows,
            rows_excluded_by_limit=parsed.rows_excluded_by_limit,
            count_method=parsed.count_method,
        )
    finally:
        temporary_path.unlink(missing_ok=True)
        await file.close()


@router.post("/workloads", response_model=Workload, status_code=201, tags=["workloads"])
def create_workload(body: WorkloadCreate, connection: DatabaseConnection) -> Workload:
    values = body.model_dump(mode="python")
    _apply_duration_only_default(values, body.model_fields_set)
    return _create_workload(connection, values)


@router.post(
    "/workloads/presets",
    response_model=Workload,
    status_code=201,
    tags=["workloads"],
)
def create_workload_preset(
    body: WorkloadPresetCreate,
    request: Request,
    connection: DatabaseConnection,
) -> Workload:
    preset = body.preset
    use_case = USE_CASES_BY_PRESET.get(preset)
    if use_case is not None:
        return _create_use_case_workload(
            body, use_case, connection, cast(Path, request.app.state.data_directory)
        )
    defaults: dict[WorkloadPreset, tuple[WorkloadKind, int, int]] = {
        WorkloadPreset.FIXED_PREFILL: (WorkloadKind.COMPLETION, 1024, 32),
        WorkloadPreset.FIXED_DECODE: (WorkloadKind.COMPLETION, 128, body.max_output_tokens),
        WorkloadPreset.MIXED_CHAT: (WorkloadKind.CHAT, 512, 128),
        WorkloadPreset.CONCURRENCY_SWEEP: (WorkloadKind.CHAT, 512, 128),
        WorkloadPreset.TARGET_REQUEST_RATE: (WorkloadKind.CHAT, 512, 128),
        WorkloadPreset.LONG_CONTEXT: (WorkloadKind.COMPLETION, 8192, 128),
    }
    kind, input_tokens, output_tokens = defaults[preset]
    levels = body.concurrency_levels
    concurrency = body.concurrency
    if preset is WorkloadPreset.CONCURRENCY_SWEEP:
        levels = levels or [1, 2, 4, 8]
        concurrency = max(levels)
    traffic_mode = (
        TrafficMode.OPEN_LOOP
        if preset is WorkloadPreset.TARGET_REQUEST_RATE
        else TrafficMode.CLOSED_LOOP
    )
    values: dict[str, object] = {
        "name": body.name,
        "kind": kind,
        "input_mode": WorkloadInputMode.SYNTHETIC,
        "preset": preset,
        "request_count": body.request_count,
        "duration_seconds": body.duration_seconds,
        "concurrency": concurrency,
        "concurrency_levels": levels,
        "traffic_mode": traffic_mode,
        "arrival_rate_per_second": body.arrival_rate_per_second,
        "warmup_count": body.warmup_count,
        "target_input_tokens": input_tokens,
        "target_output_tokens": output_tokens,
        "max_output_tokens": body.max_output_tokens,
        "prompt_sizing_method": PromptSizingMethod.CHARACTERS,
        "random_seed": body.random_seed,
        "sampling_config": body.sampling_config,
        "quality_checks": body.quality_checks,
    }
    _apply_duration_only_default(values, body.model_fields_set)
    return _create_workload(connection, values)


def _create_use_case_workload(
    body: WorkloadPresetCreate,
    use_case: UseCasePreset,
    connection: sqlite3.Connection,
    data_directory: Path,
) -> Workload:
    """Build a realistic use-case workload; explicit request fields override its defaults."""

    provided = body.model_fields_set
    output_tokens = (
        body.max_output_tokens if "max_output_tokens" in provided else use_case.output_tokens
    )
    if "request_count" in provided:
        request_count = body.request_count
    elif "duration_seconds" in provided:
        request_count = None
    else:
        request_count = use_case.request_count
    values: dict[str, object] = {
        "name": body.name,
        "kind": WorkloadKind.CHAT,
        "input_mode": WorkloadInputMode.SYNTHETIC,
        "preset": use_case.preset,
        "request_count": request_count,
        "duration_seconds": body.duration_seconds,
        "concurrency": body.concurrency if "concurrency" in provided else use_case.concurrency,
        "traffic_mode": TrafficMode.CLOSED_LOOP,
        "arrival_rate_per_second": body.arrival_rate_per_second,
        "warmup_count": body.warmup_count,
        "target_input_tokens": use_case.input_tokens,
        "target_output_tokens": output_tokens,
        "max_output_tokens": output_tokens,
        "prompt_sizing_method": PromptSizingMethod.CHARACTERS,
        "random_seed": body.random_seed,
        "sampling_config": body.sampling_config,
        "quality_checks": body.quality_checks,
    }
    if use_case.preset is WorkloadPreset.QUALITY_SMOKE:
        try:
            dataset = ensure_quality_smoke_dataset(connection, data_directory)
        except (OSError, ValueError) as error:
            raise _http_error(
                500,
                "dataset_storage_unavailable",
                "LOCALFORGE could not prepare the built-in quality smoke dataset.",
            ) from error
        values |= {
            "input_mode": WorkloadInputMode.DATASET,
            "dataset_id": dataset.id,
            "target_input_tokens": None,
            "quality_checks": body.quality_checks or QualityChecks(exact_match=True),
        }
    return _create_workload(connection, values)


@router.get("/workloads", response_model=list[Workload], tags=["workloads"])
def list_workloads(connection: DatabaseConnection) -> list[Workload]:
    return RecordRepository(connection, Workload).list(limit=1000)


@router.get("/workloads/{workload_id}", response_model=Workload, tags=["workloads"])
def get_workload(workload_id: UUID, connection: DatabaseConnection) -> Workload:
    workload = RecordRepository(connection, Workload).get(workload_id)
    if workload is None:
        raise _http_error(404, "workload_not_found", "The workload was not found.")
    return workload


@router.post(
    "/workloads/{workload_id}/replay",
    response_model=Workload,
    status_code=201,
    tags=["workloads"],
)
def replay_workload(workload_id: UUID, connection: DatabaseConnection) -> Workload:
    workload = RecordRepository(connection, Workload).get(workload_id)
    if workload is None:
        raise _http_error(404, "workload_not_found", "The workload was not found.")
    replay = workload.model_copy(
        update={"id": UUID(int=0), "name": f"{workload.name} replay"[:160]}
    )
    # Construct a fresh domain record so ID and timestamps are regenerated.
    values = replay.model_dump(mode="python", exclude={"id", "created_at", "updated_at"})
    return _create_workload(connection, values)


def _add_issue(issues: list[PreflightIssue], code: str, message: str) -> None:
    if not any(issue.code == code for issue in issues):
        issues.append(PreflightIssue(code=code, message=message))


def _preflight(
    body: BenchmarkPlanCreate,
    *,
    connection: sqlite3.Connection,
    data_directory: Path,
    catalog: AdapterCatalog,
    expected_snapshot_hash: str | None = None,
) -> BenchmarkPreflight:
    blockers: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []
    missing: list[str] = []
    supported: list[str] = []
    unknown: list[str] = []
    unsupported: list[str] = []
    contention: list[str] = []
    duration: float | None = None
    volume: int | None = None
    target_machine_id = body.machine_profile_id
    workload_summary: dict[str, JsonValue] | None = None
    engine = RecordRepository(connection, EngineProfile).get(body.engine_profile_id)
    workload = RecordRepository(connection, Workload).get(body.workload_id)
    model: ModelArtifact | None = None
    machine: MachineProfile | None = None
    dataset: Dataset | None = None
    target: dict[str, object] = {
        "engine_profile_id": str(body.engine_profile_id),
        "engine_label": None,
        "endpoint": None,
        "machine_profile_id": None,
        "machine_label": None,
        "model_ref": None,
        "model_name": None,
        "model_context_limit": None,
        "workload_id": str(body.workload_id),
        "workload_name": None,
        "remote": False,
    }
    disclosure = None

    if engine is None:
        missing.append("engine profile")
        _add_issue(
            blockers, "engine_profile_missing", "The selected engine profile no longer exists."
        )
        if body.machine_profile_id is not None:
            machine = RecordRepository(connection, MachineProfile).get(body.machine_profile_id)
            if machine is None:
                missing.append("target machine profile")
                _add_issue(
                    blockers,
                    "machine_profile_missing",
                    "The selected machine profile was not found.",
                )
            else:
                target.update(
                    {"machine_profile_id": str(machine.id), "machine_label": machine.label}
                )
        if body.model_artifact_id is not None:
            model = RecordRepository(connection, ModelArtifact).get(body.model_artifact_id)
            if model is None:
                missing.append("selected model")
                _add_issue(blockers, "model_missing", "The selected model was not found.")
        else:
            missing.append("selected model")
            _add_issue(blockers, "model_missing", "Select an available model for this engine.")
    else:
        target.update(
            {
                "engine_label": engine.label,
                "endpoint": engine.endpoint_url,
                "remote": endpoint_is_remote(engine.endpoint_url),
                "adapter_id": engine.adapter_id,
                "integration_mode": engine.integration_mode.value,
                "engine_version": engine.engine_version,
            }
        )
        if engine.status is not EngineProfileStatus.ENABLED:
            missing.append("enabled and probed engine")
            _add_issue(
                blockers,
                "engine_not_ready",
                "The engine profile must be enabled and successfully probed before a run.",
            )
        if endpoint_is_remote(engine.endpoint_url):
            host = urlsplit(engine.endpoint_url or "").hostname or "configured remote host"
            disclosure = RemoteEgressDisclosure(
                code=(
                    "remote_data_egress"
                    if engine.remote_access_enabled
                    else "remote_confirmation_required"
                ),
                host=host,
                message=(
                    f"This plan will send prompts, request settings, and generated outputs "
                    f"to {host}. "
                    "Review the dataset and endpoint before starting a run."
                ),
                request_will_be_sent=False,
            )
            if not engine.remote_access_enabled:
                missing.append("remote endpoint acknowledgement")
                _add_issue(
                    blockers,
                    "remote_access_not_enabled",
                    "Remote access is not enabled for this engine profile.",
                )
        try:
            manifest = catalog.manifest_for(engine.adapter_id)
            if engine.integration_mode not in manifest.integration_modes:
                _add_issue(
                    blockers,
                    "integration_mode_unsupported",
                    "The installed adapter does not support this integration mode.",
                )
        except AdapterNotFoundError:
            manifest = None
            missing.append("installed engine adapter")
            _add_issue(blockers, "adapter_missing", "The engine adapter is not installed.")
        if is_test_fixture_engine_blocked(connection, catalog, engine.adapter_id):
            _add_issue(blockers, TEST_FIXTURE_BLOCKER_CODE, TEST_FIXTURE_BLOCKER_MESSAGE)

        machine_id = body.machine_profile_id or engine.machine_profile_id
        target_machine_id = machine_id
        if (
            body.machine_profile_id is not None
            and engine.machine_profile_id is not None
            and body.machine_profile_id != engine.machine_profile_id
        ):
            _add_issue(
                blockers,
                "machine_engine_mismatch",
                "The selected machine profile does not match the engine target machine.",
            )
        if machine_id is not None:
            machine = RecordRepository(connection, MachineProfile).get(machine_id)
            if machine is None:
                missing.append("target machine profile")
                _add_issue(
                    blockers,
                    "machine_profile_missing",
                    "The selected machine profile was not found.",
                )
            else:
                target.update(
                    {"machine_profile_id": str(machine.id), "machine_label": machine.label}
                )
        else:
            _add_issue(
                warnings,
                "machine_unknown",
                "Target machine details are unknown; hardware-based risk checks are unavailable.",
            )

        snapshot = (
            RecordRepository(connection, CapabilitySnapshot).get(engine.capability_snapshot_id)
            if engine.capability_snapshot_id is not None
            else None
        )
        capability_values = snapshot.capabilities if snapshot is not None else {}
        if workload is not None:
            capability_name = (
                "chat_completion" if workload.kind is WorkloadKind.CHAT else "raw_completion"
            )
            capability = capability_values.get(capability_name)
            if capability is None or capability.state in {
                CapabilitySupport.UNKNOWN,
                CapabilitySupport.USER_SUPPLIED,
            }:
                unknown.append(capability_name)
                _add_issue(
                    warnings,
                    "capability_unknown",
                    f"Engine support for {capability_name} is unknown.",
                )
            elif capability.state is CapabilitySupport.UNSUPPORTED:
                unsupported.append(capability_name)
                _add_issue(
                    blockers,
                    "workload_mode_unsupported",
                    f"The engine reports {capability_name} as unsupported.",
                )
            else:
                supported.append(capability_name)
            for option_name in workload.sampling_config:
                option_capability = capability_values.get(f"sampling.{option_name}")
                if option_capability is None or option_capability.state in {
                    CapabilitySupport.UNKNOWN,
                    CapabilitySupport.USER_SUPPLIED,
                }:
                    unknown.append(option_name)
                    _add_issue(
                        warnings,
                        f"unknown_option_{option_name}",
                        f"Engine support for sampling option '{option_name}' is unknown.",
                    )
                elif option_capability.state is CapabilitySupport.UNSUPPORTED:
                    unsupported.append(option_name)
                    _add_issue(
                        blockers,
                        f"unsupported_option_{option_name}",
                        f"The engine reports sampling option '{option_name}' as unsupported.",
                    )
                else:
                    supported.append(option_name)
        model_id = body.model_artifact_id
        if model_id is not None:
            model = RecordRepository(connection, ModelArtifact).get(model_id)
        elif engine.selected_model_ref is not None:
            model = next(
                (
                    item
                    for item in RecordRepository(connection, ModelArtifact).list(limit=1000)
                    if item.engine_profile_id == engine.id
                    and item.model_ref == engine.selected_model_ref
                ),
                None,
            )
        if model is None:
            missing.append("selected model")
            _add_issue(blockers, "model_missing", "Select an available model for this engine.")
        else:
            target.update({"model_ref": model.model_ref, "model_name": model.display_name})
            target["model_context_limit"] = model.max_context_tokens
            if model.engine_profile_id not in (None, engine.id):
                _add_issue(
                    blockers,
                    "model_engine_mismatch",
                    "The selected model belongs to a different engine profile.",
                )
            if model.status is ModelArtifactStatus.MISSING:
                _add_issue(blockers, "model_unavailable", "The selected model is marked missing.")
            elif model.status is ModelArtifactStatus.UNKNOWN:
                _add_issue(warnings, "model_status_unknown", "Model availability is unknown.")
            if workload is not None:
                requested_input = workload.target_input_tokens
                requested_output = workload.target_output_tokens or workload.max_output_tokens
                if model.max_context_tokens is not None and requested_input is not None:
                    if requested_input + requested_output > model.max_context_tokens:
                        _add_issue(
                            blockers,
                            "context_limit_exceeded",
                            f"Requested input and output "
                            f"({requested_input + requested_output} tokens) "
                            f"exceed the known model context limit ({model.max_context_tokens}).",
                        )
                elif model.max_context_tokens is None:
                    unknown.append("model_context_limit")
                    _add_issue(
                        warnings,
                        "context_limit_unknown",
                        "The model context limit is unknown; prompt fit cannot be verified.",
                    )
                else:
                    _add_issue(
                        warnings,
                        "prompt_size_unknown",
                        "Requested prompt size is unknown; model context fit cannot be verified.",
                    )

    if workload is None:
        missing.append("saved workload")
        _add_issue(blockers, "workload_missing", "The selected workload no longer exists.")
    else:
        target.update({"workload_name": workload.name})
        workload_summary = {
            "id": str(workload.id),
            "name": workload.name,
            "preset": workload.preset.value if workload.preset is not None else None,
            "kind": workload.kind.value,
            "input_mode": workload.input_mode.value,
            "dataset_id": str(workload.dataset_id) if workload.dataset_id is not None else None,
            "dataset_snapshot_hash": None,
            "dataset_content_hash": None,
            "dataset_content_retained": None,
            "dataset_count_method": None,
            "request_limit": workload.request_count,
            "duration_seconds": workload.duration_seconds,
            "traffic_mode": workload.traffic_mode.value,
            "concurrency": workload.concurrency,
            "concurrency_levels": cast(JsonValue, workload.concurrency_levels),
            "arrival_rate_per_second": workload.arrival_rate_per_second,
            "warmup_count": workload.warmup_count,
            "target_input_tokens": workload.target_input_tokens,
            "target_output_tokens": workload.target_output_tokens,
            "max_output_tokens": workload.max_output_tokens,
            "prompt_sizing_method": workload.prompt_sizing_method.value,
            "random_seed": workload.random_seed,
            "sampling_config": workload.sampling_config,
            "retain_prompt_output": body.retain_prompt_output,
            "quality_checks": (
                cast(JsonValue, workload.quality_checks.model_dump(mode="json"))
                if workload.quality_checks is not None
                else None
            ),
        }
        if workload.status is not WorkloadStatus.READY:
            _add_issue(blockers, "workload_not_ready", "The selected workload is not ready.")
        sweep_iterations = max(1, len(workload.concurrency_levels))
        measured_volume = workload.request_count
        if workload.duration_seconds is not None:
            duration = workload.duration_seconds * sweep_iterations
            if workload.traffic_mode is TrafficMode.OPEN_LOOP and workload.arrival_rate_per_second:
                offered = math.ceil(workload.duration_seconds * workload.arrival_rate_per_second)
                measured_volume = (
                    min(measured_volume, offered) if measured_volume is not None else offered
                )
                if workload.request_count is not None:
                    duration = (
                        min(
                            workload.duration_seconds,
                            workload.request_count / workload.arrival_rate_per_second,
                        )
                        * sweep_iterations
                    )
            if measured_volume is not None:
                volume = (measured_volume + workload.warmup_count) * sweep_iterations
        elif (
            workload.traffic_mode is TrafficMode.OPEN_LOOP
            and workload.request_count is not None
            and workload.arrival_rate_per_second
        ):
            duration = workload.request_count / workload.arrival_rate_per_second * sweep_iterations
            volume = (workload.request_count + workload.warmup_count) * sweep_iterations
        elif workload.request_count is not None:
            volume = (workload.request_count + workload.warmup_count) * sweep_iterations
            _add_issue(
                warnings,
                "duration_unknown",
                "Run duration cannot be estimated without a target rate or measured throughput.",
            )
        if workload.prompt_sizing_method is PromptSizingMethod.TOKENIZER:
            unknown.append("tokenizer")
            _add_issue(
                warnings,
                "tokenizer_unknown",
                "No tokenizer is installed for this workload; token counts are unknown.",
            )
            if (
                workload.target_input_tokens is not None
                and workload.input_mode is WorkloadInputMode.SYNTHETIC
            ):
                _add_issue(
                    blockers,
                    "synthetic_tokenizer_size_unsupported",
                    "Synthetic input cannot be sized to a tokenizer count until "
                    "a tokenizer is installed.",
                )
        elif (
            workload.target_input_tokens is not None
            and workload.input_mode is WorkloadInputMode.SYNTHETIC
        ):
            _add_issue(
                warnings,
                "input_size_estimate_approximate",
                "Synthetic input size uses a character or word approximation; "
                "actual model tokenization varies.",
            )
        if workload.dataset_id is not None and workload.target_input_tokens is not None:
            _add_issue(
                blockers,
                "dataset_input_size_override_unsupported",
                "Imported prompt text is replayed as stored; clear the synthetic "
                "input-size target to use this dataset.",
            )
        if workload.warmup_count > 0:
            _add_issue(
                warnings,
                "warmup_duration_unknown",
                "Estimated duration covers the measured phase; warmup time depends on throughput.",
            )
        if workload.dataset_id is not None:
            dataset = RecordRepository(connection, Dataset).get(workload.dataset_id)
            if dataset is None:
                missing.append("workload dataset")
                _add_issue(blockers, "dataset_missing", "The workload dataset no longer exists.")
            elif dataset.status is not DatasetStatus.READY:
                missing.append("validated dataset")
                _add_issue(
                    blockers,
                    "dataset_not_ready",
                    "The selected dataset has not completed validation.",
                )
            elif not dataset.content_retained:
                missing.append("retained dataset content")
                _add_issue(
                    blockers,
                    "dataset_content_not_retained",
                    "The imported dataset was validated without retaining prompts and "
                    "cannot be replayed.",
                )
            elif not verify_dataset_snapshot(data_directory, dataset):
                missing.append("verified dataset snapshot")
                _add_issue(
                    blockers,
                    "dataset_snapshot_changed",
                    "The retained dataset snapshot is missing or its content hash changed.",
                )
            if dataset is not None and workload_summary is not None:
                workload_summary["dataset_snapshot_hash"] = dataset.snapshot_hash
                workload_summary["dataset_content_hash"] = dataset.content_hash
                workload_summary["dataset_content_retained"] = dataset.content_retained
                workload_summary["dataset_count_method"] = dataset.count_method
            if (
                dataset is not None
                and expected_snapshot_hash is not None
                and dataset.snapshot_hash != expected_snapshot_hash
            ):
                missing.append("saved dataset snapshot hash")
                _add_issue(
                    blockers,
                    "dataset_snapshot_changed",
                    "The dataset hash no longer matches the snapshot captured by this plan.",
                )
            if dataset is not None and dataset.prompt_sizing_method is PromptSizingMethod.TOKENIZER:
                unknown.append("dataset_tokenizer")
                _add_issue(
                    warnings,
                    "dataset_tokenizer_unknown",
                    "Dataset prompt token counts are unknown because its tokenizer is unavailable.",
                )
            if (
                dataset is not None
                and workload.quality_checks is not None
                and workload.quality_checks.exact_match
                and dataset.expected_answer_field is None
            ):
                _add_issue(
                    blockers,
                    "quality_expected_answers_missing",
                    "Exact-match checks need a dataset imported with an expected-answer field.",
                )
        effective_concurrency = (
            max(workload.concurrency_levels)
            if workload.concurrency_levels
            else workload.concurrency
        )
        logical_cores = (
            machine.hardware.cpu_logical_cores
            if machine is not None and machine.hardware is not None
            else None
        )
        if logical_cores is not None and effective_concurrency > logical_cores:
            _add_issue(
                warnings,
                "high_concurrency",
                f"Configured concurrency ({effective_concurrency}) exceeds detected logical cores "
                f"({logical_cores}); contention may distort results.",
            )
        elif logical_cores is None and effective_concurrency > 64:
            _add_issue(
                warnings,
                "concurrency_capacity_unknown",
                "High concurrency is configured but available CPU capacity is unknown.",
            )

    run_repo = RecordRepository(connection, BenchmarkRun)
    plan_repo = RecordRepository(connection, BenchmarkPlan)
    active = {
        BenchmarkRunStatus.QUEUED,
        BenchmarkRunStatus.PREFLIGHT,
        BenchmarkRunStatus.PREPARING,
        BenchmarkRunStatus.WARMING,
        BenchmarkRunStatus.RUNNING,
        BenchmarkRunStatus.CANCELLING,
    }
    for run in run_repo.list(limit=1000):
        if run.status not in active:
            continue
        active_plan = plan_repo.get(run.plan_id)
        if active_plan is None:
            continue
        active_engine = RecordRepository(connection, EngineProfile).get(
            active_plan.engine_profile_id
        )
        active_machine_id = active_plan.machine_profile_id or (
            active_engine.machine_profile_id if active_engine is not None else None
        )
        if active_plan.engine_profile_id == body.engine_profile_id:
            contention.append(f"Active run {run.id} uses this engine profile.")
        elif target_machine_id is not None and active_machine_id == target_machine_id:
            contention.append(f"Active run {run.id} targets this machine profile.")
    if contention:
        _add_issue(
            warnings,
            "resource_contention",
            "Another active run targets this engine; measurements may interfere.",
        )

    target_json = cast(dict[str, JsonValue], json.loads(json.dumps(target, allow_nan=False)))
    return BenchmarkPreflight(
        valid=not blockers,
        target=target_json,
        workload=(
            cast(dict[str, JsonValue], json.loads(json.dumps(workload_summary, allow_nan=False)))
            if workload_summary is not None
            else None
        ),
        supported_options=sorted(set(supported)),
        unknown_options=sorted(set(unknown)),
        unsupported_options=sorted(set(unsupported)),
        missing_prerequisites=sorted(set(missing)),
        estimated_duration_seconds=duration,
        estimated_request_volume=volume,
        resource_contention=contention,
        remote_data_egress=disclosure,
        blockers=blockers,
        warnings=warnings,
    )


@router.post(
    "/benchmark-plans/preflight",
    response_model=BenchmarkPreflight,
    tags=["benchmark-plans"],
)
def preflight_benchmark_plan(
    body: BenchmarkPlanCreate,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> BenchmarkPreflight:
    return _preflight(
        body,
        connection=connection,
        data_directory=cast(Path, request.app.state.data_directory),
        catalog=catalog,
    )


def _plan_snapshot(
    connection: sqlite3.Connection, workload_id: UUID
) -> tuple[dict[str, object], str | None]:
    workload = RecordRepository(connection, Workload).get(workload_id)
    if workload is None:
        return {}, None
    dataset = (
        RecordRepository(connection, Dataset).get(workload.dataset_id)
        if workload.dataset_id is not None
        else None
    )
    snapshot_hash = dataset.snapshot_hash if dataset is not None else None
    snapshot: dict[str, object] = {
        "workload": workload.model_dump(mode="json"),
        "dataset_snapshot_hash": snapshot_hash,
        "dataset_source_hash": dataset.content_hash if dataset is not None else None,
        "prompt_output_retention": False,
    }
    return snapshot, snapshot_hash


@router.post(
    "/benchmark-plans",
    response_model=BenchmarkPlanResponse,
    status_code=201,
    tags=["benchmark-plans"],
)
def create_benchmark_plan(
    body: BenchmarkPlanCreate,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> BenchmarkPlanResponse:
    preflight = _preflight(
        body,
        connection=connection,
        data_directory=cast(Path, request.app.state.data_directory),
        catalog=catalog,
    )
    configuration_snapshot, snapshot_hash = _plan_snapshot(connection, body.workload_id)
    configuration_snapshot["prompt_output_retention"] = body.retain_prompt_output
    workload = RecordRepository(connection, Workload).get(body.workload_id)
    targets = (
        body.targets
        if "targets" in body.model_fields_set
        else default_targets_for(workload.preset if workload is not None else None)
    )
    configuration_snapshot["targets"] = _targets_json(targets)
    plan = BenchmarkPlan(
        name=body.name,
        status=BenchmarkPlanStatus.READY if preflight.valid else BenchmarkPlanStatus.BLOCKED,
        machine_profile_id=body.machine_profile_id,
        engine_profile_id=body.engine_profile_id,
        model_artifact_id=body.model_artifact_id,
        workload_id=body.workload_id,
        warmup_requests=workload.warmup_count if workload is not None else 0,
        measured_requests=workload.request_count if workload is not None else 1,
        measurement_duration_seconds=(workload.duration_seconds if workload is not None else None),
        concurrency=(
            max(workload.concurrency_levels)
            if workload and workload.concurrency_levels
            else workload.concurrency
            if workload
            else 1
        ),
        timeout_seconds=load_workspace_settings(connection).default_request_timeout_seconds,
        retain_prompt_output=body.retain_prompt_output,
        dataset_snapshot_hash=snapshot_hash,
        configuration_snapshot=cast(dict[str, JsonValue], configuration_snapshot),
        targets=targets,
    )
    RecordRepository(connection, BenchmarkPlan).create(plan)
    return BenchmarkPlanResponse(plan=plan, preflight=preflight)


def _targets_json(targets: RunTargets | None) -> JsonValue:
    return cast(JsonValue, targets.model_dump(mode="json")) if targets is not None else None


_CLONE_CHANGE_ORDER = (
    "model_artifact_id",
    "engine_profile_id",
    "concurrency",
    "max_output_tokens",
    "temperature",
    "warmup_count",
    "request_count",
    "request_timeout_seconds",
)
_CLONE_WORKLOAD_FIELDS = {
    "concurrency",
    "max_output_tokens",
    "temperature",
    "warmup_count",
    "request_count",
}


def _clone_value_label(connection: sqlite3.Connection, field: str, value: object) -> str:
    if field == "engine_profile_id" and isinstance(value, UUID):
        engine = RecordRepository(connection, EngineProfile).get(value)
        return f"engine={engine.label if engine is not None else value}"
    if field == "model_artifact_id":
        if value is None:
            return "model=engine default"
        model = RecordRepository(connection, ModelArtifact).get(cast(UUID, value))
        label = (model.model_ref or model.display_name) if model is not None else value
        return f"model={label}"
    if value is None:
        return f"{field}=default"
    if isinstance(value, float):
        return f"{field}={value:g}"
    return f"{field}={value}"


def _invalid_clone_change(fields: list[str], message: str) -> HTTPException:
    return _http_error(
        422,
        "invalid_request",
        message,
        fields=[{"field": f"body.changes.{name}", "error": "value_error"} for name in fields],
    )


def _cloned_workload(source: Workload, changes: dict[str, object], label: str) -> Workload:
    values = source.model_dump(mode="python")
    if "concurrency" in changes:
        if source.concurrency_levels:
            raise _invalid_clone_change(
                ["concurrency"],
                "This workload sweeps several concurrency levels; edit the sweep instead.",
            )
        values["concurrency"] = changes["concurrency"]
    for name in ("max_output_tokens", "warmup_count", "request_count"):
        if name in changes:
            values[name] = changes[name]
    if "temperature" in changes:
        sampling = dict(source.sampling_config)
        if changes["temperature"] is None:
            sampling.pop("temperature", None)
        else:
            sampling["temperature"] = cast(float, changes["temperature"])
        values["sampling_config"] = sampling
    now = datetime.now(UTC)
    values.update(
        {
            "id": uuid4(),
            "created_at": now,
            "updated_at": now,
            "name": f"{source.name} ({label})"[:160],
        }
    )
    try:
        return Workload.model_validate(values)
    except ValidationError as error:
        raise _invalid_clone_change(
            sorted(name for name in changes if name in _CLONE_WORKLOAD_FIELDS),
            "The changed values are incompatible with this workload.",
        ) from error


def _clone_model_for_engine(
    connection: sqlite3.Connection, source_plan: BenchmarkPlan, engine_id: UUID
) -> UUID | None:
    """Keep the source model if it fits the new engine, else match its ref on that engine."""

    if source_plan.model_artifact_id is None:
        return None
    source_model = RecordRepository(connection, ModelArtifact).get(source_plan.model_artifact_id)
    if source_model is None:
        return None
    if source_model.engine_profile_id in (None, engine_id):
        return source_model.id
    rows = connection.execute(
        "SELECT payload_json FROM domain_records WHERE entity_type = ?",
        (ModelArtifact.__name__,),
    ).fetchall()
    for row in rows:
        candidate = ModelArtifact.model_validate(json.loads(row["payload_json"]))
        if (
            candidate.engine_profile_id == engine_id
            and source_model.model_ref is not None
            and candidate.model_ref == source_model.model_ref
        ):
            return candidate.id
    return None


@router.post(
    "/benchmark-plans/{plan_id}/clone",
    response_model=BenchmarkPlanCloneResponse,
    status_code=201,
    tags=["benchmark-plans"],
)
def clone_benchmark_plan(
    plan_id: UUID,
    body: BenchmarkPlanCloneRequest,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> BenchmarkPlanCloneResponse:
    """Create a new plan from a saved one with a few settings changed; the source is untouched."""

    source_plan = RecordRepository(connection, BenchmarkPlan).get(plan_id)
    if source_plan is None:
        raise _http_error(404, "benchmark_plan_not_found", "The benchmark plan was not found.")
    provided = body.changes.model_fields_set
    changed_fields = [name for name in _CLONE_CHANGE_ORDER if name in provided]
    changes: dict[str, object] = {name: getattr(body.changes, name) for name in changed_fields}
    label = (
        ", ".join(_clone_value_label(connection, name, changes[name]) for name in changed_fields)
        or "repeat"
    )

    engine_id = cast(UUID, changes.get("engine_profile_id", source_plan.engine_profile_id))
    if "model_artifact_id" in changes:
        model_id = cast(UUID | None, changes["model_artifact_id"])
    elif engine_id != source_plan.engine_profile_id:
        model_id = _clone_model_for_engine(connection, source_plan, engine_id)
    else:
        model_id = source_plan.model_artifact_id
    machine_id = source_plan.machine_profile_id
    if engine_id != source_plan.engine_profile_id and machine_id is not None:
        new_engine = RecordRepository(connection, EngineProfile).get(engine_id)
        if new_engine is not None and new_engine.machine_profile_id not in (None, machine_id):
            machine_id = None

    workload_repository = RecordRepository(connection, Workload)
    source_workload = workload_repository.get(source_plan.workload_id)
    snapshot_workload = source_plan.configuration_snapshot.get("workload")
    if source_workload is None and isinstance(snapshot_workload, dict):
        source_workload = Workload.model_validate(snapshot_workload)
        persisted_source = False
    else:
        persisted_source = source_workload is not None
    workload_changes = {
        name: value for name, value in changes.items() if name in _CLONE_WORKLOAD_FIELDS
    }
    new_workload: Workload | None = None
    if workload_changes or (source_workload is not None and not persisted_source):
        if source_workload is None:
            raise _http_error(
                409, "benchmark_workload_missing", "The plan workload snapshot is unavailable."
            )
        new_workload = _cloned_workload(source_workload, workload_changes, label)
    workload_id = new_workload.id if new_workload is not None else source_plan.workload_id

    create_body = BenchmarkPlanCreate(
        name=(body.name or f"{source_plan.name} ({label})")[:160].strip(),
        engine_profile_id=engine_id,
        machine_profile_id=machine_id,
        model_artifact_id=model_id,
        workload_id=workload_id,
        retain_prompt_output=source_plan.retain_prompt_output,
    )
    timeout_seconds = cast(
        float, changes.get("request_timeout_seconds", source_plan.timeout_seconds)
    )
    with transaction(connection):
        if new_workload is not None:
            workload_repository.create(new_workload)
        preflight = _preflight(
            create_body,
            connection=connection,
            data_directory=cast(Path, request.app.state.data_directory),
            catalog=catalog,
        )
        configuration_snapshot, snapshot_hash = _plan_snapshot(connection, workload_id)
        configuration_snapshot["prompt_output_retention"] = create_body.retain_prompt_output
        configuration_snapshot["targets"] = _targets_json(source_plan.targets)
        workload = workload_repository.get(workload_id)
        plan = BenchmarkPlan(
            name=create_body.name,
            status=BenchmarkPlanStatus.READY if preflight.valid else BenchmarkPlanStatus.BLOCKED,
            machine_profile_id=machine_id,
            engine_profile_id=engine_id,
            model_artifact_id=model_id,
            workload_id=workload_id,
            warmup_requests=workload.warmup_count if workload is not None else 0,
            measured_requests=workload.request_count if workload is not None else 1,
            measurement_duration_seconds=(
                workload.duration_seconds if workload is not None else None
            ),
            concurrency=(
                max(workload.concurrency_levels)
                if workload and workload.concurrency_levels
                else workload.concurrency
                if workload
                else 1
            ),
            timeout_seconds=timeout_seconds,
            retain_prompt_output=create_body.retain_prompt_output,
            dataset_snapshot_hash=snapshot_hash,
            configuration_snapshot=cast(dict[str, JsonValue], configuration_snapshot),
            targets=source_plan.targets,
        )
        RecordRepository(connection, BenchmarkPlan).create(plan)
    return BenchmarkPlanCloneResponse(plan=plan, preflight=preflight, changed_fields=changed_fields)


@router.get("/benchmark-plans", response_model=list[BenchmarkPlan], tags=["benchmark-plans"])
def list_benchmark_plans(connection: DatabaseConnection) -> list[BenchmarkPlan]:
    return RecordRepository(connection, BenchmarkPlan).list(limit=1000)


@router.get("/benchmark-plans/{plan_id}/export", tags=["benchmark-plans"])
def export_benchmark_plan(
    plan_id: UUID,
    request: Request,
    connection: DatabaseConnection,
) -> Response:
    plan = RecordRepository(connection, BenchmarkPlan).get(plan_id)
    if plan is None:
        raise _http_error(404, "benchmark_plan_not_found", "The benchmark plan was not found.")
    workload_snapshot = plan.configuration_snapshot.get("workload")
    exported_workload: Workload | None = None
    if isinstance(workload_snapshot, dict):
        exported_workload = Workload.model_validate(workload_snapshot)
    else:
        exported_workload = RecordRepository(connection, Workload).get(plan.workload_id)
    if exported_workload is None:
        raise _http_error(
            409, "benchmark_workload_missing", "The plan workload snapshot is unavailable."
        )
    bundle = BenchmarkPlanExport(
        app_version=__version__,
        database_schema_version=cast(int, request.app.state.database_schema_version),
        exported_at=datetime.now(UTC),
        workload=exported_workload,
        plan=BenchmarkPlanSettings(
            name=plan.name,
            engine_profile_id=plan.engine_profile_id,
            machine_profile_id=plan.machine_profile_id,
            model_artifact_id=plan.model_artifact_id,
            warmup_requests=plan.warmup_requests,
            measured_requests=plan.measured_requests,
            measurement_duration_seconds=plan.measurement_duration_seconds,
            concurrency=plan.concurrency,
            timeout_seconds=plan.timeout_seconds,
            retain_prompt_output=plan.retain_prompt_output,
            dataset_snapshot_hash=plan.dataset_snapshot_hash,
            targets=plan.targets,
        ),
    )
    content = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="localforge-plan-{plan.id}.json"'},
    )


@router.post(
    "/benchmark-plans/import",
    response_model=BenchmarkPlanResponse,
    status_code=201,
    tags=["benchmark-plans"],
)
def import_benchmark_plan(
    bundle: BenchmarkPlanExport,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> BenchmarkPlanResponse:
    plan_settings = bundle.plan
    if plan_settings.dataset_snapshot_hash is not None:
        dataset_id = bundle.workload.dataset_id
        dataset = RecordRepository(connection, Dataset).get(dataset_id) if dataset_id else None
        if dataset is None or dataset.snapshot_hash != plan_settings.dataset_snapshot_hash:
            raise _http_error(
                409,
                "plan_dataset_unavailable",
                "Import requires the same retained dataset snapshot to exist in this "
                "LOCALFORGE workspace.",
            )
    workload = Workload.model_validate(
        bundle.workload.model_dump(mode="python")
        | {
            "id": uuid4(),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "status": WorkloadStatus.READY,
        }
    )
    create_body = BenchmarkPlanCreate(
        name=plan_settings.name,
        engine_profile_id=plan_settings.engine_profile_id,
        machine_profile_id=plan_settings.machine_profile_id,
        model_artifact_id=plan_settings.model_artifact_id,
        workload_id=workload.id,
        retain_prompt_output=plan_settings.retain_prompt_output,
    )
    connection.execute("SAVEPOINT localforge_plan_import_preflight")
    try:
        RecordRepository(connection, Workload).create(workload)
        preflight = _preflight(
            create_body,
            connection=connection,
            data_directory=cast(Path, request.app.state.data_directory),
            catalog=catalog,
            expected_snapshot_hash=plan_settings.dataset_snapshot_hash,
        )
    finally:
        connection.execute("ROLLBACK TO localforge_plan_import_preflight")
        connection.execute("RELEASE localforge_plan_import_preflight")
    dataset = (
        RecordRepository(connection, Dataset).get(workload.dataset_id)
        if workload.dataset_id is not None
        else None
    )
    configuration_snapshot: dict[str, object] = {
        "workload": workload.model_dump(mode="json"),
        "dataset_snapshot_hash": dataset.snapshot_hash if dataset is not None else None,
        "dataset_source_hash": dataset.content_hash if dataset is not None else None,
        "prompt_output_retention": plan_settings.retain_prompt_output,
        "targets": _targets_json(plan_settings.targets),
    }
    imported_plan = BenchmarkPlan(
        name=plan_settings.name,
        status=BenchmarkPlanStatus.READY if preflight.valid else BenchmarkPlanStatus.BLOCKED,
        machine_profile_id=plan_settings.machine_profile_id,
        engine_profile_id=plan_settings.engine_profile_id,
        model_artifact_id=plan_settings.model_artifact_id,
        workload_id=workload.id,
        warmup_requests=plan_settings.warmup_requests,
        measured_requests=plan_settings.measured_requests,
        measurement_duration_seconds=plan_settings.measurement_duration_seconds,
        concurrency=plan_settings.concurrency,
        timeout_seconds=plan_settings.timeout_seconds,
        retain_prompt_output=plan_settings.retain_prompt_output,
        dataset_snapshot_hash=dataset.snapshot_hash if dataset is not None else None,
        configuration_snapshot=cast(dict[str, JsonValue], configuration_snapshot),
        targets=plan_settings.targets,
    )
    with transaction(connection):
        RecordRepository(connection, Workload).create(workload)
        RecordRepository(connection, BenchmarkPlan).create(imported_plan)
    return BenchmarkPlanResponse(plan=imported_plan, preflight=preflight)


@router.post(
    "/benchmark-plans/{plan_id}/preflight",
    response_model=BenchmarkPreflight,
    tags=["benchmark-plans"],
)
def preflight_saved_plan(
    plan_id: UUID,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> BenchmarkPreflight:
    plan = RecordRepository(connection, BenchmarkPlan).get(plan_id)
    if plan is None:
        raise _http_error(404, "benchmark_plan_not_found", "The benchmark plan was not found.")
    preflight = _preflight(
        BenchmarkPlanCreate(
            name=plan.name,
            engine_profile_id=plan.engine_profile_id,
            machine_profile_id=plan.machine_profile_id,
            model_artifact_id=plan.model_artifact_id,
            workload_id=plan.workload_id,
            retain_prompt_output=plan.retain_prompt_output,
        ),
        connection=connection,
        data_directory=cast(Path, request.app.state.data_directory),
        catalog=catalog,
        expected_snapshot_hash=plan.dataset_snapshot_hash,
    )
    desired_status = BenchmarkPlanStatus.READY if preflight.valid else BenchmarkPlanStatus.BLOCKED
    if plan.status is not desired_status:
        plan = BenchmarkPlan.model_validate(
            plan.model_dump(mode="python") | {"status": desired_status}
        )
        RecordRepository(connection, BenchmarkPlan).update(plan)
    return preflight
