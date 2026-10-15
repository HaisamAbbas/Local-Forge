"""Opt-in, in-memory output quality checks and the built-in "Quality smoke" dataset.

Checks run in the worker on each measured output while it is still in memory. Only the
outcome (pass / fail / unknown) and stable reason codes are persisted; the output text is
stored only when the plan separately opts in to content retention.
"""

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from localforge.core.models import (
    BenchmarkPlan,
    Dataset,
    DatasetFormat,
    DatasetSource,
    DatasetStatus,
    DuplicatePolicy,
    MetricSample,
    MetricSource,
    PromptSizingMethod,
    QualityChecks,
    RequestSample,
    Workload,
    WorkloadInputMode,
)
from localforge.dataset_import import DatasetRow, ParsedDataset
from localforge.dataset_storage import write_snapshot
from localforge.storage.repositories import RecordRepository

QUALITY_METHOD_VERSION = "localforge.quality.v1"
QUALITY_METRIC_NAME = "quality_pass_rate_percent"
QualityOutcome = Literal["pass", "fail", "unknown"]

# Stable reason codes. They never contain output text.
REASON_DESCRIPTIONS: dict[str, str] = {
    "exact_match_mismatch": "answer did not match the expected answer",
    "expected_answer_missing": "the dataset row has no expected answer",
    "invalid_json": "output was not valid JSON",
    "json_not_object": "JSON output was not an object",
    "json_missing_required_key": "JSON output was missing a required key",
    "stopped_at_length_limit": "output was cut off at the token limit",
    "stop_reason_unknown": "the engine did not report output token counts",
    "output_too_long": "output was longer than the allowed length",
    "required_text_missing": "output did not contain required text",
    "forbidden_text_present": "output contained text it must not contain",
    "request_not_completed": "the request did not complete, so its output was not checked",
}


@dataclass(frozen=True)
class QualityResult:
    outcome: QualityOutcome
    reasons: tuple[str, ...]


_FENCE = re.compile(r"^```[A-Za-z0-9_-]*\s*\n?(.*?)\n?```$", re.DOTALL)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    match = _FENCE.match(stripped)
    return match.group(1).strip() if match else stripped


def normalize_answer(text: str) -> str:
    """Normalize a short answer: fences, wrapping quotes, case, spacing, trailing punctuation."""

    value = _strip_fences(text)
    value = value.strip().strip("`\"'").strip()
    value = re.sub(r"\s+", " ", value).casefold()
    return value.rstrip(".!?;:,").strip()


def _parse_json(text: str) -> tuple[bool, object]:
    try:
        return True, json.loads(_strip_fences(text))
    except (json.JSONDecodeError, ValueError):
        return False, None


def answers_match(output: str, expected: str) -> bool:
    """Exact match after normalization; JSON-shaped expected answers compare as parsed JSON."""

    expected_is_json, expected_value = _parse_json(expected)
    if expected_is_json and isinstance(expected_value, (dict, list)):
        output_is_json, output_value = _parse_json(output)
        return output_is_json and output_value == expected_value
    return normalize_answer(output) == normalize_answer(expected)


def evaluate_output(
    checks: QualityChecks,
    *,
    output: str,
    expected_answer: str | None,
    request_succeeded: bool,
    completion_tokens: int | None,
    max_tokens: int | None,
) -> QualityResult:
    """Evaluate every enabled check; any failure fails, otherwise any unknown is unknown."""

    if not request_succeeded:
        return QualityResult("unknown", ("request_not_completed",))
    failures: list[str] = []
    unknowns: list[str] = []
    if checks.exact_match:
        if expected_answer is None:
            unknowns.append("expected_answer_missing")
        elif not answers_match(output, expected_answer):
            failures.append("exact_match_mismatch")
    if checks.valid_json:
        parsed_ok, parsed = _parse_json(output)
        if not parsed_ok:
            failures.append("invalid_json")
        elif checks.json_required_keys:
            if not isinstance(parsed, dict):
                failures.append("json_not_object")
            elif any(key not in parsed for key in checks.json_required_keys):
                failures.append("json_missing_required_key")
    if checks.stopped_properly:
        if completion_tokens is None or max_tokens is None:
            unknowns.append("stop_reason_unknown")
        elif completion_tokens >= max_tokens:
            failures.append("stopped_at_length_limit")
    if checks.max_output_characters is not None and len(output) > checks.max_output_characters:
        failures.append("output_too_long")
    haystack = output if checks.case_sensitive else output.casefold()

    def present(needle: str) -> bool:
        return (needle if checks.case_sensitive else needle.casefold()) in haystack

    if checks.must_contain and not all(present(item) for item in checks.must_contain):
        failures.append("required_text_missing")
    if checks.must_not_contain and any(present(item) for item in checks.must_not_contain):
        failures.append("forbidden_text_present")
    if failures:
        return QualityResult("fail", tuple(failures))
    if unknowns:
        return QualityResult("unknown", tuple(unknowns))
    return QualityResult("pass", ())


