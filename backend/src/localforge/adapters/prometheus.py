"""Bounded parser that preserves native Prometheus samples without normalizing them."""

import math
import re
from dataclasses import dataclass

from localforge.adapters.contracts import AdapterMetric
from localforge.core.models import MetricSource

_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_SAMPLE_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[^\s#]+)(?:\s+\d+)?(?:\s+#.*)?$"
)
_MAX_LINE_CHARS = 16_384
_MAX_SAMPLES = 10_000
_MAX_LABELS = 32
_MAX_LABEL_VALUE_CHARS = 512
_MAX_HELP_CHARS = 1_000


@dataclass(frozen=True)
class PrometheusParseResult:
    metrics: list[AdapterMetric]
    malformed_lines: int
    ignored_lines: int
    truncated_samples: int
    missing_definitions: int
    unsupported_samples: int


def _parse_labels(source: str | None) -> dict[str, str] | None:
    if not source:
        return {}
    labels: dict[str, str] = {}
    index = 0
    length = len(source)

    while index < length:
        while index < length and source[index].isspace():
            index += 1
        start = index
        while index < length and (source[index].isalnum() or source[index] == "_"):
            index += 1
        key = source[start:index]
        if not _LABEL_NAME.fullmatch(key) or key in labels or len(labels) >= _MAX_LABELS:
            return None
        while index < length and source[index].isspace():
            index += 1
        if index >= length or source[index] != "=":
            return None
        index += 1
        while index < length and source[index].isspace():
            index += 1
        if index >= length or source[index] != '"':
            return None
        index += 1

        value: list[str] = []
        escaped = False
        while index < length:
            char = source[index]
            index += 1
            if escaped:
                value.append({"n": "\n", "r": "\r", "t": "\t"}.get(char, char))
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                break
            else:
                value.append(char)
        else:
            return None
        if escaped:
            return None
        label_value = "".join(value)
        if len(label_value) > _MAX_LABEL_VALUE_CHARS:
            label_value = label_value[:_MAX_LABEL_VALUE_CHARS]
        labels[key] = label_value

        while index < length and source[index].isspace():
            index += 1
        if index == length:
            break
        if source[index] != ",":
            return None
        index += 1
        if not source[index:].strip():
            return None

    return labels


def _metric_unit(name: str, help_text: str, component: str) -> str:
    if component in {"bucket", "count"}:
        return "observations"
    family = name
    for suffix in ("_bucket", "_sum", "_count"):
        if family.endswith(suffix):
            family = family[: -len(suffix)]
            break
    if family.endswith("_seconds"):
        return "seconds"
    if family.endswith("_milliseconds"):
        return "milliseconds"
    if family.endswith("_bytes"):
        return "bytes"
    if family.endswith("_tokens") or family.endswith("_tokens_total"):
        return "tokens"

    lowered = help_text.casefold()
    for unit in ("tokens", "requests", "bytes"):
        if re.search(rf"\b{unit}\s+(?:per|/)\s+seconds?\b", lowered):
            return f"{unit}/second"
    if (
        "request" in lowered
        and any(word in lowered for word in ("number", "count", "total"))
        and not any(word in lowered for word in ("per second", "throughput", "rate"))
    ):
        return "requests"
    return "native"


def _sample_component(name: str, types: dict[str, str]) -> tuple[str, str, str]:
    for suffix, component in (
        ("_bucket", "bucket"),
        ("_sum", "sum"),
        ("_count", "count"),
    ):
        if name.endswith(suffix):
            family = name[: -len(suffix)]
            family_type = types.get(family)
            if family_type in {"histogram", "summary"}:
                return family, family_type, component
    family_type = types.get(name, "untyped")
    component = "quantile" if family_type == "summary" else "value"
    return name, family_type, component


def parse_engine_prometheus(text: str, *, metric_prefix: str) -> PrometheusParseResult:
    """Keep finite samples for one engine namespace and attach their raw definitions."""

    if not _METRIC_NAME.fullmatch(metric_prefix):
        raise ValueError("metric_prefix must be a valid Prometheus metric-name prefix")

    help_by_name: dict[str, str] = {}
    types: dict[str, str] = {}
    sample_lines: list[str] = []
    malformed_lines = 0
    ignored_lines = 0
    truncated_samples = 0

    for line in text.splitlines():
        if len(line) > _MAX_LINE_CHARS:
            malformed_lines += 1
            continue
        if not line or line.startswith("#"):
            parts = line.split(maxsplit=2)
            if len(parts) == 3 and parts[0] == "#":
                if parts[1] == "HELP" and _METRIC_NAME.fullmatch(parts[2].split(maxsplit=1)[0]):
                    metric_name, _, help_text = parts[2].partition(" ")
                    help_by_name[metric_name] = help_text[:_MAX_HELP_CHARS]
                elif parts[1] == "TYPE":
                    values = parts[2].split()
                    if (
                        len(values) == 2
                        and _METRIC_NAME.fullmatch(values[0])
                        and values[1] in {"counter", "gauge", "histogram", "summary", "untyped"}
                    ):
                        types[values[0]] = values[1]
                    else:
                        malformed_lines += 1
            continue
        candidate = line.split("{", maxsplit=1)[0].split(maxsplit=1)
        if not candidate or not candidate[0].startswith(metric_prefix):
            ignored_lines += 1
            continue
        if len(sample_lines) >= _MAX_SAMPLES:
            truncated_samples += 1
            continue
        sample_lines.append(line)

    metrics: list[AdapterMetric] = []
    missing_definitions = 0
    unsupported_samples = 0
    for line in sample_lines:
        match = _SAMPLE_LINE.fullmatch(line)
        if match is None:
            malformed_lines += 1
            continue
        name = match.group("name")
        if not name.startswith(metric_prefix) or not _METRIC_NAME.fullmatch(name):
            ignored_lines += 1
            continue
        if len(name) > 120:
            malformed_lines += 1
            continue
        labels = _parse_labels(match.group("labels"))
        if labels is None:
            malformed_lines += 1
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            malformed_lines += 1
            continue
        if not math.isfinite(value):
            malformed_lines += 1
            continue

        family, metric_type, component = _sample_component(name, types)
        if metric_type in {"summary", "untyped"}:
            unsupported_samples += 1
            continue
        metric_help = help_by_name.get(family) or help_by_name.get(name)
        if not metric_help:
            missing_definitions += 1
            metric_help = "Definition not reported by the endpoint."
        unit = _metric_unit(name, metric_help, component)
        native_labels = {
            key: label_value
            for key, label_value in labels.items()
            if not any(
                marker in key.casefold().replace("_", "")
                for marker in (
                    "authorization",
                    "apikey",
                    "password",
                    "secret",
                    "token",
                    "credential",
                )
            )
            and not key.casefold().startswith("localforge_")
        }
        native_labels.update(
            {
                "localforge_prometheus_family": family,
                "localforge_prometheus_type": metric_type,
                "localforge_prometheus_component": component,
                "localforge_prometheus_help": metric_help,
            }
        )
        metrics.append(
            AdapterMetric(
                metric_name=name,
                value=value,
                unit=unit,
                source=MetricSource.ENGINE_NATIVE,
                kind=(
                    "counter"
                    if metric_type == "counter" or component in {"bucket", "sum", "count"}
                    else "gauge"
                ),
                labels=native_labels,
            )
        )

    return PrometheusParseResult(
        metrics=metrics,
        malformed_lines=malformed_lines,
        ignored_lines=ignored_lines,
        truncated_samples=truncated_samples,
        missing_definitions=missing_definitions,
        unsupported_samples=unsupported_samples,
    )
