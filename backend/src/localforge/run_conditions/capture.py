"""Worker-side capture of run conditions at run start and end, stored in the run snapshot."""

import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import httpx2

from localforge.core.models import (
    BenchmarkRun,
    EngineProfile,
    JsonValue,
    RequestOutcome,
)
from localforge.run_conditions.analysis import (
    PromptRepeatTracker,
    RequestObservation,
    detect_prompt_cache,
    detect_prompt_truncation,
)
from localforge.run_conditions.model_identity import (
    FileHashCache,
    resolve_model_identity,
    unknown_identity,
)
from localforge.run_conditions.power import capture_power_state, power_snapshot
from localforge.run_conditions.runtime_settings import (
    ProcessFinder,
    capture_runtime_settings,
    find_listening_process,
    merge_runtime_settings,
)
from localforge.runs import emit_run_event
from localforge.storage.database import transaction
from localforge.storage.repositories import RecordRepository, RequestSampleRepository

CAPTURE_TIMEOUT_SECONDS = 20.0
HASH_TIMEOUT_SECONDS = 600.0


@dataclass
class RunConditionsCapture:
    """Everything the worker needs to finish capture after the measurement window."""

    connection: sqlite3.Connection
    run_id: UUID
    engine: EngineProfile
    model_ref: str
    data_directory: Path
    transport: httpx2.AsyncBaseTransport | None = None
    process_finder: ProcessFinder = find_listening_process
    power_reader: Callable[[], dict[str, JsonValue]] = capture_power_state
    tracker: PromptRepeatTracker = field(default_factory=PromptRepeatTracker)
    runtime_start: dict[str, JsonValue] | None = None
    power_start: dict[str, JsonValue] | None = None

    def _extensions(self) -> Mapping[str, Mapping[str, JsonValue]]:
        return {
            name: values
            for name, values in self.engine.engine_extensions.items()
            if isinstance(values, Mapping)
        }

    async def _runtime(self, stage: str) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        async with httpx2.AsyncClient(transport=self.transport) as client:
            return await asyncio.wait_for(
                capture_runtime_settings(
                    adapter_id=self.engine.adapter_id,
                    integration_mode=self.engine.integration_mode.value,
                    endpoint_url=self.engine.endpoint_url,
                    engine_extensions=self._extensions(),
                    model_ref=self.model_ref,
                    client=client,
                    process_finder=self.process_finder,
                    stage=stage,
                ),
                timeout=CAPTURE_TIMEOUT_SECONDS,
            )

    def _update_snapshot(self, values: dict[str, JsonValue]) -> None:
        with transaction(self.connection):
            repository = RecordRepository(self.connection, BenchmarkRun)
            run = repository.get(self.run_id)
            if run is None:
                return
            snapshot = dict(run.profile_snapshot)
            snapshot.update(values)
            repository.update(run.model_copy(update={"profile_snapshot": snapshot}))

    async def capture_start(self) -> None:
        """Record runtime settings, model identity, and power state before any request."""

        values: dict[str, JsonValue] = {}
        observed: dict[str, JsonValue] = {}
        try:
            self.runtime_start, observed = await self._runtime("run_start")
            values["runtime_settings"] = self.runtime_start
        except Exception:
            values["runtime_settings"] = {
                "schema_version": 1,
                "adapter_id": self.engine.adapter_id,
                "status": "unavailable",
                "fields": {},
            }
        try:
            cache = FileHashCache(self.data_directory)
            identity = await asyncio.wait_for(
                asyncio.to_thread(
                    resolve_model_identity,
                    adapter_id=self.engine.adapter_id,
                    endpoint_url=self.engine.endpoint_url,
                    observed=observed,
                    cache=cache,
                ),
                timeout=HASH_TIMEOUT_SECONDS,
            )
        except Exception:
            identity = unknown_identity("The model identity could not be verified.")
        values["model_identity"] = identity
        self.power_start = await asyncio.to_thread(self.power_reader)
        values["power_state"] = power_snapshot(self.power_start, None)
        self._update_snapshot(values)

    def observe_prompt(self, prompt: str, sequence: int) -> str:
        return self.tracker.observe(prompt, sequence)

    async def capture_end(self) -> None:
        """Record end-of-run power, fill late runtime settings, and flag prompt-cache hits."""

        values: dict[str, JsonValue] = {}
        if self.power_start is not None:
            power_end = await asyncio.to_thread(self.power_reader)
            values["power_state"] = power_snapshot(self.power_start, power_end)
        if self.runtime_start is not None:
            try:
                runtime_end, _observed = await self._runtime("run_end")
                values["runtime_settings"] = merge_runtime_settings(self.runtime_start, runtime_end)
            except Exception:
                pass
        warning = detect_prompt_cache(self._observations())
        values["prompt_cache"] = (
            warning
            if warning is not None
            else {"code": None, "affected_requests": 0, "checked": True}
        )
        truncation = detect_prompt_truncation(self._observations())
        values["prompt_truncation"] = (
            truncation
            if truncation is not None
            else {"code": None, "affected_requests": 0, "checked": True}
        )
        if values:
            self._update_snapshot(values)
        if warning is not None:
            emit_run_event(self.connection, self.run_id, "warning", warning)
        if truncation is not None:
            emit_run_event(self.connection, self.run_id, "warning", truncation)

    def _observations(self) -> list[RequestObservation]:
        cached_by_request: dict[str, int] = {}
        for row in self.connection.execute(
            "SELECT request_id, payload_json FROM run_events "
            "WHERE run_id = ? AND event_type = 'engine_usage' AND request_id IS NOT NULL",
            (str(self.run_id),),
        ):
            payload = json.loads(row["payload_json"])
            cached = payload.get("cached_input_tokens") if isinstance(payload, dict) else None
            if isinstance(cached, int) and not isinstance(cached, bool):
                cached_by_request[str(row["request_id"])] = cached
        evaluated_by_request: dict[str, int] = {}
        for row in self.connection.execute(
            "SELECT payload_json FROM metric_samples WHERE run_id = ? "
            "AND json_extract(payload_json, '$.metric_name') = 'ollama.prompt_eval_count'",
            (str(self.run_id),),
        ):
            payload = json.loads(row["payload_json"])
            labels = payload.get("labels") if isinstance(payload, dict) else None
            value = payload.get("value") if isinstance(payload, dict) else None
            if isinstance(labels, dict) and isinstance(value, (int, float)):
                request_id = labels.get("request_id")
                if isinstance(request_id, str):
                    evaluated_by_request[request_id] = int(value)
        observations: list[RequestObservation] = []
        for sample in RequestSampleRepository(self.connection).iter_for_run(self.run_id):
            prompt_hash, repeated = self.tracker.by_sequence.get(
                sample.sequence_number, (None, False)
            )
            request_key = str(sample.request_id) if sample.request_id is not None else ""
            observations.append(
                RequestObservation(
                    sequence=sample.sequence_number,
                    phase=sample.phase,
                    succeeded=sample.outcome is RequestOutcome.SUCCEEDED,
                    ttft_ms=sample.ttft_ms,
                    prompt_hash=prompt_hash,
                    repeated_prompt=repeated,
                    prompt_tokens=sample.prompt_tokens,
                    cached_input_tokens=cached_by_request.get(request_key),
                    prompt_eval_count=evaluated_by_request.get(request_key),
                    prompt_chars=self.tracker.chars_by_sequence.get(sample.sequence_number),
                )
            )
        return observations