class RequestQualityCheck:
    """A per-request evaluator bound to that request's expected answer and token limit."""

    def __init__(
        self, checks: QualityChecks, expected_answer: str | None, max_tokens: int | None
    ) -> None:
        self.checks = checks
        self.expected_answer = expected_answer
        self.max_tokens = max_tokens

    def evaluate(
        self, *, output: str, request_succeeded: bool, completion_tokens: int | None
    ) -> QualityResult:
        return evaluate_output(
            self.checks,
            output=output,
            expected_answer=self.expected_answer,
            request_succeeded=request_succeeded,
            completion_tokens=completion_tokens,
            max_tokens=self.max_tokens,
        )


def _expected_answers(
    connection: sqlite3.Connection, data_directory: Path, workload: Workload
) -> list[str | None]:
    if workload.input_mode is not WorkloadInputMode.DATASET or workload.dataset_id is None:
        return []
    dataset = RecordRepository(connection, Dataset).get(workload.dataset_id)
    if dataset is None or not dataset.storage_ref:
        return []
    answers: list[str | None] = []
    with (data_directory / dataset.storage_ref).open("rb") as snapshot:
        for line in snapshot:
            row = json.loads(line)
            answer = row.get("expected_answer") if isinstance(row, dict) else None
            answers.append(answer if isinstance(answer, str) else None)
    return answers


def request_quality_factory(
    connection: sqlite3.Connection,
    data_directory: Path,
    plan: BenchmarkPlan,
    workload: Workload,
    *,
    max_tokens: int | None,
) -> Callable[[int], RequestQualityCheck] | None:
    """Return a per-request-index evaluator factory, or None when no checks are enabled."""

    del plan  # Checks belong to the workload; the plan is accepted for future per-plan rules.
    checks = workload.quality_checks
    if checks is None or not checks.enabled():
        return None
    answers = _expected_answers(connection, data_directory, workload) if checks.exact_match else []

    def for_index(index: int) -> RequestQualityCheck:
        expected = answers[index % len(answers)] if answers else None
        return RequestQualityCheck(checks, expected, max_tokens)

    return for_index


def quality_pass_rate_metric(
    run_id: UUID, outcomes: Iterable[str | None], *, labels: dict[str, str] | None = None
) -> list[MetricSample]:
    """Summarize per-request quality outcomes; emits nothing when no request was checked."""

    counted = Counter(outcome for outcome in outcomes if outcome is not None)
    if not counted:
        return []
    passed = counted["pass"]
    evaluated = passed + counted["fail"]
    flags: list[str] = []
    if evaluated == 0:
        flags.append("unavailable_no_evaluated_outputs")
    if counted["unknown"]:
        flags.append("some_outputs_not_evaluated")
    return [
        MetricSample(
            run_id=run_id,
            metric_name=QUALITY_METRIC_NAME,
            value=100 * passed / evaluated if evaluated else None,
            unit="%",
            source=MetricSource.DERIVED,
            aggregation="run_rate",
            sample_count=evaluated,
            definition=(
                "Share of checked measurement outputs that passed every enabled quality check. "
                "Outputs that could not be checked are excluded."
            ),
            formula="100 * passed outputs / (passed + failed outputs)",
            method_version=QUALITY_METHOD_VERSION,
            quality_flags=flags,
            labels=(labels or {})
            | {
                "passed": str(passed),
                "failed": str(counted["fail"]),
                "not_checked": str(counted["unknown"]),
            },
        )
    ]


def quality_reason_counts(samples: Iterable[RequestSample]) -> Counter[str]:
    reasons: Counter[str] = Counter()
    for sample in samples:
        if sample.phase == "measurement" and sample.quality_outcome == "fail":
            reasons.update(sample.quality_reasons)
    return reasons


# --- Built-in "Quality smoke" dataset -------------------------------------------------------

QUALITY_SMOKE_DATASET_NAME = "Quality smoke (built-in)"
_ONLY_NUMBER = " Answer with only the number."
_ONLY_CITY = " Answer with only the city name."
_ONLY_JSON = " Reply with only the JSON object, no explanation."

