"""Content-addressed dataset snapshots stored beneath LOCALFORGE's data directory."""

import hashlib
import os
import tempfile
from pathlib import Path

from localforge.core.models import Dataset
from localforge.dataset_import import ParsedDataset


def _is_path_link(path: Path) -> bool:
    """Recognize symbolic links and Windows junctions in storage path components."""

    return path.is_symlink() or path.is_junction()


def dataset_incoming_directory(data_directory: Path) -> Path:
    """Return the upload staging directory after rejecting redirected path components."""

    data_root = data_directory.resolve()
    datasets_path = data_root / "datasets"
    if _is_path_link(datasets_path):
        raise ValueError("dataset storage directory cannot be a link")
    datasets_root = datasets_path.resolve()
    if datasets_root.parent != data_root:
        raise ValueError("dataset storage directory escaped local storage")

    incoming_path = datasets_root / "incoming"
    if _is_path_link(incoming_path):
        raise ValueError("dataset upload staging directory cannot be a link")
    incoming_root = incoming_path.resolve()
    if incoming_root.parent != datasets_root:
        raise ValueError("dataset upload staging directory escaped dataset storage")
    return incoming_root


def _snapshot_path(data_directory: Path, snapshot_hash: str) -> Path:
    if len(snapshot_hash) != 64 or any(
        character not in "0123456789abcdef" for character in snapshot_hash
    ):
        raise ValueError("dataset snapshot hash is invalid")
    data_root = data_directory.resolve()
    dataset_directory = data_root / "datasets"
    if _is_path_link(dataset_directory):
        raise ValueError("dataset storage directory cannot be a link")
    root = dataset_directory.resolve()
    if root.parent != data_root:
        raise ValueError("dataset storage directory escaped local storage")
    candidate = root / f"{snapshot_hash}.jsonl"
    if _is_path_link(candidate):
        raise ValueError("dataset snapshot cannot be a link")
    path = candidate.resolve()
    if path.parent != root:
        raise ValueError("dataset snapshot path escaped its storage directory")
    return path


def write_snapshot(data_directory: Path, parsed: ParsedDataset) -> str:
    """Atomically create a snapshot, refusing to overwrite different data at its hash path."""

    path = _snapshot_path(data_directory, parsed.snapshot_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hash_snapshot(path) != parsed.snapshot_hash:
            raise OSError("existing dataset snapshot failed its content hash check")
        return f"datasets/{path.name}"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="snapshot-", suffix=".tmp", dir=path.parent
    )
    try:
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as output:
            for row in parsed.rows:
                line = row.normalized_json().encode("utf-8") + b"\n"
                output.write(line)
                digest.update(line)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != parsed.snapshot_hash:
            raise OSError("generated dataset snapshot hash did not match its metadata")
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            if hash_snapshot(path) != parsed.snapshot_hash:
                raise OSError("concurrent dataset snapshot failed its content hash check") from None
        return f"datasets/{path.name}"
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def hash_snapshot(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset_snapshot(data_directory: Path, dataset: Dataset) -> bool:
    """Check path binding and bytes before an execution plan consumes imported prompts."""

    if not dataset.content_retained or not dataset.storage_ref or not dataset.snapshot_hash:
        return False
    expected_ref = f"datasets/{dataset.snapshot_hash}.jsonl"
    if dataset.storage_ref != expected_ref:
        return False
    try:
        return (
            hash_snapshot(_snapshot_path(data_directory, dataset.snapshot_hash))
            == dataset.snapshot_hash
        )
    except (OSError, ValueError):
        return False


def delete_snapshot(data_directory: Path, snapshot_hash: str) -> None:
    """Remove a newly-created orphan snapshot after a failed record transaction."""

    _snapshot_path(data_directory, snapshot_hash).unlink(missing_ok=True)
