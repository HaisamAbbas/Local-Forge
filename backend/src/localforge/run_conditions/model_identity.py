"""Prove which model weights a run used, by SHA-256, whenever that can be verified.

Ollama stores each model's GGUF weights as a content-addressed blob named ``sha256-<hex>``,
where the hex is the SHA-256 of the GGUF file itself. Hashing a local GGUF that llama.cpp or
KoboldCpp loaded therefore yields a digest directly comparable with Ollama's blob digest.
"""

import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from localforge.core.models import JsonValue
from localforge.core.urls import endpoint_is_remote

BASIS_OLLAMA = "ollama_digest"
BASIS_FILE = "file_hash"
BASIS_UNKNOWN = "unknown"
CACHE_FILE_NAME = "model-hash-cache.json"
_CHUNK_BYTES = 8 * 1024 * 1024
_BLOB_DIGEST = re.compile(r"sha256[-:]([0-9a-f]{64})", re.IGNORECASE)
_cache_lock = threading.Lock()


def unknown_identity(note: str) -> dict[str, JsonValue]:
    return {
        "sha256": None,
        "basis": BASIS_UNKNOWN,
        "size_bytes": None,
        "file_name": None,
        "note": note,
    }


class FileHashCache:
    """SHA-256 of local model files, cached by path, size, and modification time."""

    def __init__(self, data_directory: Path) -> None:
        self._path = data_directory / "cache" / CACHE_FILE_NAME
        self.computed = 0

    def _load(self) -> dict[str, dict[str, JsonValue]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return cast(dict[str, dict[str, JsonValue]], raw) if isinstance(raw, dict) else {}

    def _store(self, entries: dict[str, dict[str, JsonValue]]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(entries, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            pass

    def sha256(self, path: Path) -> tuple[str, int] | None:
        """Return (hex digest, size) for a readable regular file, hashing it at most once."""

        try:
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
        except (OSError, RuntimeError):
            return None
        if not resolved.is_file():
            return None
        key = os.path.normcase(str(resolved))
        with _cache_lock:
            cached = self._load().get(key)
        if (
            isinstance(cached, dict)
            and cached.get("size_bytes") == stat.st_size
            and cached.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(cached.get("sha256"), str)
        ):
            return cast(str, cached["sha256"]), stat.st_size
        digest = hashlib.sha256()
        try:
            with resolved.open("rb") as handle:
                while chunk := handle.read(_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError:
            return None
        hexdigest = digest.hexdigest()
        self.computed += 1
        with _cache_lock:
            entries = self._load()
            entries[key] = {
                "sha256": hexdigest,
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "hashed_at": datetime.now(UTC).isoformat(),
            }
            self._store(entries)
        return hexdigest, stat.st_size


def ollama_blob_digest(show_payload: object) -> str | None:
    """Read the weights blob digest from the FROM line of Ollama's generated Modelfile."""

    if not isinstance(show_payload, dict):
        return None
    modelfile = show_payload.get("modelfile")
    if not isinstance(modelfile, str):
        return None
    for line in modelfile.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        match = _BLOB_DIGEST.search(stripped)
        if match:
            return match.group(1).lower()
    return None


def _ollama_identity(show_payload: object) -> dict[str, JsonValue]:
    digest = ollama_blob_digest(show_payload)
    if digest is None:
        return unknown_identity("Ollama did not report the weights blob digest.")
    identity: dict[str, JsonValue] = {
        "sha256": digest,
        "basis": BASIS_OLLAMA,
        "size_bytes": None,
        "file_name": f"sha256-{digest[:12]}…",
        "note": "Ollama blob names are the SHA-256 of the GGUF weights file.",
    }
    details = show_payload.get("details") if isinstance(show_payload, dict) else None
    if isinstance(details, dict):
        for key in ("quantization_level", "parameter_size", "format", "family"):
            value = details.get(key)
            if isinstance(value, str) and value.strip():
                identity[key] = value.strip()[:80]
    return identity


def _file_identity(path_text: str, cache: FileHashCache, source: str) -> dict[str, JsonValue]:
    path = Path(path_text)
    if not path.is_absolute():
        return unknown_identity("The engine reported a model name, not a local file path.")
    # The path comes from the engine. Only GGUF weights are hashed, so a local process that
    # reports some other file (a document, an enormous file) cannot make LOCALFORGE read it.
    if path.suffix.casefold() != ".gguf":
        return unknown_identity("The reported file is not a GGUF model file, so it was not read.")
    hashed = cache.sha256(path)
    if hashed is None:
        return unknown_identity("The model file is not readable on this machine.")
    digest, size = hashed
    return {
        "sha256": digest,
        "basis": BASIS_FILE,
        "size_bytes": size,
        "file_name": path.name[:240],
        "source": source,
        "note": "SHA-256 of the model file the engine loaded.",
    }


def resolve_model_identity(
    *,
    adapter_id: str,
    endpoint_url: str | None,
    observed: Mapping[str, JsonValue],
    cache: FileHashCache,
) -> dict[str, JsonValue]:
    """Pick the strongest identity evidence the run's observations allow."""

    if "ollama_show" in observed:
        return _ollama_identity(observed["ollama_show"])
    try:
        remote = endpoint_is_remote(endpoint_url)
    except ValueError:
        remote = True
    candidates: list[tuple[str, str]] = []
    managed = observed.get("managed_model_path")
    if isinstance(managed, str):
        candidates.append((managed, "managed_launch_arguments"))
    if not remote:
        for key, source in (
            ("model_path", "engine_reported_model_path"),
            ("process_model_path", "process_command_line"),
        ):
            value = observed.get(key)
            if isinstance(value, str):
                candidates.append((value, source))
    last_note = "The engine did not report a model file LOCALFORGE can read."
    if remote and not candidates:
        last_note = "The engine runs on another machine, so its model file cannot be hashed."
    for path_text, source in candidates:
        identity = _file_identity(path_text, cache, source)
        if identity["sha256"] is not None:
            return identity
        last_note = cast(str, identity["note"])
    return unknown_identity(last_note)


def weights_state(identities: list[object]) -> tuple[str, list[JsonValue]]:
    """Return VERIFIED_IDENTICAL, DIFFERENT, or UNVERIFIED for a set of run identities."""

    digests: list[JsonValue] = []
    for identity in identities:
        digest = identity.get("sha256") if isinstance(identity, dict) else None
        digests.append(digest if isinstance(digest, str) else None)
    known = {item for item in digests if isinstance(item, str)}
    if len(known) > 1:
        return "different", digests
    if known and all(item is not None for item in digests):
        return "verified_identical", digests
    return "unverified", digests