QUALITY_SMOKE_ITEMS: tuple[tuple[str, str], ...] = (
    ("What is 7 + 5?" + _ONLY_NUMBER, "12"),
    ("What is 9 times 8?" + _ONLY_NUMBER, "72"),
    ("What is 100 minus 37?" + _ONLY_NUMBER, "63"),
    ("What is 144 divided by 12?" + _ONLY_NUMBER, "12"),
    ("What is 15 + 27?" + _ONLY_NUMBER, "42"),
    ("What is 6 squared?" + _ONLY_NUMBER, "36"),
    ("What is 1000 minus 1?" + _ONLY_NUMBER, "999"),
    ("What is the capital of France?" + _ONLY_CITY, "Paris"),
    ("What is the capital of Japan?" + _ONLY_CITY, "Tokyo"),
    ("What is the capital of Italy?" + _ONLY_CITY, "Rome"),
    ("What is the capital of Germany?" + _ONLY_CITY, "Berlin"),
    ("What is the capital of Canada?" + _ONLY_CITY, "Ottawa"),
    ("What is the capital of Egypt?" + _ONLY_CITY, "Cairo"),
    ("What is the capital of Spain?" + _ONLY_CITY, "Madrid"),
    (
        'Return a JSON object with a key "city" whose value is "Paris".' + _ONLY_JSON,
        '{"city": "Paris"}',
    ),
    (
        'Return a JSON object with a key "answer" whose value is the number 4.' + _ONLY_JSON,
        '{"answer": 4}',
    ),
    (
        'Return a JSON object with keys "a" and "b" set to the numbers 1 and 2.' + _ONLY_JSON,
        '{"a": 1, "b": 2}',
    ),
    (
        'Return a JSON object with a key "ok" set to true.' + _ONLY_JSON,
        '{"ok": true}',
    ),
    ("Spell the word cat backwards. Answer with only the word.", "tac"),
    ("What is the next number in the sequence 2, 4, 6, 8?" + _ONLY_NUMBER, "10"),
)


def _smoke_parsed_dataset() -> ParsedDataset:
    rows = tuple(
        DatasetRow(
            source_row=index + 1,
            prompt=prompt,
            expected_answer=answer,
            prompt_size=len(prompt),
        )
        for index, (prompt, answer) in enumerate(QUALITY_SMOKE_ITEMS)
    )
    snapshot = hashlib.sha256()
    for row in rows:
        snapshot.update(row.normalized_json().encode("utf-8"))
        snapshot.update(b"\n")
    digest = snapshot.hexdigest()
    return ParsedDataset(
        source_hash=digest,
        snapshot_hash=digest,
        rows=rows,
        parsed_rows=len(rows),
        duplicate_rows=0,
        rows_excluded_by_limit=0,
        count_method="unicode_codepoint_count",
    )


def ensure_quality_smoke_dataset(connection: sqlite3.Connection, data_directory: Path) -> Dataset:
    """Create (or reuse) the built-in smoke dataset and its verified local snapshot."""

    parsed = _smoke_parsed_dataset()
    storage_ref = write_snapshot(data_directory, parsed)
    repository = RecordRepository(connection, Dataset)
    for existing in repository.list(limit=1000):
        if (
            existing.source is DatasetSource.BUILT_IN_PRESET
            and existing.snapshot_hash == parsed.snapshot_hash
            and existing.status is DatasetStatus.READY
        ):
            return existing
    dataset = Dataset(
        name=QUALITY_SMOKE_DATASET_NAME,
        source=DatasetSource.BUILT_IN_PRESET,
        status=DatasetStatus.READY,
        description=(
            f"{len(parsed.rows)} short arithmetic, capital-city, and JSON-formatting prompts "
            "with expected answers."
        ),
        item_count=len(parsed.rows),
        content_hash=parsed.source_hash,
        snapshot_hash=parsed.snapshot_hash,
        content_retained=True,
        format=DatasetFormat.JSONL,
        prompt_field="prompt",
        expected_answer_field="expected_answer",
        row_limit=len(parsed.rows),
        shuffle_seed=0,
        duplicate_policy=DuplicatePolicy.KEEP,
        prompt_sizing_method=PromptSizingMethod.CHARACTERS,
        count_method=parsed.count_method,
        storage_ref=storage_ref,
        redacted_summary=f"Built-in quality smoke set; {len(parsed.rows)} prompts.",
    )
    repository.create(dataset)
    return dataset


def describe_reasons(reasons: Sequence[str]) -> list[str]:
    return [REASON_DESCRIPTIONS.get(reason, reason) for reason in reasons]


def outcome_list(samples: Iterable[RequestSample]) -> list[str | None]:
    return [
        cast(str | None, sample.quality_outcome)
        for sample in samples
        if sample.phase == "measurement"
    ]
