"""Comparison dimensions for engine runtime settings, model weights, and host power state."""

from typing import cast

from localforge.core.models import JsonValue
from localforge.run_conditions.model_identity import weights_state
from localforge.run_conditions.power import (
    POWER_FIELD_LABELS,
    POWER_IDENTITY_FIELDS,
)
from localforge.run_conditions.runtime_settings import (
    FIELD_LABELS,
    KEY_RUNTIME_FIELDS,
    RUNTIME_FIELDS,
    runtime_field_value,
)


def _display(value: JsonValue) -> str:
    if value is None:
        return "not reported"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:.0%}" if 0 <= value <= 1 else f"{value:g}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none"
    return str(value)


def describe_differences(differences: list[dict[str, JsonValue]]) -> str:
    """Plain list such as ``CPU threads (6 vs 12) and parallel slots (1 vs 4)``."""

    parts = []
    for item in differences:
        values = cast(list[JsonValue], item.get("values") or [])
        parts.append(f"{item.get('label')} ({' vs '.join(_display(value) for value in values)})")
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def runtime_settings_dimension(snapshots: list[object]) -> dict[str, JsonValue]:
    differing: list[dict[str, JsonValue]] = []
    unknown: list[str] = []
    matched: list[str] = []
    for field in RUNTIME_FIELDS:
        values = [runtime_field_value(snapshot, field) for snapshot in snapshots]
        if any(value is None for value in values):
            if any(value is not None for value in values) or field in KEY_RUNTIME_FIELDS:
                unknown.append(field)
            continue
        if len({repr(value) for value in values}) > 1:
            differing.append(
                {"field": field, "label": FIELD_LABELS[field], "values": cast(JsonValue, values)}
            )
        else:
            matched.append(field)
    if differing:
        state = "mismatch"
    elif all(field in matched for field in KEY_RUNTIME_FIELDS):
        state = "match"
    else:
        state = "unknown"
    return {
        "key": "runtime_settings",
        "label": "Engine runtime settings",
        "state": state,
        "values": cast(
            JsonValue,
            [
                snapshot.get("fields") if isinstance(snapshot, dict) else None
                for snapshot in snapshots
            ],
        ),
        "differing_fields": cast(JsonValue, differing),
        "unknown_fields": cast(JsonValue, unknown),
        "matched_fields": cast(JsonValue, matched),
        "summary": describe_differences(differing) if differing else None,
    }


def model_weights_dimension(identities: list[object]) -> dict[str, JsonValue]:
    state, digests = weights_state(identities)
    return {
        "key": "model_weights",
        "label": "Model weights",
        "state": state,
        "values": cast(
            JsonValue,
            [
                {
                    "sha256": identity.get("sha256"),
                    "basis": identity.get("basis"),
                    "size_bytes": identity.get("size_bytes"),
                    "file_name": identity.get("file_name"),
                }
                if isinstance(identity, dict)
                else None
                for identity in identities
            ],
        ),
        "digests": digests,
    }


def _power_view(power_state: object) -> tuple[dict[str, JsonValue] | None, bool]:
    if not isinstance(power_state, dict):
        return None, False
    start = power_state.get("start")
    if not isinstance(start, dict):
        return None, False
    view = {
        field: cast(JsonValue, start.get(field))
        for field in POWER_IDENTITY_FIELDS
        if start.get(field) is not None
    }
    return (view or None), power_state.get("changed_during_run") is True


def power_state_dimension(power_states: list[object]) -> dict[str, JsonValue]:
    views = [_power_view(state) for state in power_states]
    changed_runs = [index for index, (_view, changed) in enumerate(views) if changed]
    differing: list[dict[str, JsonValue]] = []
    for field in POWER_IDENTITY_FIELDS:
        values = [view.get(field) if view is not None else None for view, _changed in views]
        if all(value is not None for value in values) and len({repr(v) for v in values}) > 1:
            differing.append(
                {
                    "field": field,
                    "label": POWER_FIELD_LABELS[field],
                    "values": cast(JsonValue, values),
                }
            )
    ac_known = all(view is not None and "ac_power" in view for view, _changed in views)
    if differing or changed_runs:
        state = "mismatch"
    elif ac_known:
        state = "match"
    else:
        state = "unknown"
    return {
        "key": "power_state",
        "label": "Power state",
        "state": state,
        "values": cast(JsonValue, [view for view, _changed in views]),
        "differing_fields": cast(JsonValue, differing),
        "changed_during_run": cast(JsonValue, changed_runs),
        "summary": describe_differences(differing) if differing else None,
    }
