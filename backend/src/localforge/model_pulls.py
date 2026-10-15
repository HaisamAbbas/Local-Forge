"""Download models through a local Ollama server, only on an explicit user request.

LocalForge never downloads model weights itself: it asks the user's own Ollama to pull a tag
(``POST /api/pull`` with streaming progress), and Ollama fetches the files from its registry.
Each pull runs as a background task that records progress in memory; cancelling closes the
stream, which makes Ollama stop the download (already-downloaded layers stay in Ollama's cache
and a later pull resumes from them).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import httpx2
from pydantic import BaseModel

from localforge.core.models import utc_now

PullStatus = Literal["running", "succeeded", "failed", "cancelled"]
ProbeStatus = Literal["not_started", "running", "succeeded", "failed"]

PULL_TIMEOUT = httpx2.Timeout(connect=5.0, read=600.0, write=10.0, pool=5.0)
MAX_LINE_BYTES = 64 * 1024
MAX_ERROR_CHARS = 300
MAX_ACTIVE_PULLS = 2
MAX_REMEMBERED_PULLS = 50

# Called after a successful pull; returns (succeeded, message).
ProbeCallback = Callable[[UUID], Awaitable[tuple[bool, str]]]


class ModelPullView(BaseModel):
    id: UUID
    engine_profile_id: UUID
    engine_label: str
    ollama_tag: str
    catalog_model_id: str | None
    status: PullStatus
    phase: str | None
    bytes_completed: int | None
    bytes_total: int | None
    percent: float | None
    error: str | None
    probe_status: ProbeStatus
    probe_message: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass
class _Pull:
    id: UUID
    engine_profile_id: UUID
    engine_label: str
    base_url: str
    ollama_tag: str
    catalog_model_id: str | None
    status: PullStatus = "running"
    phase: str | None = "starting"
    layers: dict[str, tuple[int, int]] = field(default_factory=dict)
    error: str | None = None
    probe_status: ProbeStatus = "not_started"
    probe_message: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    task: "asyncio.Task[None] | None" = None

    def view(self) -> ModelPullView:
        total = sum(layer_total for layer_total, _done in self.layers.values()) or None
        done = sum(min(done, layer_total) for layer_total, done in self.layers.values())
        completed = done if total is not None else None
        percent = None
        if total:
            percent = round(100.0 * (completed or 0) / total, 1)
        if self.status == "succeeded" and total:
            completed, percent = total, 100.0
        return ModelPullView(
            id=self.id,
            engine_profile_id=self.engine_profile_id,
            engine_label=self.engine_label,
            ollama_tag=self.ollama_tag,
            catalog_model_id=self.catalog_model_id,
            status=self.status,
            phase=self.phase,
            bytes_completed=completed,
            bytes_total=total,
            percent=percent,
            error=self.error,
            probe_status=self.probe_status,
            probe_message=self.probe_message,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


class PullConflict(Exception):
    def __init__(self, code: str, message: str, pull_id: UUID | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.pull_id = pull_id


def _bounded(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_ERROR_CHARS else text[: MAX_ERROR_CHARS - 3] + "..."


class OllamaPullManager:
    """In-memory registry of Ollama pulls started from this LocalForge process."""

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._pulls: dict[UUID, _Pull] = {}

    def list(self) -> list[ModelPullView]:
        return [pull.view() for pull in sorted(self._pulls.values(), key=lambda p: p.started_at)]

    def get(self, pull_id: UUID) -> ModelPullView | None:
        pull = self._pulls.get(pull_id)
        return pull.view() if pull is not None else None

    def start(
        self,
        *,
        engine_profile_id: UUID,
        engine_label: str,
        base_url: str,
        ollama_tag: str,
        catalog_model_id: str | None,
        probe: ProbeCallback,
    ) -> ModelPullView:
        active = [pull for pull in self._pulls.values() if pull.status == "running"]
        for pull in active:
            if pull.engine_profile_id == engine_profile_id and pull.ollama_tag == ollama_tag:
                raise PullConflict(
                    "pull_in_progress", "This model is already being pulled.", pull.id
                )
        if len(active) >= MAX_ACTIVE_PULLS:
            raise PullConflict(
                "too_many_pulls",
                f"At most {MAX_ACTIVE_PULLS} pulls can run at once; wait or cancel one.",
            )
        self._forget_old()
        pull = _Pull(
            id=uuid4(),
            engine_profile_id=engine_profile_id,
            engine_label=engine_label,
            base_url=base_url.rstrip("/"),
            ollama_tag=ollama_tag,
            catalog_model_id=catalog_model_id,
        )
        self._pulls[pull.id] = pull
        pull.task = asyncio.create_task(self._run(pull, probe))
        return pull.view()

    def cancel(self, pull_id: UUID) -> ModelPullView | None:
        pull = self._pulls.get(pull_id)
        if pull is None:
            return None
        if pull.status == "running" and pull.task is not None and not pull.task.done():
            pull.task.cancel()
            # Report the outcome immediately; the task confirms it when it unwinds.
            self._finish(pull, "cancelled", phase="cancelled")
        return pull.view()

    async def wait(self, pull_id: UUID) -> None:
        """Wait for a pull's background task (tests and shutdown)."""

        pull = self._pulls.get(pull_id)
        if pull is not None and pull.task is not None:
            await asyncio.gather(pull.task, return_exceptions=True)

    async def close(self) -> None:
        tasks = [pull.task for pull in self._pulls.values() if pull.task and not pull.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _forget_old(self) -> None:
        finished = [pull for pull in self._pulls.values() if pull.status != "running"]
        finished.sort(key=lambda pull: pull.started_at)
        while len(self._pulls) >= MAX_REMEMBERED_PULLS and finished:
            self._pulls.pop(finished.pop(0).id, None)

    @staticmethod
    def _finish(
        pull: _Pull, status: PullStatus, *, phase: str | None = None, error: str | None = None
    ) -> None:
        if pull.status != "running":
            return
        pull.status = status
        if phase is not None:
            pull.phase = phase
        pull.error = error
        pull.finished_at = utc_now()

    async def _run(self, pull: _Pull, probe: ProbeCallback) -> None:
        try:
            succeeded = await self._stream(pull)
        except asyncio.CancelledError:
            self._finish(pull, "cancelled", phase="cancelled")
            raise
        except httpx2.TimeoutException:
            self._finish(pull, "failed", error="Ollama stopped sending progress (timed out).")
            return
        except httpx2.HTTPError:
            self._finish(pull, "failed", error="Could not reach Ollama. Check that it is running.")
            return
        if not succeeded:
            return
        self._finish(pull, "succeeded", phase="success")
        pull.probe_status = "running"
        try:
            ok, message = await probe(pull.engine_profile_id)
        except asyncio.CancelledError:
            pull.probe_status, pull.probe_message = "failed", "The refresh was cancelled."
            raise
        except Exception:
            ok, message = False, "The engine could not be refreshed; refresh it from Models."
        pull.probe_status = "succeeded" if ok else "failed"
        pull.probe_message = message

    async def _stream(self, pull: _Pull) -> bool:
        """Follow Ollama's NDJSON progress stream; True once Ollama reports success."""

        async with httpx2.AsyncClient(transport=self._transport, timeout=PULL_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{pull.base_url}/api/pull",
                json={"model": pull.ollama_tag, "stream": True},
            ) as response:
                if response.status_code != 200:
                    body = b""
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_LINE_BYTES:
                            break
                    self._finish(
                        pull,
                        "failed",
                        error=_ollama_error(body)
                        or f"Ollama refused the pull (HTTP {response.status_code}).",
                    )
                    return False
                async for line in response.aiter_lines():
                    if not line.strip() or len(line) > MAX_LINE_BYTES:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    error = event.get("error")
                    if isinstance(error, str) and error:
                        self._finish(pull, "failed", error=_bounded(error))
                        return False
                    status = event.get("status")
                    if isinstance(status, str) and status:
                        pull.phase = _bounded(status)
                    digest = event.get("digest")
                    total = event.get("total")
                    completed = event.get("completed", 0)
                    if (
                        isinstance(digest, str)
                        and isinstance(total, int)
                        and not isinstance(total, bool)
                        and total > 0
                    ):
                        done = (
                            completed
                            if isinstance(completed, int) and not isinstance(completed, bool)
                            else 0
                        )
                        pull.layers[digest] = (total, max(0, done))
                    if status == "success":
                        return True
        self._finish(pull, "failed", error="Ollama ended the download without confirming success.")
        return False


def _ollama_error(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return _bounded(error) if isinstance(error, str) and error else None
