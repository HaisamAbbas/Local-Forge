"""Persisted experiment sweeps, run comparison, and private report exports."""

import csv
import html
import io
import json
import os
import re
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from localforge import __version__
from localforge.api.dependencies import DatabaseConnection, LocalAdapterCatalog
from localforge.api.runs import start_run
from localforge.api.schemas import (
    BenchmarkPlanCreate,
    BenchmarkPreflight,
    ExperimentGroupCreate,
    ExperimentGroupCreateResponse,
    ExperimentNextTrialResponse,
    ExperimentStartRequest,
    ReportCreate,
    ReportCreateResponse,
    RunComparisonRequest,
    RunStartRequest,
)
from localforge.api.workloads import _preflight
from localforge.comparisons import (
    _redact_private_fields,
    _redact_sensitive_text,
    build_experiment_summary,
    build_report_document,
    build_run_comparison,
)
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkPlanStatus,
    BenchmarkRun,
    BenchmarkRunStatus,
    Dataset,
    ExperimentGroup,
    ExperimentGroupStatus,
    ExperimentTrial,
    ExperimentTrialStatus,
    ExperimentVariant,
    JsonValue,
    ReportFormat,
    ReportMetadata,
    ReportStatus,
    Workload,
    WorkloadPreset,
)
from localforge.storage.database import transaction
from localforge.storage.repositories import RecordRepository

router = APIRouter(prefix="/api/v1")
MAX_EXPERIMENT_RUNS = 20
RUNTIME_CONFIRM_SECONDS = 300.0
MAX_REPORT_BYTES = 25 * 1024 * 1024
_SWEEP_VARIABLES = {
    "concurrency",
    "max_output_tokens",
    "arrival_rate_per_second",
    "sampling_config.temperature",
    "sampling_config.top_p",
    "sampling_config.top_k",
    "sampling_config.seed",
}
_TERMINAL_RUN_STATUSES = {
    BenchmarkRunStatus.COMPLETED,
    BenchmarkRunStatus.FAILED,
    BenchmarkRunStatus.CANCELLED,
    BenchmarkRunStatus.INTERRUPTED,
}


def _error(status: int, code: str, message: str, **details: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **details})


def _group_or_404(connection: sqlite3.Connection, group_id: UUID) -> ExperimentGroup:
    group = RecordRepository(connection, ExperimentGroup).get(group_id)
    if group is None:
        raise _error(404, "experiment_not_found", "The experiment group was not found.")
    return group


def _require_terminal_runs(connection: sqlite3.Connection, run_ids: list[UUID]) -> None:
    repository = RecordRepository(connection, BenchmarkRun)
    for run_id in run_ids:
        run = repository.get(run_id)
        if run is None:
            raise _error(404, "run_not_found", "One or more selected runs were not found.")
        if run.status not in _TERMINAL_RUN_STATUSES:
            raise _error(
                409,
                "run_not_terminal",
                "Wait for every selected run to finish before comparing measurements.",
                run_id=str(run_id),
                run_status=run.status.value,
            )


def _refresh_group(connection: sqlite3.Connection, group: ExperimentGroup) -> ExperimentGroup:
    updated_trials: list[ExperimentTrial] = []
    now = datetime.now(UTC)
    for trial in group.trials:
        if trial.status is ExperimentTrialStatus.LAUNCHING and trial.run_id is None:
            if trial.launch_started_at is not None and trial.launch_started_at < now - timedelta(
                seconds=60
            ):
                updated_trials.append(
                    trial.model_copy(
                        update={
                            "status": ExperimentTrialStatus.PENDING,
                            "launch_started_at": None,
                            "error": "A previous launch attempt ended before a run was recorded.",
                        }
                    )
                )
                continue
        if trial.run_id is None:
            updated_trials.append(trial)
            continue
        run = RecordRepository(connection, BenchmarkRun).get(trial.run_id)
        if run is None:
            updated_trials.append(
                trial.model_copy(
                    update={
                        "status": ExperimentTrialStatus.FAILED,
                        "error": "The associated benchmark run no longer exists.",
                    }
                )
            )
        elif run.status is BenchmarkRunStatus.COMPLETED:
            updated_trials.append(
                trial.model_copy(update={"status": ExperimentTrialStatus.SUCCEEDED, "error": None})
            )
        elif run.status in _TERMINAL_RUN_STATUSES:
            updated_trials.append(
                trial.model_copy(
                    update={
                        "status": ExperimentTrialStatus.FAILED,
                        "error": _redact_sensitive_text(
                            run.error_message or f"Run ended with status {run.status.value}."
                        )[:500],
                    }
                )
            )
        else:
            updated_trials.append(
                trial.model_copy(update={"status": ExperimentTrialStatus.RUNNING})
            )

    all_terminal = bool(updated_trials) and all(
        trial.status in {ExperimentTrialStatus.SUCCEEDED, ExperimentTrialStatus.FAILED}
        for trial in updated_trials
    )
    status = group.status
    if group.status is ExperimentGroupStatus.RUNNING and all_terminal:
        status = (
            ExperimentGroupStatus.COMPLETED
            if all(trial.status is ExperimentTrialStatus.SUCCEEDED for trial in updated_trials)
            else ExperimentGroupStatus.FAILED
        )
    changed = status != group.status or updated_trials != group.trials
    if not changed:
        return group
    refreshed = ExperimentGroup.model_validate(
        group.model_dump(mode="python") | {"status": status, "trials": updated_trials}
    )
    return RecordRepository(connection, ExperimentGroup).update(refreshed)


