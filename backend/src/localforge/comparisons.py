"""Evidence-backed run comparison and reproducible report assembly."""

import json
import math
import re
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, cast
from uuid import UUID

from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkRun,
    ExperimentGroup,
    ExperimentTrialStatus,
    JsonValue,
    MachineProfile,
    MetricSample,
    MetricSource,
    ModelArtifact,
    Workload,
)
from localforge.storage.repositories import (
    MetricSampleRepository,
    RecordRepository,
    RequestSampleRepository,
)

_IGNORED_WORKLOAD_FIELDS = {"id", "schema_version", "created_at", "updated_at", "status", "name"}
_PLAN_FIELDS = (
    "warmup_requests",
    "measured_requests",
    "measurement_duration_seconds",
    "concurrency",
    "timeout_seconds",
    "retain_prompt_output",
    "dataset_snapshot_hash",
)
_PRIVATE_FIELD_NAMES = {
    "accesskey",
    "accesstoken",
    "apikey",
    "authorization",
    "authkey",
    "authtoken",
    "bearer",
    "credential",
    "credentials",
    "credentialref",
    "credentialrefs",
    "apitoken",
    "bearertoken",
    "password",
    "privatekey",
    "secret",
    "secretreferences",
    "secretrefs",
    "token",
}
_HARDWARE_REQUIRED_FIELDS = (
    "os_name",
    "os_version",
    "architecture",
    "cpu_model",
    "cpu_logical_cores",
    "memory_total_bytes",
)
_HARDWARE_OPTIONAL_FIELDS = ("cpu_physical_cores",)
_ACCELERATOR_REQUIRED_FIELDS = ("name", "vendor")
_ACCELERATOR_OPTIONAL_FIELDS = (
    "memory_bytes",
    "integrated",
    "memory_kind",
    "runtime",
    "driver_version",
)
# Per-observation records describe one request or one telemetry tick, not a run, so pairing them
# across runs is arbitrary. Run-level aggregates of the same data are compared instead.
_PER_OBSERVATION_AGGREGATIONS = {"raw", "sample"}
_VOLATILE_METRIC_LABELS = {
    "correlationid",
    "requestid",
    "runid",
    "sequencenumber",
    "traceid",
    "trialid",
}


