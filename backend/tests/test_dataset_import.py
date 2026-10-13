"""Streaming parser and immutable content-addressed dataset snapshot checks."""

import subprocess

import pytest

from localforge.core.models import (
    Dataset,
    DatasetFormat,
    DatasetSource,
    DatasetStatus,
    DuplicatePolicy,
    PromptSizingMethod,
)
from localforge.dataset_import import DatasetImportError, parse_dataset
from localforge.dataset_storage import (
    dataset_incoming_directory,
    verify_dataset_snapshot,
    write_snapshot,
)
from tests.path_helpers import create_directory_link


def _parse(path, *, format: DatasetFormat = DatasetFormat.JSONL, **overrides):
    values = {
        "format": format,
        "prompt_field": "prompt",
        "expected_answer_field": None,
        "row_limit": 10,
        "shuffle_seed": 7,
        "duplicate_policy": DuplicatePolicy.KEEP,
        "sizing_method": PromptSizingMethod.CHARACTERS,
    }
    values.update(overrides)
    return parse_dataset(path, **values)


def test_jsonl_sampling_hash_is_deterministic_and_duplicate_policy_is_counted(tmp_path) -> None:
    source = tmp_path / "prompts.jsonl"
    source.write_text(
        '{"prompt":"alpha"}\n{"prompt":"beta"}\n{"prompt":"alpha"}\n{"prompt":"gamma"}\n',
        encoding="utf-8",
    )

    first = _parse(
        source,
        row_limit=2,
        shuffle_seed=71,
        duplicate_policy=DuplicatePolicy.SKIP,
    )
    replay = _parse(
        source,
        row_limit=2,
        shuffle_seed=71,
        duplicate_policy=DuplicatePolicy.SKIP,
    )

    assert first.source_hash == replay.source_hash
    assert first.snapshot_hash == replay.snapshot_hash
    assert first.rows == replay.rows
    assert first.parsed_rows == 4
    assert first.duplicate_rows == 1
    assert first.rows_excluded_by_limit == 1


def test_jsonl_reports_line_errors_without_returning_a_partial_snapshot(tmp_path) -> None:
    source = tmp_path / "malformed.jsonl"
    source.write_text('{"prompt":"valid"}\n{"other":"missing"}\n', encoding="utf-8")

    with pytest.raises(DatasetImportError, match="Mapped field 'prompt' is missing") as error:
        _parse(source)

    assert error.value.code == "missing_field"
    assert error.value.line == 2


def test_csv_mapping_and_duplicate_error_are_line_specific(tmp_path) -> None:
    source = tmp_path / "prompts.csv"
    source.write_text(
        "question,answer\nfirst,one\nsecond,two\nfirst,one\n",
        encoding="utf-8",
    )

    with pytest.raises(DatasetImportError, match="Duplicate row found") as error:
        _parse(
            source,
            format=DatasetFormat.CSV,
            prompt_field="question",
            expected_answer_field="answer",
            duplicate_policy=DuplicatePolicy.ERROR,
        )

    assert error.value.code == "duplicate_row"
    assert error.value.line == 4


def test_retained_snapshot_verification_detects_modified_bytes(tmp_path) -> None:
    source = tmp_path / "prompts.jsonl"
    source.write_text('{"prompt":"stable"}\n', encoding="utf-8")
    parsed = _parse(source)
    dataset = Dataset(
        name="Stable prompts",
        source=DatasetSource.IMPORTED_FILE,
        status=DatasetStatus.READY,
        item_count=len(parsed.rows),
        content_hash=parsed.source_hash,
        snapshot_hash=parsed.snapshot_hash,
        content_retained=True,
        storage_ref=write_snapshot(tmp_path, parsed),
    )

    assert verify_dataset_snapshot(tmp_path, dataset) is True
    (tmp_path / dataset.storage_ref).write_text('{"prompt":"changed"}\n', encoding="utf-8")
    assert verify_dataset_snapshot(tmp_path, dataset) is False


def test_dataset_storage_rejects_a_symlinked_root(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    try:
        create_directory_link(tmp_path / "datasets", outside)
    except (NotImplementedError, OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    with pytest.raises(ValueError, match="cannot be a link"):
        dataset_incoming_directory(tmp_path)

    source = tmp_path / "prompts.jsonl"
    source.write_text('{"prompt":"private"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be a link"):
        write_snapshot(tmp_path, _parse(source))
    assert list(outside.iterdir()) == []


def test_dataset_storage_rejects_a_symlinked_upload_staging_directory(tmp_path) -> None:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-incoming-outside"
    outside.mkdir()
    try:
        create_directory_link(datasets / "incoming", outside)
    except (NotImplementedError, OSError, subprocess.SubprocessError) as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    with pytest.raises(ValueError, match="staging directory cannot be a link"):
        dataset_incoming_directory(tmp_path)
    assert list(outside.iterdir()) == []


def test_dataset_storage_rejects_a_symlinked_snapshot_file(tmp_path) -> None:
    source = tmp_path / "prompts.jsonl"
    source.write_text('{"prompt":"private"}\n', encoding="utf-8")
    parsed = _parse(source)
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-snapshot-outside.jsonl"
    outside.write_text('{"prompt":"outside"}\n', encoding="utf-8")
    snapshot = datasets / f"{parsed.snapshot_hash}.jsonl"
    try:
        snapshot.symlink_to(outside)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"file symlinks are unavailable: {error}")

    with pytest.raises(ValueError, match="snapshot cannot be a link"):
        write_snapshot(tmp_path, parsed)
    dataset = Dataset(
        name="Linked prompts",
        source=DatasetSource.IMPORTED_FILE,
        status=DatasetStatus.READY,
        item_count=len(parsed.rows),
        content_hash=parsed.source_hash,
        snapshot_hash=parsed.snapshot_hash,
        content_retained=True,
        storage_ref=f"datasets/{parsed.snapshot_hash}.jsonl",
    )
    assert verify_dataset_snapshot(tmp_path, dataset) is False
    assert outside.read_text(encoding="utf-8") == '{"prompt":"outside"}\n'