def _resolve_workload_value(workload: Workload, path: str) -> JsonValue:
    if path.startswith("sampling_config."):
        return workload.sampling_config.get(path.split(".", 1)[1])
    return cast(JsonValue, getattr(workload, path))


def _json_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _apply_workload_value(workload: Workload, path: str, value: JsonValue) -> Workload:
    values = workload.model_dump(mode="python")
    if path.startswith("sampling_config."):
        key = path.split(".", 1)[1]
        sampling = dict(workload.sampling_config)
        if value is None:
            sampling.pop(key, None)
        else:
            sampling[key] = value
        values["sampling_config"] = sampling
    else:
        values[path] = value
    now = datetime.now(UTC)
    values.update({"id": uuid4(), "created_at": now, "updated_at": now})
    values["name"] = f"{workload.name} — {path}={value}"
    try:
        return Workload.model_validate(values)
    except ValidationError as error:
        raise _error(
            422, "sweep_value_invalid", "A variant value is invalid for this workload."
        ) from error


def _safe_export_filename(name: str, file_format: ReportFormat, report_id: UUID) -> str:
    if any(character in name for character in ("/", "\\", "\x00")) or ".." in name:
        raise ValueError("filename cannot contain path separators or traversal segments")
    if PurePath(name).is_absolute() or Path(name).name != name:
        raise ValueError("filename must be a local file name")
    suffix = Path(name).suffix.lower()
    if suffix and suffix != f".{file_format.value}":
        raise ValueError(f"filename extension must match {file_format.value}")
    stem = name[: -len(suffix)] if suffix else name
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_.")
    if not slug:
        slug = "localforge-report"
    return f"{slug[:100]}-{str(report_id)[:8]}.{file_format.value}"


def _report_directory(request: Request) -> tuple[Path, Path]:
    data_directory = Path(request.app.state.data_directory).resolve()
    report_path = data_directory / "reports"
    if report_path.is_symlink() or report_path.is_junction():
        raise _error(422, "invalid_export_path", "The reports directory cannot be a link.")
    reports_directory = report_path.resolve()
    if not reports_directory.is_relative_to(data_directory):
        raise _error(422, "invalid_export_path", "The reports directory escaped local storage.")
    return data_directory, reports_directory