def _json_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _redact_sensitive_text(value: str) -> str:
    value = re.sub(
        r"(?i)(\b(?:authorization|api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|credential)\b\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|token)=)[^&#\s]+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    return re.sub(r"(?i)(://)[^/@\s]+@", r"\1[REDACTED]@", value)


def _is_private_field(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    if normalized in {
        "prompttokens",
        "completiontokens",
        "maxtokens",
        "credentialsincluded",
    }:
        return False
    words = set(re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).casefold()))
    return (
        normalized in _PRIVATE_FIELD_NAMES
        or normalized.startswith("secret")
        or normalized.endswith(("secret", "credential", "credentials", "password", "token"))
        or bool(
            words
            & {
                "authorization",
                "auth",
                "bearer",
                "credential",
                "credentials",
                "password",
                "secret",
                "privatekey",
            }
        )
        or {"api", "key"}.issubset(words)
        or {"access", "token"}.issubset(words)
        or {"auth", "token"}.issubset(words)
        or (
            "token" in words
            and bool(
                words & {"credential", "key", "password", "reference", "ref", "secret", "value"}
            )
        )
    )


def _stable_metric_labels(labels: dict[str, str]) -> dict[str, str]:
    return {
        key: _redact_sensitive_text(value)
        for key, value in labels.items()
        if re.sub(r"[^a-z0-9]", "", key.casefold()) not in _VOLATILE_METRIC_LABELS
        and not _is_private_field(key)
    }


def _find_quantization(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized in {"quantization", "quantizationlevel", "quantizationmetadata"}:
                if nested is not None:
                    return nested
        for nested in value.values():
            found = _find_quantization(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_quantization(nested)
            if found is not None:
                return found
    return None


def _redact_private_fields(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: _redact_private_fields(child)
            for key, child in value.items()
            if not _is_private_field(key)
        }
    if isinstance(value, list):
        return [_redact_private_fields(child) for child in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _workload_payload(
    plan: BenchmarkPlan, connection: sqlite3.Connection
) -> dict[str, JsonValue] | None:
    raw = plan.configuration_snapshot.get("workload")
    if isinstance(raw, dict):
        return raw
    workload = RecordRepository(connection, Workload).get(plan.workload_id)
    return workload.model_dump(mode="json") if workload is not None else None


def _workload_without_identity(
    workload: dict[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    if workload is None:
        return None
    return {key: value for key, value in workload.items() if key not in _IGNORED_WORKLOAD_FIELDS}


def _flatten(value: JsonValue, prefix: str = "") -> dict[str, JsonValue]:
    if isinstance(value, dict):
        flattened: dict[str, JsonValue] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            flattened.update(_flatten(child, path))
        return flattened
    return {prefix: value}


def _workload_changes(
    left: dict[str, JsonValue] | None,
    right: dict[str, JsonValue] | None,
) -> list[dict[str, JsonValue]]:
    if left is None or right is None:
        return [{"path": "workload", "values": [left, right]}]
    left_flat = _flatten(cast(JsonValue, _workload_without_identity(left)))
    right_flat = _flatten(cast(JsonValue, _workload_without_identity(right)))
    return [
        {
            "path": f"workload.{key}",
            "values": [left_flat.get(key), right_flat.get(key)],
        }
        for key in sorted(set(left_flat) | set(right_flat))
        if _json_key(left_flat.get(key)) != _json_key(right_flat.get(key))
    ]


def _read_path(value: object, path: str) -> object:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _model_snapshot(
    run: BenchmarkRun, plan: BenchmarkPlan, connection: sqlite3.Connection
) -> dict[str, JsonValue] | None:
    captured = run.profile_snapshot.get("model_artifact")
    if isinstance(captured, dict):
        return captured
    artifact = (
        RecordRepository(connection, ModelArtifact).get(plan.model_artifact_id)
        if plan.model_artifact_id is not None
        else None
    )
    if artifact is None:
        return None
    return artifact.model_dump(mode="json")


def _machine_snapshot(
    run: BenchmarkRun, plan: BenchmarkPlan, connection: sqlite3.Connection
) -> dict[str, JsonValue] | None:
    captured = run.profile_snapshot.get("machine")
    if isinstance(captured, dict):
        return captured
    machine_id = plan.machine_profile_id
    machine = (
        RecordRepository(connection, MachineProfile).get(machine_id)
        if machine_id is not None
        else None
    )
    return machine.model_dump(mode="json") if machine is not None else None


def _model_identity_incomplete(model: dict[str, JsonValue] | None) -> bool:
    if model is None:
        return True
    identity = cast(dict[str, JsonValue], _model_identity(model))
    quantization = identity.get("quantization")
    return (
        any(
            _identity_value_missing(identity.get(key))
            for key in ("model_ref", "revision", "format", "quantization")
        )
        or isinstance(quantization, (dict, list))
        and not quantization
    )


def _identity_value_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _identity_value(value: object) -> JsonValue:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (bool, int, float)):
        return value
    return None


def _hardware_identities(
    machines: list[dict[str, JsonValue] | None],
) -> tuple[list[JsonValue], list[bool], list[str]]:
    """Project machine snapshots onto stable hardware identity fields.

    Required fields must be known for every run, or the dimension is unknown. Optional fields
    (for example discrete-GPU memory, which shared-memory integrated GPUs do not report) are
    compared only when every selected run reports them; partially reported fields are listed
    as unverified instead of silently matching. Detection provenance (``detected_by``) and
    observation timestamps are never part of the identity.
    """

    identities: list[dict[str, JsonValue] | None] = []
    incomplete: list[bool] = []
    for machine in machines:
        hardware = machine.get("hardware") if machine is not None else None
        if not isinstance(hardware, dict):
            identities.append(None)
            incomplete.append(True)
            continue
        identity: dict[str, JsonValue] = {
            key: _identity_value(hardware.get(key))
            for key in (*_HARDWARE_REQUIRED_FIELDS, *_HARDWARE_OPTIONAL_FIELDS)
        }
        missing = any(identity[key] is None for key in _HARDWARE_REQUIRED_FIELDS)
        raw_accelerators = hardware.get("accelerators")
        accelerators: list[JsonValue] | None = None
        if isinstance(raw_accelerators, list):
            accelerators = []
            for raw in raw_accelerators:
                if not isinstance(raw, dict):
                    missing = True
                    continue
                accelerator = {
                    key: _identity_value(raw.get(key))
                    for key in (*_ACCELERATOR_REQUIRED_FIELDS, *_ACCELERATOR_OPTIONAL_FIELDS)
                }
                missing = missing or any(
                    accelerator[key] is None for key in _ACCELERATOR_REQUIRED_FIELDS
                )
                accelerators.append(cast(JsonValue, accelerator))
        else:
            missing = True
        identity["accelerators"] = accelerators
        identities.append(identity)
        incomplete.append(missing)

    known = [identity for identity in identities if identity is not None]
    unverified: list[str] = []
    for key in _HARDWARE_OPTIONAL_FIELDS:
        if not known or any(identity[key] is None for identity in known):
            if any(identity[key] is not None for identity in known):
                unverified.append(key)
            for identity in known:
                identity.pop(key, None)
    accelerator_rows = [
        cast(dict[str, JsonValue], accelerator)
        for identity in known
        if isinstance(identity.get("accelerators"), list)
        for accelerator in cast(list[JsonValue], identity["accelerators"])
    ]
    for key in _ACCELERATOR_OPTIONAL_FIELDS:
        if not accelerator_rows or any(row[key] is None for row in accelerator_rows):
            if any(row[key] is not None for row in accelerator_rows):
                unverified.append(f"accelerators.{key}")
            for row in accelerator_rows:
                row.pop(key, None)
    for identity in known:
        if isinstance(identity.get("accelerators"), list):
            cast(list[JsonValue], identity["accelerators"]).sort(key=_json_key)
    return cast(list[JsonValue], identities), incomplete, unverified


def _model_identity(model: dict[str, JsonValue] | None) -> object:
    if model is None:
        return None
    quantization = _find_quantization(model.get("engine_extensions"))
    return {
        "model_ref": model.get("model_ref"),
        "revision": model.get("revision"),
        "format": model.get("format"),
        "size_bytes": model.get("size_bytes"),
        "quantization": quantization,
    }


def _tokenizer_identity(model: dict[str, JsonValue] | None) -> object:
    return model.get("tokenizer_identity") if model is not None else None


def _is_run_level_metric(metric: MetricSample) -> bool:
    """Return whether a metric summarizes a run rather than one request or telemetry tick."""

    return (
        metric.source is not MetricSource.SYSTEM_OBSERVED
        and metric.aggregation not in _PER_OBSERVATION_AGGREGATIONS
        and not any(
            re.sub(r"[^a-z0-9]", "", key.casefold()) == "requestid" for key in metric.labels
        )
    )


def _metric_signature(metric: MetricSample) -> tuple[object, ...]:
    return (
        metric.metric_name,
        metric.unit,
        metric.source.value,
        metric.aggregation,
        metric.definition,
        metric.formula,
        metric.method_version,
        metric.metric_kind,
        _json_key(_stable_metric_labels(metric.labels)),
    )


def _metric_value(rows: list[MetricSample]) -> float | None:
    values = [row.value for row in rows if row.value is not None and math.isfinite(row.value)]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.fmean(values)


def _comparison_metrics(
    run_ids: list[UUID], metric_rows: dict[UUID, list[MetricSample]]
) -> tuple[list[dict[str, JsonValue]], list[dict[str, JsonValue]], list[str]]:
    by_run: dict[UUID, dict[tuple[object, ...], list[MetricSample]]] = {}
    per_observation_names: set[str] = set()
    for run_id in run_ids:
        indexed: dict[tuple[object, ...], list[MetricSample]] = defaultdict(list)
        for metric in metric_rows[run_id]:
            if not _is_run_level_metric(metric):
                per_observation_names.add(metric.metric_name)
                continue
            indexed[_metric_signature(metric)].append(metric)
        by_run[run_id] = indexed

    common_signatures: set[tuple[object, ...]] | None = None
    for run_id in run_ids:
        current = set(by_run[run_id])
        common_signatures = current if common_signatures is None else common_signatures & current
    common_signatures = common_signatures or set()
    shared: list[dict[str, JsonValue]] = []
    unavailable: list[dict[str, JsonValue]] = []
    for signature in sorted(common_signatures, key=_json_key):
        (
            name,
            unit,
            source,
            aggregation,
            definition,
            formula,
            method_version,
            kind,
            _labels_key,
        ) = signature
        per_run: dict[str, JsonValue] = {}
        values: dict[UUID, float | None] = {}
        for run_id in run_ids:
            rows = by_run[run_id][signature]
            value = _metric_value(rows)
            values[run_id] = value
            per_run[str(run_id)] = {
                "value": value,
                "sample_count": sum(row.sample_count for row in rows if row.value is not None),
                "record_count": len(rows),
            }
        if all(value is None for value in values.values()):
            unavailable.append(
                {
                    "metric_name": cast(str, name),
                    "reason": (
                        "Metric has no value for any compared run: "
                        f"{_json_key([aggregation, _stable_metric_labels(rows[0].labels)])}"
                    ),
                }
            )
            continue
        baseline = values[run_ids[0]]
        shared.append(
            {
                "metric_name": cast(str, name),
                "unit": cast(str, unit),
                "source": cast(str, source),
                "aggregation": cast(str, aggregation),
                "definition": cast(str, definition),
                "formula": cast(str, formula),
                "method_version": cast(str, method_version),
                "metric_kind": cast(str, kind),
                "labels": cast(JsonValue, _stable_metric_labels(rows[0].labels)),
                "baseline_run_id": str(run_ids[0]),
                "runs": per_run,
                "observed_deltas": _observed_deltas(values, baseline, run_ids),
            }
        )
    all_signatures = set().union(*(set(by_run[run_id]) for run_id in run_ids))
    excluded: list[dict[str, JsonValue]] = [
        {
            "metric_name": cast(str, signature[0]),
            "reason": f"Metric signature is not shared by every run: {_json_key(signature[1:])}",
        }
        for signature in sorted(all_signatures - common_signatures, key=_json_key)
    ]
    excluded.extend(unavailable)
    excluded.extend(
        {
            "metric_name": name,
            "reason": (
                "Per-request and telemetry-sample observations are not paired across runs; "
                "only run-level summaries are compared."
            ),
        }
        for name in sorted(per_observation_names)
    )
    sources = sorted({cast(str, signature[2]) for signature in common_signatures})
    return shared, excluded, sources


def _observed_deltas(
    values: dict[UUID, float | None], baseline: float | None, run_ids: list[UUID]
) -> dict[str, JsonValue]:
    deltas: dict[str, JsonValue] = {}
    for run_id in run_ids:
        observed = values[run_id]
        deltas[str(run_id)] = (
            observed - baseline if observed is not None and baseline is not None else None
        )
    return deltas


def build_run_comparison(
    connection: sqlite3.Connection,
    run_ids: Iterable[UUID],
    *,
    declared_variable: str | None = None,
    baseline_run_id: UUID | None = None,
) -> dict[str, JsonValue]:
    """Compare only metric definitions shared by every selected run."""

    selected_ids = list(dict.fromkeys(run_ids))
    if len(selected_ids) < 2:
        raise ValueError("select at least two distinct runs to compare")
    runs_by_id: dict[UUID, BenchmarkRun] = {}
    plans: dict[UUID, BenchmarkPlan] = {}
    workloads: dict[UUID, dict[str, JsonValue] | None] = {}
    models: dict[UUID, dict[str, JsonValue] | None] = {}
    machines: dict[UUID, dict[str, JsonValue] | None] = {}
    metrics: dict[UUID, list[MetricSample]] = {}
    for run_id in selected_ids:
        run = RecordRepository(connection, BenchmarkRun).get(run_id)
        if run is None:
            raise LookupError("one or more selected runs were not found")
        plan = RecordRepository(connection, BenchmarkPlan).get(run.plan_id)
        if plan is None:
            raise LookupError("a selected run no longer has its benchmark plan")
        runs_by_id[run_id] = run
        plans[run_id] = plan
        workloads[run_id] = _workload_payload(plan, connection)
        models[run_id] = _model_snapshot(run, plan, connection)
        machines[run_id] = _machine_snapshot(run, plan, connection)
        metrics[run_id] = MetricSampleRepository(connection).list_for_run(run_id)

    first_id = baseline_run_id if baseline_run_id in selected_ids else selected_ids[0]
    compare_ids = [first_id, *(run_id for run_id in selected_ids if run_id != first_id)]
    comparison_rows, excluded_metrics, _metric_sources = _comparison_metrics(compare_ids, metrics)
    first = compare_ids[0]
    dimensions: list[dict[str, JsonValue]] = []
    noncomparable: list[str] = []
    unknown_critical: list[str] = []
    major_mismatch: list[str] = []

    def dimension(
        key: str,
        label: str,
        values: list[JsonValue],
        *,
        unknown_if_missing: bool = False,
        critical: bool = False,
        major: bool = False,
        expected_change: bool = False,
        incomplete_values: list[bool] | None = None,
    ) -> bool:
        known = [item for item in values if item is not None]
        incomplete = len(known) < len(values) or any(incomplete_values or [])
        equal = (
            not incomplete
            and len(known) == len(values)
            and len({_json_key(item) for item in known}) <= 1
        )
        state = (
            "match" if equal else ("unknown" if unknown_if_missing and incomplete else "mismatch")
        )
        if expected_change and not equal:
            state = "declared_change"
        dimensions.append({"key": key, "label": label, "state": state, "values": values})
        if state == "unknown":
            unknown_critical.append(key)
        elif state == "mismatch":
            if critical:
                noncomparable.append(key)
            elif major:
                major_mismatch.append(key)
        return equal

    hardware_values, hardware_incomplete, hardware_unverified = _hardware_identities(
        [machines[run_id] for run_id in compare_ids]
    )
    dimension(
        "hardware",
        "Hardware",
        hardware_values,
        unknown_if_missing=True,
        major=True,
        incomplete_values=hardware_incomplete,
    )
    if hardware_unverified:
        dimensions[-1]["unverified_fields"] = cast(JsonValue, hardware_unverified)

    model_values = [cast(JsonValue, _model_identity(models[run_id])) for run_id in compare_ids]
    dimension(
        "model_artifact",
        "Model artifact, revision, and quantization",
        model_values,
        unknown_if_missing=True,
        critical=True,
        incomplete_values=[_model_identity_incomplete(models[run_id]) for run_id in compare_ids],
    )
    tokenizer_values = [
        cast(JsonValue, _tokenizer_identity(models[run_id])) for run_id in compare_ids
    ]
    dimension(
        "tokenizer",
        "Tokenizer identity",
        tokenizer_values,
        unknown_if_missing=True,
        critical=True,
        incomplete_values=[
            _identity_value_missing(_tokenizer_identity(models[run_id])) for run_id in compare_ids
        ],
    )

    engine_values = [runs_by_id[run_id].profile_snapshot.get("engine") for run_id in compare_ids]
    runtime_values: list[JsonValue] = []
    runtime_incomplete: list[bool] = []
    for engine_snapshot in engine_values:
        if isinstance(engine_snapshot, dict):
            adapter_version = engine_snapshot.get("adapter_version")
            engine_version = engine_snapshot.get("engine_version")
            runtime_values.append(
                {
                    "adapter_id": engine_snapshot.get("adapter_id"),
                    "adapter_version": adapter_version,
                    "engine_version": engine_version,
                }
            )
            runtime_incomplete.append(
                not isinstance(adapter_version, str)
                or not adapter_version.strip()
                or not isinstance(engine_version, str)
                or not engine_version.strip()
            )
        else:
            runtime_values.append(None)
            runtime_incomplete.append(True)
    dimension(
        "runtime",
        "Engine and runtime version",
        runtime_values,
        unknown_if_missing=True,
        major=True,
        incomplete_values=runtime_incomplete,
    )

    workload_changes: list[dict[str, JsonValue]] = []
    base_workload = workloads[first]
    for run_id in compare_ids[1:]:
        workload_changes.extend(
            {
                "path": change["path"],
                "run_ids": [str(first), str(run_id)],
                "values": change["values"],
            }
            for change in _workload_changes(base_workload, workloads[run_id])
        )
    allowed_path = f"workload.{declared_variable}" if declared_variable else None
    changed_paths = sorted({cast(str, item["path"]) for item in workload_changes})
    unexpected_workload_changes = [path for path in changed_paths if path != allowed_path]
    declared_value = (
        {
            "path": allowed_path,
            "values": [
                _read_path(workloads[run_id], declared_variable or "") for run_id in compare_ids
            ],
        }
        if allowed_path is not None
        else None
    )
    workload_state = (
        "match"
        if not changed_paths
        else "declared_change"
        if declared_variable is not None and not unexpected_workload_changes
        else "mismatch"
    )
    dimensions.append(
        {
            "key": "workload",
            "label": "Workload, input snapshot, and sampling settings",
            "state": workload_state,
            "values": [_workload_without_identity(workloads[run_id]) for run_id in compare_ids],
            "changes": cast(JsonValue, workload_changes),
        }
    )
    if unexpected_workload_changes:
        noncomparable.append("workload")

    traffic_values = [
        {
            "traffic_mode": _read_path(workloads[run_id], "traffic_mode"),
            "arrival_rate_per_second": _read_path(workloads[run_id], "arrival_rate_per_second"),
            "concurrency": _read_path(workloads[run_id], "concurrency"),
            "concurrency_levels": _read_path(workloads[run_id], "concurrency_levels"),
        }
        if workloads[run_id] is not None
        else None
        for run_id in compare_ids
    ]
    traffic_changes_expected = declared_variable in {
        "concurrency",
        "arrival_rate_per_second",
    }
    dimension(
        "traffic_pattern",
        "Traffic pattern and load generation",
        cast(list[JsonValue], traffic_values),
        unknown_if_missing=True,
        major=True,
        expected_change=traffic_changes_expected,
    )

    plan_values: list[JsonValue] = []
    for run_id in compare_ids:
        plan = plans[run_id]
        plan_snapshot: dict[str, JsonValue] = {
            field: cast(JsonValue, getattr(plan, field)) for field in _PLAN_FIELDS
        }
        if declared_variable == "concurrency":
            plan_snapshot.pop("concurrency", None)
        plan_values.append(plan_snapshot)
    dimension("plan_settings", "Benchmark plan settings", plan_values, unknown_if_missing=True)

    measurement_values: list[JsonValue] = []
    for run_id in compare_ids:
        definitions_by_key: dict[str, dict[str, JsonValue]] = {}
        for metric in metrics[run_id]:
            if not _is_run_level_metric(metric):
                continue
            definition: dict[str, JsonValue] = {
                "metric_name": metric.metric_name,
                "unit": metric.unit,
                "source": metric.source.value,
                "aggregation": metric.aggregation,
                "definition": metric.definition,
                "formula": metric.formula,
                "method_version": metric.method_version,
                "metric_kind": metric.metric_kind,
                "labels": cast(JsonValue, _stable_metric_labels(metric.labels)),
            }
            definitions_by_key[_json_key(definition)] = definition
        definitions = list(definitions_by_key.values())
        measurement_values.append(
            cast(JsonValue, sorted(definitions, key=_json_key)) if definitions else None
        )
    dimension(
        "measurement_sources",
        "Shared metric sources and definitions",
        measurement_values,
        unknown_if_missing=True,
    )
    contention_values = [runs_by_id[run_id].potentially_contended for run_id in compare_ids]
    contention_equal = len(set(contention_values)) == 1 and not any(contention_values)
    dimensions.append(
        {
            "key": "contention",
            "label": "Resource contention",
            "state": "match" if contention_equal else "mismatch",
            "values": cast(list[JsonValue], contention_values),
        }
    )
    if not contention_equal:
        major_mismatch.append("contention")

    changed_variable_count = len(changed_paths)
    has_major = bool(major_mismatch)
    if noncomparable:
        grade = "not_comparable"
        rationale = (
            "Model artifact/tokenizer or workload identity differs beyond the declared variable."
        )
    elif changed_variable_count > 1 or (declared_variable is None and changed_variable_count > 0):
        grade = "not_comparable"
        rationale = (
            "More than one workload variable changed, so a controlled comparison is not supported."
        )
    elif has_major:
        grade = "C"
        rationale = (
            "The runs differ in major environment or measurement conditions; "
            "treat the result as directional."
        )
    elif unknown_critical or any(item["state"] == "unknown" for item in dimensions):
        grade = "B"
        rationale = (
            "Some provenance is unknown; the matching evidence supports a cautious comparison."
        )
    elif any(item["state"] == "mismatch" for item in dimensions):
        grade = "B"
        rationale = "A non-critical provenance field differs; the result is mostly controlled."
    else:
        grade = "A"
        rationale = "The captured setup matches except for the declared variable."

    if grade == "not_comparable":
        label = "Not comparable"
    else:
        label = {"A": "Controlled", "B": "Mostly controlled", "C": "Directional"}[grade]
    causal_allowed = (
        grade == "A"
        and changed_variable_count <= 1
        and (declared_variable is None or not unexpected_workload_changes)
        and not any(contention_values)
    )
    result: dict[str, Any] = {
        "run_ids": [str(item) for item in compare_ids],
        "baseline_run_id": str(first),
        "declared_variable": declared_value,
        "grade": grade,
        "label": label,
        "rationale": rationale,
        "controlled_comparison_badge": causal_allowed,
        "causal_interpretation_allowed": causal_allowed,
        "dimensions": dimensions,
        "workload_changes": cast(JsonValue, workload_changes),
        "excluded_metrics": cast(JsonValue, excluded_metrics),
        "shared_metrics": cast(JsonValue, comparison_rows),
        "run_statuses": {str(run_id): runs_by_id[run_id].status.value for run_id in compare_ids},
    }
    return cast(dict[str, JsonValue], result)


def build_report_document(
    connection: sqlite3.Connection,
    *,
    run_ids: list[UUID],
    app_version: str,
    database_schema_version: int,
    baseline_run_id: UUID | None = None,
    declared_variable: str | None = None,
    include_retained_content: bool = False,
) -> dict[str, JsonValue]:
    comparison = (
        build_run_comparison(
            connection,
            run_ids,
            declared_variable=declared_variable,
            baseline_run_id=baseline_run_id,
        )
        if len(run_ids) > 1
        else None
    )
    report_runs: list[dict[str, JsonValue]] = []
    for run_id in run_ids:
        run = RecordRepository(connection, BenchmarkRun).get(run_id)
        if run is None:
            raise LookupError("one or more selected runs were not found")
        plan = RecordRepository(connection, BenchmarkPlan).get(run.plan_id)
        if plan is None:
            raise LookupError("a selected run no longer has its benchmark plan")
        metrics = MetricSampleRepository(connection).list_for_run(run_id)
        outcome_counts = {
            str(row["outcome"]): int(row["count"])
            for row in connection.execute(
                "SELECT outcome, COUNT(*) AS count "
                "FROM request_samples WHERE run_id = ? GROUP BY outcome",
                (str(run_id),),
            ).fetchall()
        }
        phase_counts = {
            str(row["phase"]): int(row["count"])
            for row in connection.execute(
                "SELECT json_extract(payload_json, '$.phase') AS phase, COUNT(*) AS count "
                "FROM request_samples WHERE run_id = ? GROUP BY phase",
                (str(run_id),),
            ).fetchall()
            if row["phase"] is not None
        }
        error_counts = {
            str(row["error_class"]): int(row["count"])
            for row in connection.execute(
                "SELECT json_extract(payload_json, '$.error_class') "
                "AS error_class, COUNT(*) AS count "
                "FROM request_samples WHERE run_id = ? "
                "AND json_extract(payload_json, '$.error_class') IS NOT NULL "
                "GROUP BY error_class",
                (str(run_id),),
            ).fetchall()
        }
        token_sources = {
            "prompt": {
                str(row["source"]): int(row["count"])
                for row in connection.execute(
                    "SELECT json_extract(payload_json, '$.prompt_token_count_source') AS source, "
                    "COUNT(*) AS count FROM request_samples WHERE run_id = ? GROUP BY source",
                    (str(run_id),),
                ).fetchall()
                if row["source"] is not None
            },
            "completion": {
                str(row["source"]): int(row["count"])
                for row in connection.execute(
                    "SELECT json_extract(payload_json, '$.completion_token_count_source') "
                    "AS source, "
                    "COUNT(*) AS count FROM request_samples WHERE run_id = ? GROUP BY source",
                    (str(run_id),),
                ).fetchall()
                if row["source"] is not None
            },
        }
        requests: list[dict[str, JsonValue]] = []
        if include_retained_content:
            for sample in RequestSampleRepository(connection).iter_for_run(run_id, limit=None):
                if sample.content_retained:
                    requests.append(
                        {
                            "sequence_number": sample.sequence_number,
                            "phase": sample.phase,
                            "outcome": sample.outcome.value,
                            "prompt_text": (
                                _redact_sensitive_text(sample.prompt_text)
                                if sample.prompt_text is not None
                                else None
                            ),
                            "output_text": (
                                _redact_sensitive_text(sample.output_text)
                                if sample.output_text is not None
                                else None
                            ),
                        }
                    )
                    if len(requests) > 50_000:
                        raise ValueError(
                            "selected retained content exceeds the report sample limit"
                        )
        report_run: dict[str, Any] = {
            "run": {
                "id": str(run.id),
                "status": run.status.value,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "potentially_contended": run.potentially_contended,
                "error_code": run.error_code,
                "error_message": (
                    _redact_sensitive_text(run.error_message)
                    if run.error_message is not None
                    else None
                ),
                "profile_snapshot": _redact_private_fields(run.profile_snapshot),
            },
            "plan": _redact_private_fields(plan.model_dump(mode="json")),
            "workload": _redact_private_fields(plan.configuration_snapshot.get("workload")),
            "request_sample_summary": {
                "total": sum(outcome_counts.values()),
                "outcomes": outcome_counts,
                "phases": phase_counts,
                "error_classes": error_counts,
                "token_count_sources": token_sources,
            },
            "metrics": [
                _redact_private_fields(metric.model_dump(mode="json")) for metric in metrics
            ],
            "request_content": requests if include_retained_content else None,
            "warnings": [
                _redact_sensitive_text(message)
                for message in [
                    "Run proceeded while resources were potentially contended."
                    if run.potentially_contended
                    else None
                ]
                if message is not None
            ],
        }
        report_runs.append(cast(dict[str, JsonValue], report_run))
    result: dict[str, Any] = {
        "report_version": 1,
        "app_version": app_version,
        "database_schema_version": database_schema_version,
        "generated_at": None,
        "content_included": include_retained_content,
        "privacy": {
            "credentials_included": False,
            "request_text_included": include_retained_content,
        },
        "comparability": _redact_private_fields(cast(JsonValue, comparison)),
        "runs": report_runs,
    }
    return cast(dict[str, JsonValue], result)


def build_experiment_summary(
    connection: sqlite3.Connection, group: ExperimentGroup
) -> dict[str, JsonValue]:
    """Summarize successful repeats with median and min/max spread, preserving failures."""

    run_ids = [
        trial.run_id
        for trial in group.trials
        if trial.run_id is not None and trial.status is ExperimentTrialStatus.SUCCEEDED
    ]
    comparison = (
        build_run_comparison(
            connection,
            run_ids,
            declared_variable=group.independent_variable,
        )
        if len(run_ids) >= 2
        else None
    )
    metric_rows = comparison.get("shared_metrics", []) if comparison is not None else []
    variants = [
        {"name": "Baseline", "value": group.baseline_value},
        *[item.model_dump(mode="json") for item in group.variants],
    ]
    summaries: list[dict[str, JsonValue]] = []
    for variant in variants:
        name = cast(str, variant["name"])
        trials = [item for item in group.trials if item.variant_name == name]
        succeeded = [item for item in trials if item.status is ExperimentTrialStatus.SUCCEEDED]
        failed_count = sum(item.status is ExperimentTrialStatus.FAILED for item in trials)
        trial_metrics: list[dict[str, JsonValue]] = []
        if isinstance(metric_rows, list):
            for raw_metric in metric_rows:
                if not isinstance(raw_metric, dict):
                    continue
                per_run = raw_metric.get("runs")
                if not isinstance(per_run, dict):
                    continue
                observations: list[float] = []
                sample_count = 0
                for trial in succeeded:
                    if trial.run_id is None:
                        continue
                    observation = per_run.get(str(trial.run_id))
                    if isinstance(observation, dict):
                        value = observation.get("value")
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            observations.append(float(value))
                        count = observation.get("sample_count")
                        if isinstance(count, int) and not isinstance(count, bool):
                            sample_count += count
                trial_metrics.append(
                    {
                        "metric_name": raw_metric.get("metric_name"),
                        "labels": raw_metric.get("labels"),
                        "unit": raw_metric.get("unit"),
                        "source": raw_metric.get("source"),
                        "aggregation": raw_metric.get("aggregation"),
                        "definition": raw_metric.get("definition"),
                        "formula": raw_metric.get("formula"),
                        "method_version": raw_metric.get("method_version"),
                        "metric_kind": raw_metric.get("metric_kind"),
                        "median": statistics.median(observations) if observations else None,
                        "spread_min": min(observations) if observations else None,
                        "spread_max": max(observations) if observations else None,
                        "repeat_count": len(observations),
                        "sample_count": sample_count,
                    }
                )
        variant_summary: dict[str, Any] = {
            "name": name,
            "value": variant.get("value"),
            "trial_count": len(trials),
            "succeeded_count": len(succeeded),
            "failed_count": failed_count,
            "metrics": trial_metrics,
        }
        summaries.append(cast(dict[str, JsonValue], variant_summary))
    result: dict[str, Any] = {
        "experiment_group_id": str(group.id),
        "name": group.name,
        "status": group.status.value,
        "independent_variable": group.independent_variable,
        "hypothesis": group.description,
        "repetitions": group.repetitions,
        "randomize_order": group.randomize_order,
        "order_seed": group.order_seed,
        "comparison": comparison,
        "variants": summaries,
        "failed_trials": [
            trial.model_dump(mode="json")
            for trial in group.trials
            if trial.status is ExperimentTrialStatus.FAILED
        ],
    }
    return cast(dict[str, JsonValue], result)
