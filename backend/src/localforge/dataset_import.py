"""Bounded, deterministic JSONL/CSV validation and immutable dataset snapshots."""

import csv
import hashlib
import json
import random
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from localforge.core.models import DatasetFormat, DuplicatePolicy, PromptSizingMethod

MAX_DATASET_ROWS = 250_000
MAX_DATASET_ROW_CHARS = 2_000_000
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,119}$")


class DatasetImportError(ValueError):
    """A dataset failure with a stable code and optional physical source location."""

    def __init__(self, code: str, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.line = line


@dataclass(frozen=True)
class DatasetRow:
    source_row: int
    prompt: str
    expected_answer: str | None
    prompt_size: int | None

    def normalized_json(self) -> str:
        return json.dumps(
            {"prompt": self.prompt, "expected_answer": self.expected_answer},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class ParsedDataset:
    source_hash: str
    snapshot_hash: str
    rows: tuple[DatasetRow, ...]
    parsed_rows: int
    duplicate_rows: int
    rows_excluded_by_limit: int
    count_method: str


def _field(value: object, mapping: str, line: int) -> str:
    if not isinstance(value, dict):
        raise DatasetImportError("invalid_row", "Each JSONL row must be an object.", line=line)
    if mapping not in value:
        raise DatasetImportError(
            "missing_field", f"Mapped field '{mapping}' is missing.", line=line
        )
    result = value[mapping]
    if not isinstance(result, str):
        raise DatasetImportError(
            "invalid_field", f"Mapped field '{mapping}' must contain text.", line=line
        )
    return result


def _prompt_size(prompt: str, method: PromptSizingMethod) -> int | None:
    if method is PromptSizingMethod.CHARACTERS:
        return len(prompt)
    if method is PromptSizingMethod.WORDS:
        return len(prompt.split())
    return None


def _read_row(
    payload: object,
    *,
    line: int,
    prompt_field: str,
    expected_answer_field: str | None,
    sizing_method: PromptSizingMethod,
) -> DatasetRow:
    prompt = _field(payload, prompt_field, line)
    if not prompt.strip():
        raise DatasetImportError("empty_prompt", "Prompt text cannot be empty.", line=line)
    if len(prompt) > MAX_DATASET_ROW_CHARS:
        raise DatasetImportError(
            "row_too_large", f"Prompt exceeds {MAX_DATASET_ROW_CHARS} characters.", line=line
        )
    answer: str | None = None
    if expected_answer_field is not None:
        if not isinstance(payload, dict) or expected_answer_field not in payload:
            raise DatasetImportError(
                "missing_field",
                f"Mapped field '{expected_answer_field}' is missing.",
                line=line,
            )
        raw_answer = payload[expected_answer_field]
        if raw_answer is not None and not isinstance(raw_answer, str):
            raise DatasetImportError(
                "invalid_field",
                f"Mapped field '{expected_answer_field}' must contain text or null.",
                line=line,
            )
        answer = raw_answer
    if answer is not None and len(answer) > MAX_DATASET_ROW_CHARS:
        raise DatasetImportError(
            "row_too_large",
            f"Expected answer exceeds {MAX_DATASET_ROW_CHARS} characters.",
            line=line,
        )
    return DatasetRow(
        source_row=line,
        prompt=prompt,
        expected_answer=answer,
        prompt_size=_prompt_size(prompt, sizing_method),
    )


def _csv_rows(
    source: TextIO,
    *,
    prompt_field: str,
    expected_answer_field: str | None,
    sizing_method: PromptSizingMethod,
) -> Iterator[DatasetRow]:
    reader = csv.DictReader(source, strict=True)
    headers = reader.fieldnames
    if not headers:
        raise DatasetImportError("empty_file", "The CSV file has no header row.", line=1)
    if any(not name or not name.strip() for name in headers):
        raise DatasetImportError("invalid_header", "CSV column names cannot be empty.", line=1)
    if len(set(headers)) != len(headers):
        raise DatasetImportError("duplicate_header", "CSV column names must be unique.", line=1)
    for mapped in (prompt_field, expected_answer_field):
        if mapped is not None and mapped not in headers:
            raise DatasetImportError(
                "missing_field", f"Mapped CSV column '{mapped}' was not found.", line=1
            )
    try:
        for row in reader:
            line = max(reader.line_num, 2)
            if None in row:
                raise DatasetImportError(
                    "extra_columns", "CSV row has more values than its header.", line=line
                )
            if any(value is None for value in row.values()):
                raise DatasetImportError(
                    "missing_field", "CSV row has fewer values than its header.", line=line
                )
            yield _read_row(
                row,
                line=line,
                prompt_field=prompt_field,
                expected_answer_field=expected_answer_field,
                sizing_method=sizing_method,
            )
    except csv.Error as error:
        raise DatasetImportError(
            "malformed_csv",
            f"Malformed CSV near line {reader.line_num}: {error}",
            line=max(reader.line_num, 1),
        ) from error


def _jsonl_rows(
    source: TextIO,
    *,
    prompt_field: str,
    expected_answer_field: str | None,
    sizing_method: PromptSizingMethod,
) -> Iterator[DatasetRow]:
    for line_number, line in enumerate(source, start=1):
        if not line.strip():
            raise DatasetImportError("empty_row", "JSONL rows cannot be blank.", line=line_number)
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise DatasetImportError(
                "malformed_jsonl",
                f"Malformed JSON on line {line_number}: {error.msg}.",
                line=line_number,
            ) from error
        yield _read_row(
            payload,
            line=line_number,
            prompt_field=prompt_field,
            expected_answer_field=expected_answer_field,
            sizing_method=sizing_method,
        )


def parse_dataset(
    path: Path,
    *,
    format: DatasetFormat,
    prompt_field: str,
    expected_answer_field: str | None,
    row_limit: int,
    shuffle_seed: int,
    duplicate_policy: DuplicatePolicy,
    sizing_method: PromptSizingMethod,
    tokenizer_name: str | None = None,
) -> ParsedDataset:
    """Parse the complete source incrementally, then select and shuffle a bounded sample."""

    for mapped in (prompt_field, expected_answer_field):
        if mapped is not None and not _FIELD_NAME.fullmatch(mapped):
            raise DatasetImportError(
                "invalid_mapping", "Field mappings must be simple column or object keys."
            )
    if row_limit < 1 or row_limit > 100_000:
        raise DatasetImportError("invalid_row_limit", "Row limit must be between 1 and 100000.")

    source_hash = hashlib.sha256()
    with path.open("rb") as binary_source:
        for chunk in iter(lambda: binary_source.read(64 * 1024), b""):
            source_hash.update(chunk)

    try:
        source = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as error:
        raise DatasetImportError("read_failed", "The uploaded file could not be read.") from error

    if path.stat().st_size == 0:
        source.close()
        raise DatasetImportError("empty_file", "The uploaded file is empty.", line=1)

    count_method = {
        PromptSizingMethod.CHARACTERS: "unicode_codepoint_count",
        PromptSizingMethod.WORDS: "whitespace_word_count",
        PromptSizingMethod.TOKENIZER: (
            f"unknown_tokenizer:{tokenizer_name}" if tokenizer_name else "unknown_tokenizer"
        ),
    }[sizing_method]
    rng = random.Random(shuffle_seed)
    seen: set[str] = set()
    selected: list[DatasetRow] = []
    accepted = 0
    duplicates = 0
    source_rows = 0
    reader = (
        _csv_rows(
            source,
            prompt_field=prompt_field,
            expected_answer_field=expected_answer_field,
            sizing_method=sizing_method,
        )
        if format is DatasetFormat.CSV
        else _jsonl_rows(
            source,
            prompt_field=prompt_field,
            expected_answer_field=expected_answer_field,
            sizing_method=sizing_method,
        )
    )
    try:
        try:
            for row in reader:
                source_rows += 1
                if source_rows > MAX_DATASET_ROWS:
                    raise DatasetImportError(
                        "row_count_limit",
                        f"Files may contain at most {MAX_DATASET_ROWS} rows.",
                        line=row.source_row,
                    )
                row_hash = hashlib.sha256(row.normalized_json().encode("utf-8")).hexdigest()
                if row_hash in seen:
                    duplicates += 1
                    if duplicate_policy is DuplicatePolicy.ERROR:
                        raise DatasetImportError(
                            "duplicate_row",
                            "Duplicate row found; no dataset was imported.",
                            line=row.source_row,
                        )
                    if duplicate_policy is DuplicatePolicy.SKIP:
                        continue
                else:
                    seen.add(row_hash)
                accepted += 1
                if len(selected) < row_limit:
                    selected.append(row)
                else:
                    index = rng.randrange(accepted)
                    if index < row_limit:
                        selected[index] = row
        except UnicodeDecodeError as error:
            raise DatasetImportError(
                "invalid_encoding", "Dataset must use valid UTF-8 text encoding."
            ) from error
        except OSError as error:
            raise DatasetImportError(
                "read_failed", "The uploaded file could not be read."
            ) from error
    finally:
        source.close()

    if accepted == 0:
        raise DatasetImportError("empty_dataset", "The file does not contain any prompt rows.")
    rng.shuffle(selected)
    snapshot_digest = hashlib.sha256()
    for row in selected:
        snapshot_digest.update(row.normalized_json().encode("utf-8"))
        snapshot_digest.update(b"\n")
    return ParsedDataset(
        source_hash=source_hash.hexdigest(),
        snapshot_hash=snapshot_digest.hexdigest(),
        rows=tuple(selected),
        parsed_rows=source_rows,
        duplicate_rows=duplicates,
        rows_excluded_by_limit=max(0, accepted - len(selected)),
        count_method=count_method,
    )