def _markdown_cell(value: object) -> str:
    text = html.escape(str(value if value is not None else ""), quote=True)
    text = re.sub(r"([\\`*_{}\[\]()#+\-.!|])", r"\\\1", text)
    return text.replace("\r", " ").replace("\n", " ")


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _markdown_report(document: dict[str, Any], name: str) -> str:
    privacy = document.get("privacy")
    privacy = privacy if isinstance(privacy, dict) else {}
    lines = [
        f"# {_markdown_cell(name)}",
        "",
        f"Generated: {_markdown_cell(document.get('generated_at'))}",
        f"LOCALFORGE: {_markdown_cell(document.get('app_version'))} · "
        f"database schema {_markdown_cell(document.get('database_schema_version'))}",
        f"Credentials included: {_markdown_cell(privacy.get('credentials_included', False))}",
        f"Request text included: {_markdown_cell(document.get('content_included', False))}",
        "",
    ]
    comparison = document.get("comparability")
    if isinstance(comparison, dict):
        lines.extend(
            [
                "## Comparability",
                "",
                f"**{comparison.get('label')}** — {comparison.get('rationale')}",
                f"Controlled comparison badge: {comparison.get('controlled_comparison_badge')}",
                "",
                "| Dimension | State | Captured values |",
                "|---|---|---|",
            ]
        )
        dimensions = comparison.get("dimensions")
        if isinstance(dimensions, list):
            for item in dimensions:
                if isinstance(item, dict):
                    values = json.dumps(item.get("values"), ensure_ascii=False)
                    label = _markdown_cell(item.get("label"))
                    state = _markdown_cell(item.get("state"))
                    lines.append(f"| {label} | {state} | {_markdown_cell(values)} |")
        lines.append("")
    experiment_group = _mapping(document.get("experiment_group"))
    if experiment_group:
        lines.extend(
            [
                "## Experiment group",
                "",
                f"- Name: {_markdown_cell(experiment_group.get('name'))}",
                f"- Status: {_markdown_cell(experiment_group.get('status'))}",
                f"- Baseline plan: {_markdown_cell(experiment_group.get('baseline_plan_id'))}",
                f"- Independent variable: "
                f"{_markdown_cell(experiment_group.get('independent_variable'))}",
                f"- Baseline value: {_markdown_cell(experiment_group.get('baseline_value'))}",
                f"- Repetitions: {_markdown_cell(experiment_group.get('repetitions'))}",
                f"- Randomized order: {_markdown_cell(experiment_group.get('randomize_order'))}",
                f"- Order seed: {_markdown_cell(experiment_group.get('order_seed'))}",
                f"- Hypothesis: {_markdown_cell(experiment_group.get('hypothesis') or 'None')}",
                "",
            ]
        )
        variants = experiment_group.get("variants")
        variant_rows = [{"name": "Baseline", "value": experiment_group.get("baseline_value")}]
        if isinstance(variants, list):
            variant_rows.extend(item for item in variants if isinstance(item, dict))
        lines.extend(["| Variant | Value |", "|---|---:|"])
        for variant in variant_rows:
            lines.append(
                f"| {_markdown_cell(variant.get('name'))} | "
                f"{_markdown_cell(json.dumps(variant.get('value'), ensure_ascii=False))} |"
            )
        trials = experiment_group.get("trials")
        if isinstance(trials, list):
            lines.extend(
                [
                    "",
                    "### Trials",
                    "",
                    "| Order | Variant | Value | Repeat | Status | Run | Error |",
                    "|---:|---|---:|---:|---|---|---|",
                ]
            )
            for trial in trials:
                if isinstance(trial, dict):
                    lines.append(
                        (
                            "| {order} | {variant} | {value} | {repeat} | "
                            "{status} | {run} | {error} |"
                        ).format(
                            order=_markdown_cell(trial.get("order")),
                            variant=_markdown_cell(trial.get("variant_name")),
                            value=_markdown_cell(
                                json.dumps(trial.get("variant_value"), ensure_ascii=False)
                            ),
                            repeat=_markdown_cell(trial.get("repetition")),
                            status=_markdown_cell(trial.get("status")),
                            run=_markdown_cell(trial.get("run_id")),
                            error=_markdown_cell(trial.get("error") or "None"),
                        )
                    )
        lines.append("")
    runs = document.get("runs")
    if not isinstance(runs, list):
        return "\n".join(lines)
    lines.extend(["## Runs", ""])
    for item in runs:
        if not isinstance(item, dict):
            continue
        run = _mapping(item.get("run"))
        plan = _mapping(item.get("plan"))
        snapshot = _mapping(run.get("profile_snapshot"))
        engine = _mapping(snapshot.get("engine"))
        artifact = _mapping(snapshot.get("model_artifact"))
        machine = _mapping(snapshot.get("machine"))
        workload_json = json.dumps(item.get("workload"), ensure_ascii=False)
        lines.extend(
            [
                f"### Run {_markdown_cell(run.get('id'))}",
                "",
                f"- Status: {_markdown_cell(run.get('status'))}",
                f"- Engine: {_markdown_cell(json.dumps(engine, ensure_ascii=False))}",
                f"- Model artifact: {_markdown_cell(json.dumps(artifact, ensure_ascii=False))}",
                f"- Hardware: {_markdown_cell(json.dumps(machine, ensure_ascii=False))}",
                f"- Plan: {_markdown_cell(json.dumps(plan, ensure_ascii=False))}",
                f"- Workload snapshot: {_markdown_cell(workload_json)}",
                f"- Error: {_markdown_cell(run.get('error_message') or 'None')}",
            ]
        )
        warnings = item.get("warnings")
        if isinstance(warnings, list):
            lines.extend(
                f"- Warning: {_markdown_cell(warning)}"
                for warning in warnings
                if isinstance(warning, str)
            )
        lines.append("")
        metric_values = item.get("metrics")
        if isinstance(metric_values, list):
            lines.extend(
                [
                    "| Metric | Value | Unit | Source | Samples | Formula |",
                    "|---|---:|---|---|---:|---|",
                ]
            )
            for metric in metric_values:
                if isinstance(metric, dict):
                    lines.append(
                        "| {name} | {value} | {unit} | {source} | {count} | `{formula}` |".format(
                            name=_markdown_cell(metric.get("metric_name")),
                            value=_markdown_cell(
                                metric.get("value")
                                if metric.get("value") is not None
                                else "Unavailable"
                            ),
                            unit=_markdown_cell(metric.get("unit")),
                            source=_markdown_cell(metric.get("source")),
                            count=_markdown_cell(metric.get("sample_count")),
                            formula=_markdown_cell(metric.get("formula")),
                        )
                    )
            lines.append("")
        content = item.get("request_content")
        if isinstance(content, list) and content:
            lines.extend(["#### Retained request text", ""])
            for request in content:
                if not isinstance(request, dict):
                    continue
                prompt = request.get("prompt_text")
                output = request.get("output_text")
                sequence = _markdown_cell(request.get("sequence_number"))
                phase = _markdown_cell(request.get("phase"))
                outcome = _markdown_cell(request.get("outcome"))
                lines.extend(
                    [
                        f"Request {sequence} ({phase}, {outcome})",
                        "",
                    ]
                )
                for label, value in (("Prompt", prompt), ("Output", output)):
                    if value is None:
                        continue
                    text = str(value)
                    fence = "`" * (
                        max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
                        + 1
                    )
                    lines.extend([f"{label}:", "", f"{fence}text", text, fence, ""])
    comparison = document.get("comparability")
    if isinstance(comparison, dict):
        lines.extend(
            [
                "## Shared metrics",
                "",
                "Only matching units, sources, formulas, and aggregation methods are shown.",
                "",
            ]
        )
        shared = comparison.get("shared_metrics")
        if isinstance(shared, list):
            lines.extend(["| Metric | Unit | Source | Run values |", "|---|---|---|---|"])
            for metric in shared:
                if isinstance(metric, dict):
                    run_values = json.dumps(metric.get("runs"), ensure_ascii=False)
                    lines.append(
                        "| {} | {} | {} | {} |".format(
                            _markdown_cell(metric.get("metric_name")),
                            _markdown_cell(metric.get("unit")),
                            _markdown_cell(metric.get("source")),
                            _markdown_cell(run_values),
                        )
                    )
    return "\n".join(lines) + "\n"


def _csv_report(document: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "record_type",
            "run_id",
            "status",
            "engine_version",
            "adapter_id",
            "model_artifact_json",
            "hardware_json",
            "workload_plan_json",
            "metric_name",
            "value",
            "unit",
            "source",
            "aggregation",
            "sample_count",
            "definition",
            "formula",
            "method_version",
            "warning_or_error",
            "prompt_text",
            "output_text",
            "report_version",
            "app_version",
            "database_schema_version",
            "generated_at",
            "comparability_grade",
            "comparability_label",
            "comparability_rationale",
            "dimension_key",
            "dimension_state",
            "dimension_values_json",
            "comparison_values_json",
            "observed_deltas_json",
            "experiment_group_json",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    comparison = _mapping(document.get("comparability"))
    comparison_context = {
        "comparability_grade": comparison.get("grade"),
        "comparability_label": comparison.get("label"),
        "comparability_rationale": comparison.get("rationale"),
    }
    writer.writerow(
        {
            "record_type": "report",
            "report_version": document.get("report_version"),
            "app_version": document.get("app_version"),
            "database_schema_version": document.get("database_schema_version"),
            "generated_at": document.get("generated_at"),
            "experiment_group_json": json.dumps(
                document.get("experiment_group"), ensure_ascii=False, sort_keys=True
            ),
            **comparison_context,
        }
    )
    dimensions = comparison.get("dimensions")
    if isinstance(dimensions, list):
        for item in dimensions:
            if isinstance(item, dict):
                writer.writerow(
                    {
                        "record_type": "comparison_dimension",
                        "dimension_key": item.get("key"),
                        "dimension_state": item.get("state"),
                        "dimension_values_json": json.dumps(
                            item.get("changes", item.get("values")),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        **comparison_context,
                    }
                )
    shared = comparison.get("shared_metrics")
    if isinstance(shared, list):
        for metric in shared:
            if isinstance(metric, dict):
                writer.writerow(
                    {
                        "record_type": "shared_metric",
                        "metric_name": metric.get("metric_name"),
                        "unit": metric.get("unit"),
                        "source": metric.get("source"),
                        "aggregation": metric.get("aggregation"),
                        "sample_count": metric.get("sample_count"),
                        "definition": metric.get("definition"),
                        "formula": metric.get("formula"),
                        "method_version": metric.get("method_version"),
                        "comparison_values_json": json.dumps(
                            metric.get("runs"), ensure_ascii=False, sort_keys=True
                        ),
                        "observed_deltas_json": json.dumps(
                            metric.get("observed_deltas"), ensure_ascii=False, sort_keys=True
                        ),
                        **comparison_context,
                    }
                )
    runs = document.get("runs")
    if not isinstance(runs, list):
        return output.getvalue()
    for item in runs:
        if not isinstance(item, dict):
            continue
        run = _mapping(item.get("run"))
        snapshot = _mapping(run.get("profile_snapshot"))
        engine = _mapping(snapshot.get("engine"))
        raw_warnings = item.get("warnings")
        warnings = raw_warnings if isinstance(raw_warnings, list) else []
        row_base = {
            **comparison_context,
            "run_id": run.get("id"),
            "status": run.get("status"),
            "engine_version": engine.get("engine_version"),
            "adapter_id": engine.get("adapter_id"),
            "model_artifact_json": json.dumps(snapshot.get("model_artifact"), ensure_ascii=False),
            "hardware_json": json.dumps(snapshot.get("machine"), ensure_ascii=False),
            "workload_plan_json": json.dumps(
                {"workload": item.get("workload"), "plan": item.get("plan")},
                ensure_ascii=False,
            ),
            "warning_or_error": "; ".join(
                [f"Warning: {warning}" for warning in warnings if isinstance(warning, str)]
                + ([f"Error: {run['error_message']}"] if run.get("error_message") else [])
            ),
        }
        writer.writerow({"record_type": "run", **row_base})
        metrics = item.get("metrics")
        if isinstance(metrics, list):
            for metric in metrics:
                if isinstance(metric, dict):
                    writer.writerow(
                        {
                            "record_type": "metric",
                            **row_base,
                            "metric_name": metric.get("metric_name"),
                            "value": metric.get("value"),
                            "unit": metric.get("unit"),
                            "source": metric.get("source"),
                            "aggregation": metric.get("aggregation"),
                            "sample_count": metric.get("sample_count"),
                            "definition": metric.get("definition"),
                            "formula": metric.get("formula"),
                            "method_version": metric.get("method_version"),
                        }
                    )
        content = item.get("request_content")
        if isinstance(content, list):
            for sample in content:
                if isinstance(sample, dict):
                    writer.writerow(
                        {
                            "record_type": "request_content",
                            **row_base,
                            "warning_or_error": (
                                f"sequence={sample.get('sequence_number')}; "
                                f"phase={sample.get('phase')}; "
                                f"outcome={sample.get('outcome')}"
                            ),
                            "prompt_text": sample.get("prompt_text"),
                            "output_text": sample.get("output_text"),
                        }
                    )
    return output.getvalue()


def _serialize_report(document: dict[str, Any], file_format: ReportFormat, name: str) -> bytes:
    if file_format is ReportFormat.JSON:
        text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    elif file_format is ReportFormat.CSV:
        text = _csv_report(document)
    else:
        text = _markdown_report(document, name)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("The report exceeds the 25 MB export limit.")
    return encoded


@router.get("/experiments", response_model=list[ExperimentGroup], tags=["experiments"])
def list_experiments(connection: DatabaseConnection) -> list[ExperimentGroup]:
    repository = RecordRepository(connection, ExperimentGroup)
    return [_refresh_group(connection, group) for group in repository.list(limit=1000)]


@router.get("/experiments/{group_id}", response_model=ExperimentGroup, tags=["experiments"])
def get_experiment(group_id: UUID, connection: DatabaseConnection) -> ExperimentGroup:
    return _refresh_group(connection, _group_or_404(connection, group_id))


@router.post(
    "/experiments",
    response_model=ExperimentGroupCreateResponse,
    status_code=201,
    tags=["experiments"],
)
def create_experiment(
    body: ExperimentGroupCreate,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> ExperimentGroupCreateResponse:
    if body.independent_variable not in _SWEEP_VARIABLES:
        raise _error(
            422,
            "unsupported_sweep_variable",
            "Choose a supported workload setting for the independent variable.",
        )
    run_count = (len(body.variants) + 1) * body.repetitions
    if run_count > MAX_EXPERIMENT_RUNS:
        raise _error(
            422,
            "experiment_run_limit_exceeded",
            f"An experiment can contain at most {MAX_EXPERIMENT_RUNS} trials.",
        )
    if len({variant.name.casefold() for variant in body.variants}) != len(body.variants):
        raise _error(422, "duplicate_variant_name", "Variant names must be unique.")
    if any(variant.name.casefold() == "baseline" for variant in body.variants):
        raise _error(422, "reserved_variant_name", "The name 'Baseline' is reserved.")
    baseline_plan = RecordRepository(connection, BenchmarkPlan).get(body.baseline_plan_id)
    if baseline_plan is None or baseline_plan.status is not BenchmarkPlanStatus.READY:
        raise _error(409, "baseline_plan_unavailable", "Choose a ready baseline benchmark plan.")
    raw_workload = baseline_plan.configuration_snapshot.get("workload")
    baseline_workload: Workload | None = None
    if isinstance(raw_workload, dict):
        baseline_workload = Workload.model_validate(raw_workload)
    else:
        baseline_workload = RecordRepository(connection, Workload).get(baseline_plan.workload_id)
    if baseline_workload is None:
        raise _error(
            409, "baseline_workload_missing", "The baseline workload snapshot is unavailable."
        )
    if (
        body.independent_variable == "concurrency"
        and baseline_workload.preset is WorkloadPreset.CONCURRENCY_SWEEP
    ):
        raise _error(
            422,
            "sweep_variable_conflict",
            "A concurrency-sweep workload cannot also vary its concurrency setting.",
        )
    if (
        body.independent_variable == "arrival_rate_per_second"
        and baseline_workload.traffic_mode.value != "open_loop"
    ):
        raise _error(
            422,
            "sweep_variable_conflict",
            "Arrival rate can vary only in an open-loop workload.",
        )
    baseline_value = _resolve_workload_value(baseline_workload, body.independent_variable)
    values = [baseline_value, *(variant.value for variant in body.variants)]
    if any(value is None for value in values):
        raise _error(
            422, "sweep_value_missing", "Every baseline and variant needs a concrete value."
        )
    if len({_json_key(value) for value in values}) != len(values):
        raise _error(
            422,
            "duplicate_sweep_value",
            "Each variant must use a distinct value from the baseline and other variants.",
        )

    plan_repository = RecordRepository(connection, BenchmarkPlan)
    workload_repository = RecordRepository(connection, Workload)
    datasets = RecordRepository(connection, Dataset)
    if baseline_workload.dataset_id is not None:
        dataset = datasets.get(baseline_workload.dataset_id)
        if dataset is None or dataset.snapshot_hash != baseline_plan.dataset_snapshot_hash:
            raise _error(
                409,
                "dataset_snapshot_changed",
                "The baseline dataset snapshot is no longer available.",
            )

    variant_specs = [
        ExperimentVariant(name="Baseline", value=baseline_value),
        *[ExperimentVariant(name=item.name, value=item.value) for item in body.variants],
    ]
    descriptors = [
        (variant, repetition)
        for variant in variant_specs
        for repetition in range(1, body.repetitions + 1)
    ]
    if body.randomize_order:
        import random

        random.Random(body.order_seed).shuffle(descriptors)

    data_directory = Path(request.app.state.data_directory)
    engine_profile_id = baseline_plan.engine_profile_id
    estimated_duration = 0.0
    estimates_known = True
    trial_workloads: list[tuple[ExperimentVariant, int, Workload, BenchmarkPlanCreate]] = []
    for variant, repetition in descriptors:
        if variant.name == "Baseline":
            now = datetime.now(UTC)
            workload = baseline_workload.model_copy(
                deep=True,
                update={"id": uuid4(), "created_at": now, "updated_at": now},
            )
        else:
            workload = _apply_workload_value(
                baseline_workload, body.independent_variable, variant.value
            )
        plan_request = BenchmarkPlanCreate(
            name=f"{body.name} — {variant.name} — repeat {repetition}"[:160],
            engine_profile_id=engine_profile_id,
            machine_profile_id=baseline_plan.machine_profile_id,
            model_artifact_id=baseline_plan.model_artifact_id,
            workload_id=workload.id,
            retain_prompt_output=baseline_plan.retain_prompt_output,
        )
        trial_workloads.append((variant, repetition, workload, plan_request))

    preflights: list[BenchmarkPreflight] = []
    connection.execute("SAVEPOINT localforge_experiment_preflight")
    try:
        for variant, _repetition, workload, plan_request in trial_workloads:
            workload_repository.create(workload)
            preflight = _preflight(
                plan_request,
                connection=connection,
                data_directory=data_directory,
                catalog=catalog,
            )
            if not preflight.valid:
                first_blocker = (
                    preflight.blockers[0].message
                    if preflight.blockers
                    else "Variant preflight failed."
                )
                raise _error(
                    422,
                    "experiment_variant_invalid",
                    f"Variant '{variant.name}' is not runnable: {first_blocker}",
                    variant=variant.name,
                    blockers=[item.model_dump(mode="json") for item in preflight.blockers],
                )
            preflights.append(preflight)
    finally:
        connection.execute("ROLLBACK TO localforge_experiment_preflight")
        connection.execute("RELEASE localforge_experiment_preflight")

    materialized: list[tuple[Workload, BenchmarkPlan, ExperimentTrial]] = []
    for order, ((variant, repetition, workload, body_plan), preflight) in enumerate(
        zip(trial_workloads, preflights, strict=True)
    ):
        if preflight.estimated_duration_seconds is None:
            estimates_known = False
        else:
            estimated_duration += preflight.estimated_duration_seconds
        dataset = datasets.get(workload.dataset_id) if workload.dataset_id is not None else None
        snapshot_hash = dataset.snapshot_hash if dataset is not None else None
        config_snapshot: dict[str, object] = {
            "workload": workload.model_dump(mode="json"),
            "dataset_snapshot_hash": snapshot_hash,
            "dataset_source_hash": dataset.content_hash if dataset is not None else None,
            "prompt_output_retention": baseline_plan.retain_prompt_output,
        }
        new_plan = BenchmarkPlan(
            name=body_plan.name,
            status=BenchmarkPlanStatus.READY,
            machine_profile_id=baseline_plan.machine_profile_id,
            engine_profile_id=engine_profile_id,
            model_artifact_id=baseline_plan.model_artifact_id,
            workload_id=workload.id,
            warmup_requests=workload.warmup_count,
            measured_requests=workload.request_count,
            measurement_duration_seconds=workload.duration_seconds,
            concurrency=(
                max(workload.concurrency_levels)
                if workload.concurrency_levels
                else workload.concurrency
            ),
            timeout_seconds=baseline_plan.timeout_seconds,
            retain_prompt_output=baseline_plan.retain_prompt_output,
            dataset_snapshot_hash=snapshot_hash,
            configuration_snapshot=cast(dict[str, JsonValue], config_snapshot),
        )
        trial = ExperimentTrial(
            order=order,
            variant_name=variant.name,
            variant_value=variant.value,
            repetition=repetition,
            plan_id=new_plan.id,
        )
        materialized.append((workload, new_plan, trial))

    group = ExperimentGroup(
        name=body.name.strip(),
        description=body.hypothesis.strip() if body.hypothesis else None,
        baseline_plan_id=baseline_plan.id,
        independent_variable=body.independent_variable,
        baseline_value=baseline_value,
        variants=[item for item in variant_specs if item.name != "Baseline"],
        repetitions=body.repetitions,
        randomize_order=body.randomize_order,
        order_seed=body.order_seed,
        estimated_duration_seconds=estimated_duration if estimates_known else None,
        trials=[trial for _workload, _plan, trial in materialized],
    )
    with transaction(connection):
        for workload, plan, _trial in materialized:
            workload_repository.create(workload)
            plan_repository.create(plan)
        RecordRepository(connection, ExperimentGroup).create(group)
    return ExperimentGroupCreateResponse(
        group=group,
        estimated_run_count=run_count,
        estimated_duration_seconds=estimated_duration if estimates_known else None,
    )


@router.post(
    "/experiments/{group_id}/start",
    response_model=ExperimentGroup,
    tags=["experiments"],
)
def start_experiment(
    group_id: UUID,
    body: ExperimentStartRequest,
    connection: DatabaseConnection,
) -> ExperimentGroup:
    group = _refresh_group(connection, _group_or_404(connection, group_id))
    if group.status is not ExperimentGroupStatus.DRAFT:
        raise _error(409, "experiment_not_draft", "Only a draft experiment can be started.")
    if (
        group.estimated_duration_seconds is None
        or group.estimated_duration_seconds > RUNTIME_CONFIRM_SECONDS
    ) and not body.confirm_estimated_runtime:
        raise _error(
            409,
            "experiment_runtime_confirmation_required",
            "Confirm the estimated or unknown total runtime before launching this sweep.",
            estimated_duration_seconds=group.estimated_duration_seconds,
            trial_count=len(group.trials),
        )
    started = ExperimentGroup.model_validate(
        group.model_dump(mode="python") | {"status": ExperimentGroupStatus.RUNNING}
    )
    return RecordRepository(connection, ExperimentGroup).update(started)


@router.post(
    "/experiments/{group_id}/next",
    response_model=ExperimentNextTrialResponse,
    tags=["experiments"],
)
def launch_next_experiment_trial(
    group_id: UUID,
    request: Request,
    connection: DatabaseConnection,
) -> ExperimentNextTrialResponse:
    group = _refresh_group(connection, _group_or_404(connection, group_id))
    if group.status is not ExperimentGroupStatus.RUNNING:
        raise _error(
            409, "experiment_not_running", "Start the experiment before launching a trial."
        )

    with transaction(connection):
        latest = _group_or_404(connection, group_id)
        latest = _refresh_group(connection, latest)
        if latest.status is not ExperimentGroupStatus.RUNNING:
            return ExperimentNextTrialResponse(group=latest)
        active = [
            item
            for item in latest.trials
            if item.status in {ExperimentTrialStatus.RUNNING, ExperimentTrialStatus.LAUNCHING}
        ]
        if active:
            raise _error(
                409, "experiment_trial_in_progress", "Wait for the current trial to finish."
            )
        trial = next(
            (item for item in latest.trials if item.status is ExperimentTrialStatus.PENDING), None
        )
        if trial is None:
            completed = _refresh_group(
                connection,
                ExperimentGroup.model_validate(
                    latest.model_dump(mode="python") | {"trials": latest.trials}
                ),
            )
            return ExperimentNextTrialResponse(group=completed)
        launching = trial.model_copy(
            update={
                "status": ExperimentTrialStatus.LAUNCHING,
                "launch_started_at": datetime.now(UTC),
                "error": None,
            }
        )
        trials = [launching if item.order == trial.order else item for item in latest.trials]
        reserved = ExperimentGroup.model_validate(
            latest.model_dump(mode="python") | {"trials": trials}
        )
        RecordRepository(connection, ExperimentGroup).update(reserved)

    try:
        response = start_run(
            RunStartRequest(plan_id=trial.plan_id),
            request,
            connection,
        )
    except HTTPException as error:
        latest = _group_or_404(connection, group_id)
        failed_run_id: UUID | None = None
        detail: dict[str, Any] = error.detail if isinstance(error.detail, dict) else {}
        if isinstance(detail.get("run_id"), str):
            try:
                failed_run_id = UUID(detail["run_id"])
            except ValueError:
                failed_run_id = None
        candidate = next(item for item in latest.trials if item.order == trial.order)
        retryable = error.status_code == 409 and detail.get("code") == "resource_lock_conflict"
        changed = candidate.model_copy(
            update={
                "status": ExperimentTrialStatus.PENDING
                if retryable
                else ExperimentTrialStatus.FAILED,
                "run_id": failed_run_id,
                "launch_started_at": None,
                "error": _redact_sensitive_text(
                    str(detail.get("message", "The trial could not be launched."))
                )[:500],
            }
        )
        trials = [changed if item.order == trial.order else item for item in latest.trials]
        RecordRepository(connection, ExperimentGroup).update(
            ExperimentGroup.model_validate(latest.model_dump(mode="python") | {"trials": trials})
        )
        raise
    latest = _group_or_404(connection, group_id)
    candidate = next(item for item in latest.trials if item.order == trial.order)
    changed = candidate.model_copy(
        update={
            "run_id": response.run.id,
            "status": ExperimentTrialStatus.RUNNING,
            "launch_started_at": None,
            "error": None,
        }
    )
    trials = [changed if item.order == trial.order else item for item in latest.trials]
    updated = ExperimentGroup.model_validate(
        latest.model_dump(mode="python")
        | {
            "trials": trials,
            "run_ids": list(dict.fromkeys([*latest.run_ids, response.run.id])),
        }
    )
    saved = RecordRepository(connection, ExperimentGroup).update(updated)
    return ExperimentNextTrialResponse(group=saved, run=response.run)


@router.post("/comparisons", tags=["comparisons"])
def compare_runs(
    body: RunComparisonRequest, connection: DatabaseConnection
) -> dict[str, JsonValue]:
    if len(set(body.run_ids)) != len(body.run_ids):
        raise _error(422, "duplicate_run_id", "Select each run only once.")
    _require_terminal_runs(connection, body.run_ids)
    try:
        return build_run_comparison(
            connection,
            body.run_ids,
            declared_variable=body.declared_variable,
            baseline_run_id=body.baseline_run_id,
        )
    except LookupError as error:
        raise _error(404, "run_not_found", str(error)) from error
    except ValueError as error:
        raise _error(422, "comparison_invalid", str(error)) from error


@router.get("/experiments/{group_id}/comparison", tags=["comparisons"])
def compare_experiment_group(
    group_id: UUID,
    connection: DatabaseConnection,
) -> dict[str, JsonValue]:
    group = _refresh_group(connection, _group_or_404(connection, group_id))
    if group.status not in {ExperimentGroupStatus.COMPLETED, ExperimentGroupStatus.FAILED}:
        raise _error(
            409,
            "experiment_comparison_unavailable",
            "Wait for every experiment trial to finish before comparing the group.",
        )
    terminal_trials = [
        trial
        for trial in group.trials
        if trial.run_id is not None
        and trial.status in {ExperimentTrialStatus.SUCCEEDED, ExperimentTrialStatus.FAILED}
    ]
    if len(terminal_trials) < 2:
        raise _error(
            409,
            "experiment_comparison_unavailable",
            "At least two terminal experiment trials are required for comparison.",
        )
    run_ids = [cast(UUID, trial.run_id) for trial in terminal_trials]
    _require_terminal_runs(connection, run_ids)
    baseline = next(
        (trial.run_id for trial in terminal_trials if trial.variant_name == "Baseline"), None
    )
    try:
        return build_run_comparison(
            connection,
            run_ids,
            declared_variable=group.independent_variable,
            baseline_run_id=baseline,
        )
    except LookupError as error:
        raise _error(404, "run_not_found", str(error)) from error
    except ValueError as error:
        raise _error(422, "comparison_invalid", str(error)) from error


@router.get("/experiments/{group_id}/summary", tags=["experiments"])
def summarize_experiment_group(
    group_id: UUID,
    connection: DatabaseConnection,
) -> dict[str, JsonValue]:
    group = _refresh_group(connection, _group_or_404(connection, group_id))
    return build_experiment_summary(connection, group)


@router.get("/reports", response_model=list[ReportMetadata], tags=["reports"])
def list_reports(connection: DatabaseConnection) -> list[ReportMetadata]:
    return RecordRepository(connection, ReportMetadata).list(limit=1000)


@router.post("/reports", response_model=ReportCreateResponse, status_code=201, tags=["reports"])
def create_report(
    body: ReportCreate,
    request: Request,
    connection: DatabaseConnection,
) -> ReportCreateResponse:
    if len(set(body.run_ids)) != len(body.run_ids):
        raise _error(422, "duplicate_run_id", "Select each run only once.")
    _require_terminal_runs(connection, body.run_ids)
    if body.baseline_run_id is not None and body.baseline_run_id not in body.run_ids:
        raise _error(
            422,
            "baseline_run_not_selected",
            "The report baseline must be one of the selected runs.",
        )
    group: ExperimentGroup | None = None
    declared_variable = None
    baseline_run_id = body.baseline_run_id
    if body.experiment_group_id is not None:
        group = _refresh_group(connection, _group_or_404(connection, body.experiment_group_id))
        allowed_run_ids = {trial.run_id for trial in group.trials if trial.run_id is not None}
        if not set(body.run_ids).issubset(allowed_run_ids):
            raise _error(
                422,
                "report_runs_outside_group",
                "Every selected run must belong to this experiment group.",
            )
        declared_variable = group.independent_variable
        if baseline_run_id is None:
            baseline_trial = next(
                (
                    trial
                    for trial in group.trials
                    if trial.variant_name == "Baseline" and trial.run_id in body.run_ids
                ),
                None,
            )
            baseline_run_id = baseline_trial.run_id if baseline_trial is not None else None

    report_id = uuid4()
    try:
        filename = _safe_export_filename(body.filename or body.name, body.format, report_id)
    except ValueError as error:
        raise _error(422, "invalid_export_filename", str(error)) from error
    generated_at = datetime.now(UTC)
    try:
        document = cast(
            dict[str, Any],
            build_report_document(
                connection,
                run_ids=body.run_ids,
                app_version=__version__,
                database_schema_version=cast(int, request.app.state.database_schema_version),
                baseline_run_id=baseline_run_id,
                declared_variable=declared_variable,
                include_retained_content=body.include_retained_content,
            ),
        )
        document["generated_at"] = generated_at.isoformat()
        document["name"] = body.name.strip()
        if group is not None:
            document["experiment_group"] = {
                "id": str(group.id),
                "name": group.name,
                "status": group.status.value,
                "baseline_plan_id": str(group.baseline_plan_id)
                if group.baseline_plan_id is not None
                else None,
                "hypothesis": group.description,
                "independent_variable": group.independent_variable,
                "baseline_value": group.baseline_value,
                "variants": [item.model_dump(mode="json") for item in group.variants],
                "repetitions": group.repetitions,
                "randomize_order": group.randomize_order,
                "order_seed": group.order_seed,
                "trials": [item.model_dump(mode="json") for item in group.trials],
            }
            document["experiment_summary"] = build_experiment_summary(connection, group)
        document = cast(
            dict[str, Any],
            _redact_private_fields(cast(JsonValue, document)),
        )
        content = _serialize_report(document, body.format, body.name.strip())
    except (ValueError, LookupError) as error:
        status = 404 if isinstance(error, LookupError) else 422
        code = "run_not_found" if isinstance(error, LookupError) else "report_invalid"
        raise _error(status, code, str(error)) from error

    data_directory, reports_directory = _report_directory(request)
    target_candidate = reports_directory / f"{report_id}.{body.format.value}"
    if target_candidate.is_symlink() or target_candidate.is_junction():
        raise _error(422, "invalid_export_path", "The report target cannot be a link.")
    target = target_candidate.resolve()
    if not target.is_relative_to(reports_directory):
        raise _error(
            422, "invalid_export_path", "The report target escaped the local reports directory."
        )
    temp_path: Path | None = None
    try:
        reports_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{report_id}.", suffix=".tmp", dir=reports_directory, delete=False
        ) as temporary:
            temp_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        if os.name == "posix":
            temp_path.chmod(0o600)
        temp_path.replace(target)
        export_ref = target.relative_to(data_directory).as_posix()
        report = ReportMetadata(
            id=report_id,
            name=body.name.strip(),
            format=body.format,
            status=ReportStatus.READY,
            run_ids=body.run_ids,
            generated_at=generated_at,
            export_ref=export_ref,
            filename=filename,
            content_included=body.include_retained_content,
            redacted_summary=f"{len(body.run_ids)} run(s); credentials omitted.",
        )
        RecordRepository(connection, ReportMetadata).create(report)
        return ReportCreateResponse(report=report)
    except (OSError, sqlite3.Error) as error:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(error, sqlite3.Error):
            raise _error(
                500, "report_export_failed", "LOCALFORGE could not save report metadata."
            ) from error
        failed = ReportMetadata(
            id=report_id,
            name=body.name.strip(),
            format=body.format,
            status=ReportStatus.FAILED,
            run_ids=body.run_ids,
            generated_at=generated_at,
            filename=filename,
            content_included=False,
            error_message=(
                "LOCALFORGE could not write the report file. Check local disk space "
                "and report-directory permissions."
            ),
        )
        try:
            RecordRepository(connection, ReportMetadata).create(failed)
        except sqlite3.Error:
            pass
        raise _error(
            500, "report_export_failed", failed.error_message or "Report export failed."
        ) from error


@router.get("/reports/{report_id}/download", tags=["reports"])
def download_report(
    report_id: UUID,
    request: Request,
    connection: DatabaseConnection,
) -> FileResponse:
    report = RecordRepository(connection, ReportMetadata).get(report_id)
    if report is None or report.status is not ReportStatus.READY or not report.export_ref:
        raise _error(404, "report_file_not_found", "The report file is not available.")
    data_directory, reports_directory = _report_directory(request)
    expected_export_ref = f"reports/{report.id}.{report.format.value}"
    if report.export_ref != expected_export_ref:
        raise _error(404, "report_file_not_found", "The report file is not available.")
    target_candidate = reports_directory / f"{report.id}.{report.format.value}"
    if target_candidate.is_symlink() or target_candidate.is_junction():
        raise _error(404, "report_file_not_found", "The report file is not available.")
    path = target_candidate.resolve()
    if not path.is_relative_to(reports_directory):
        raise _error(404, "report_file_not_found", "The report file is not available.")
    if not path.is_file():
        raise _error(404, "report_file_not_found", "The report file is no longer present.")
    media_type = {
        ReportFormat.JSON: "application/json",
        ReportFormat.CSV: "text/csv; charset=utf-8",
        ReportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    }[report.format]
    return FileResponse(path, media_type=media_type, filename=report.filename or path.name)
