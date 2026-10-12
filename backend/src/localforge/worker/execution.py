"""Run one persisted benchmark in the isolated LOCALFORGE worker process."""

import asyncio
import json
import os
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from localforge.adapters.catalog import (
    AdapterCatalog,
    AdapterNotFoundError,
    default_adapter_catalog,
)
from localforge.adapters.contracts import AdapterConfiguration, GenerationRequest, StreamEventType
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkRun,
    BenchmarkRunStatus,
    Dataset,
    DatasetStatus,
    EngineProfile,
    JsonValue,
    ModelArtifact,
    RequestOutcome,
    RequestSample,
    Workload,
    WorkloadInputMode,
    WorkloadPreset,
)
from localforge.dataset_storage import verify_dataset_snapshot
from localforge.runs import (
    ACTIVE_RUN_STATUSES,
    emit_run_event,
    heartbeat_worker,
    transition_run,
)
from localforge.storage.database import open_database
from localforge.storage.repositories import RecordRepository, RequestSampleRepository

CANCEL_DRAIN_SECONDS = 2.0
MAX_WORKER_CONCURRENCY = 128
HEARTBEAT_INTERVAL_SECONDS = 0.5
CONTROL_POLL_INTERVAL_SECONDS = 0.05


class RunSetupError(RuntimeError):
    """A safe, stable run setup failure that can be shown in the local UI."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AdapterDrainTimeoutError(RuntimeError):
    """Raised after a request outlives the worker's bounded cancellation drain."""


def _finish_stop_request(connection: sqlite3.Connection, run: BenchmarkRun) -> None:
    """Keep user cancellation distinct from an app shutdown interruption."""

    shutting_down = run.error_code == "app_shutdown"
    transition_run(
        connection,
        run.id,
        BenchmarkRunStatus.INTERRUPTED if shutting_down else BenchmarkRunStatus.CANCELLED,
        error_code=run.error_code if shutting_down else None,
        error_message=run.error_message if shutting_down else None,
    )


def _load_run_context(
    connection: sqlite3.Connection,
    run_id: UUID,
    data_directory: Path,
) -> tuple[BenchmarkRun, BenchmarkPlan, Workload, EngineProfile, str, Callable[[int], str]]:
    run = RecordRepository(connection, BenchmarkRun).get(run_id)
    if run is None:
        raise RunSetupError("run_missing", "The saved run could not be found.")
    plan = RecordRepository(connection, BenchmarkPlan).get(run.plan_id)
    if plan is None:
        raise RunSetupError("plan_missing", "The benchmark plan no longer exists.")
    workload = RecordRepository(connection, Workload).get(plan.workload_id)
    engine = RecordRepository(connection, EngineProfile).get(plan.engine_profile_id)
    if workload is None or engine is None:
        raise RunSetupError("configuration_missing", "The saved run configuration is incomplete.")
    if engine.status.value != "enabled":
        raise RunSetupError("engine_not_ready", "The selected engine is no longer enabled.")

    model_ref: str | None = engine.selected_model_ref
    if plan.model_artifact_id is not None:
        model = RecordRepository(connection, ModelArtifact).get(plan.model_artifact_id)
        if model is None or model.status.value == "missing":
            raise RunSetupError("model_unavailable", "The selected model is no longer available.")
        model_ref = model.model_ref
    if not model_ref:
        raise RunSetupError("model_missing", "The selected engine has no saved model reference.")

    if workload.input_mode is WorkloadInputMode.SYNTHETIC:

        def prompt_for(index: int) -> str:
            return f"LOCALFORGE synthetic benchmark request {index + 1}."

    else:
        dataset = (
            RecordRepository(connection, Dataset).get(workload.dataset_id)
            if workload.dataset_id is not None
            else None
        )
        if (
            dataset is None
            or dataset.status is not DatasetStatus.READY
            or not dataset.content_retained
            or dataset.snapshot_hash != plan.dataset_snapshot_hash
            or not verify_dataset_snapshot(data_directory, dataset)
        ):
            raise RunSetupError(
                "dataset_snapshot_unavailable", "The saved dataset snapshot failed verification."
            )
        snapshot_path = data_directory / cast(str, dataset.storage_ref)
        offsets: list[int] = []
        with snapshot_path.open("rb") as snapshot:
            while True:
                position = snapshot.tell()
                line = snapshot.readline()
                if not line:
                    break
                offsets.append(position)
        if not offsets:
            raise RunSetupError("dataset_empty", "The retained dataset has no usable prompts.")

        def prompt_for(index: int) -> str:
            with snapshot_path.open("rb") as snapshot:
                snapshot.seek(offsets[index % len(offsets)])
                row = json.loads(snapshot.readline())
            prompt = row.get("prompt") if isinstance(row, dict) else None
            if not isinstance(prompt, str):
                raise RunSetupError("dataset_invalid", "A retained dataset row has no prompt text.")
            return prompt

    return run, plan, workload, engine, model_ref, prompt_for


def _adapter_config(engine: EngineProfile) -> AdapterConfiguration:
    values = dict(engine.config)
    if engine.endpoint_url is not None:
        values["endpoint_url"] = engine.endpoint_url
    if engine.health_route is not None:
        values["health_route"] = engine.health_route
    if engine.models_route is not None:
        values["models_route"] = engine.models_route
    return AdapterConfiguration(
        integration_mode=engine.integration_mode,
        values=values,
        secret_refs=engine.secret_refs,
    )


async def _execute_request(
    *,
    connection: sqlite3.Connection,
    catalog: AdapterCatalog,
    adapter_id: str,
    config: AdapterConfiguration,
    model_ref: str,
    prompt: str,
    parameters: dict[str, JsonValue],
    run_id: UUID,
    request_index: int,
    phase: str,
    concurrency_level: int | None = None,
    timeout_seconds: float,
    cancellation_requested: asyncio.Event,
    counts: Counter[str],
    retain_content: bool,
) -> RequestOutcome:
    request_id = uuid4()
    wall_started = datetime.now(UTC)
    monotonic_started = time.perf_counter()
    started_payload: dict[str, JsonValue] = {"sequence": request_index, "phase": phase}
    if concurrency_level is not None:
        started_payload["concurrency_level"] = concurrency_level
    emit_run_event(
        connection,
        run_id,
        "request_started",
        started_payload,
        request_id=request_id,
    )
    first_output_at: float | None = None
    chunk_index = 0
    output_parts: list[str] = []
    output_characters = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage_exact = False
    observed_terminal: StreamEventType | None = None
    failed_category: str | None = None

    async def consume() -> None:
        nonlocal first_output_at, chunk_index, output_characters
        nonlocal input_tokens, output_tokens, usage_exact, observed_terminal, failed_category
        request = GenerationRequest(
            model_ref=model_ref,
            prompt=prompt,
            parameters=parameters,
        )
        prepared = await catalog.prepare_request(adapter_id, config, request)
        async for event in catalog.stream_request(adapter_id, prepared):
            if event.event_type in {StreamEventType.FIRST_OUTPUT, StreamEventType.OUTPUT_CHUNK}:
                chunk = event.text or ""
                output_characters += len(chunk)
                if retain_content and chunk:
                    output_parts.append(chunk)
                if event.event_type is StreamEventType.FIRST_OUTPUT and first_output_at is None:
                    first_output_at = time.perf_counter()
                    emit_run_event(
                        connection,
                        run_id,
                        "first_output",
                        {"observed": True},
                        request_id=request_id,
                    )
                emit_run_event(
                    connection,
                    run_id,
                    "output_chunk",
                    {"chunk_index": chunk_index, "characters": len(chunk)},
                    request_id=request_id,
                )
                chunk_index += 1
            elif event.event_type is StreamEventType.USAGE and event.usage is not None:
                input_tokens = event.usage.input_tokens
                output_tokens = event.usage.output_tokens
                usage_exact = event.usage.exact
                emit_run_event(
                    connection,
                    run_id,
                    "engine_usage",
                    {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "exact": usage_exact,
                    },
                    request_id=request_id,
                )
            elif event.event_type is StreamEventType.COMPLETED:
                observed_terminal = StreamEventType.COMPLETED
            elif event.event_type in {
                StreamEventType.ERROR,
                StreamEventType.DISCONNECTED,
                StreamEventType.TIMEOUT,
                StreamEventType.CANCELLED,
            }:
                observed_terminal = event.event_type
                failed_category = {
                    StreamEventType.ERROR: "adapter_error",
                    StreamEventType.DISCONNECTED: "disconnected",
                    StreamEventType.TIMEOUT: "timeout",
                    StreamEventType.CANCELLED: "cancelled",
                }[event.event_type]

    async def abort_and_drain() -> None:
        try:
            abort = await asyncio.wait_for(
                catalog.abort_request(adapter_id, request_id), timeout=1.0
            )
            if not abort.supported or not abort.accepted:
                emit_run_event(
                    connection,
                    run_id,
                    "warning",
                    {
                        "code": "adapter_cancellation_unavailable",
                        "message": (
                            "The engine may continue processing after LOCALFORGE "
                            "closes the request."
                        ),
                    },
                    request_id=request_id,
                )
        except (TimeoutError, AdapterNotFoundError, ValueError):
            emit_run_event(
                connection,
                run_id,
                "warning",
                {
                    "code": "adapter_cancellation_unavailable",
                    "message": (
                        "The engine may continue processing after LOCALFORGE closes the request."
                    ),
                },
                request_id=request_id,
            )

    stream_task = asyncio.create_task(consume())
    cancel_task = asyncio.create_task(cancellation_requested.wait())
    outcome: RequestOutcome
    drain_expired = False
    try:
        done, _ = await asyncio.wait(
            {stream_task, cancel_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stream_task in done:
            await stream_task
            if observed_terminal is StreamEventType.COMPLETED:
                outcome = RequestOutcome.SUCCEEDED
            elif observed_terminal is StreamEventType.DISCONNECTED:
                outcome = RequestOutcome.DISCONNECTED
            elif observed_terminal is StreamEventType.TIMEOUT:
                outcome = RequestOutcome.TIMED_OUT
            elif observed_terminal is StreamEventType.CANCELLED:
                outcome = RequestOutcome.CANCELLED
            else:
                outcome = RequestOutcome.FAILED
                failed_category = failed_category or "adapter_error"
        elif cancel_task in done:
            await abort_and_drain()
            try:
                await asyncio.wait_for(asyncio.shield(stream_task), timeout=CANCEL_DRAIN_SECONDS)
            except TimeoutError:
                stream_task.cancel()
                await asyncio.wait({stream_task}, timeout=0.1)
                drain_expired = not stream_task.done()
                if not drain_expired:
                    await asyncio.gather(stream_task, return_exceptions=True)
            outcome = RequestOutcome.CANCELLED
        else:
            await abort_and_drain()
            try:
                await asyncio.wait_for(asyncio.shield(stream_task), timeout=CANCEL_DRAIN_SECONDS)
            except TimeoutError:
                stream_task.cancel()
                await asyncio.wait({stream_task}, timeout=0.1)
                drain_expired = not stream_task.done()
                if not drain_expired:
                    await asyncio.gather(stream_task, return_exceptions=True)
            outcome = RequestOutcome.TIMED_OUT
    except (Exception, asyncio.CancelledError):
        if cancellation_requested.is_set():
            outcome = RequestOutcome.CANCELLED
        else:
            outcome = RequestOutcome.FAILED
            failed_category = "adapter_error"
    finally:
        cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)

    wall_completed = datetime.now(UTC)
    duration_ms = max(0.0, (time.perf_counter() - monotonic_started) * 1000)
    if outcome is RequestOutcome.SUCCEEDED:
        counts["succeeded"] += 1
        emit_run_event(
            connection,
            run_id,
            "request_completed",
            {"duration_ms": duration_ms, "emitted_characters": output_characters},
            request_id=request_id,
        )
    elif outcome is RequestOutcome.CANCELLED:
        counts["cancelled"] += 1
        emit_run_event(
            connection,
            run_id,
            "request_cancelled",
            {"duration_ms": duration_ms},
            request_id=request_id,
        )
    else:
        counts["failed"] += 1
        if outcome is RequestOutcome.TIMED_OUT:
            counts["timed_out"] += 1
        emit_run_event(
            connection,
            run_id,
            "request_failed",
            {
                "category": failed_category or outcome.value,
                "duration_ms": duration_ms,
            },
            request_id=request_id,
        )

    if phase == "measurement":
        sample = RequestSample(
            run_id=run_id,
            sequence_number=request_index,
            outcome=outcome,
            started_at=wall_started,
            completed_at=wall_completed,
            latency_ms=duration_ms,
            ttft_ms=(
                max(0.0, (first_output_at - monotonic_started) * 1000)
                if first_output_at is not None
                else None
            ),
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            redacted_summary=("request bodies retained by plan opt-in" if retain_content else None),
            content_retained=retain_content,
            prompt_text=prompt if retain_content else None,
            output_text="".join(output_parts) if retain_content else None,
        )
        RequestSampleRepository(connection).create(sample, allow_content_retention=retain_content)
    if drain_expired:
        raise AdapterDrainTimeoutError("adapter request did not drain")
    return outcome


async def execute_run(
    *,
    run_id: UUID,
    database_path: Path,
    data_directory: Path,
    app_instance_id: str,
    worker_id: str,
    catalog_factory: Callable[[], AdapterCatalog] = default_adapter_catalog,
) -> int:
    connection = open_database(database_path)
    catalog = catalog_factory()
    cancellation_requested = asyncio.Event()
    monitor_finished = asyncio.Event()
    try:
        initial = RecordRepository(connection, BenchmarkRun).get(run_id)
        if initial is None or initial.worker_id != worker_id:
            return 2
        if initial.status is BenchmarkRunStatus.CANCELLING:
            _finish_stop_request(connection, initial)
            return 0
        if initial.status not in ACTIVE_RUN_STATUSES:
            return 0

        heartbeat_worker(
            connection,
            run_id=run_id,
            app_instance_id=app_instance_id,
            worker_id=worker_id,
            process_id=os.getpid(),
        )

        async def watch_run() -> None:
            next_heartbeat = asyncio.get_running_loop().time()
            while not monitor_finished.is_set():
                await asyncio.sleep(CONTROL_POLL_INTERVAL_SECONDS)
                if asyncio.get_running_loop().time() >= next_heartbeat:
                    heartbeat_worker(
                        connection,
                        run_id=run_id,
                        app_instance_id=app_instance_id,
                        worker_id=worker_id,
                        process_id=os.getpid(),
                    )
                    next_heartbeat = asyncio.get_running_loop().time() + HEARTBEAT_INTERVAL_SECONDS
                current = RecordRepository(connection, BenchmarkRun).get(run_id)
                if current is None or current.status is BenchmarkRunStatus.CANCELLING:
                    cancellation_requested.set()
                    return
                if current.status in {
                    BenchmarkRunStatus.CANCELLED,
                    BenchmarkRunStatus.FAILED,
                    BenchmarkRunStatus.INTERRUPTED,
                    BenchmarkRunStatus.COMPLETED,
                }:
                    cancellation_requested.set()
                    return

        monitor = asyncio.create_task(watch_run())
        try:
            transition_run(connection, run_id, BenchmarkRunStatus.PREPARING)
            run, plan, workload, engine, model_ref, prompt_for = _load_run_context(
                connection, run_id, data_directory
            )
            config = _adapter_config(engine)
            validation = await asyncio.wait_for(
                catalog.validate_config(engine.adapter_id, config), timeout=plan.timeout_seconds
            )
            if not validation.valid:
                raise RunSetupError(
                    "adapter_config_invalid", "The adapter rejected the saved engine configuration."
                )

            sweep_levels = (
                workload.concurrency_levels
                if workload.preset is WorkloadPreset.CONCURRENCY_SWEEP
                else [plan.concurrency]
            )
            if plan.warmup_requests > 0:
                transition_run(connection, run_id, BenchmarkRunStatus.WARMING)
                warmup_counts: Counter[str] = Counter()
                warmup_sequence = 0
                for _level in sweep_levels:
                    for index in range(plan.warmup_requests):
                        if cancellation_requested.is_set():
                            break
                        outcome = await _execute_request(
                            connection=connection,
                            catalog=catalog,
                            adapter_id=engine.adapter_id,
                            config=config,
                            model_ref=model_ref,
                            prompt=prompt_for(index),
                            parameters=workload.sampling_config
                            | {
                                "max_tokens": workload.target_output_tokens
                                or workload.max_output_tokens
                            },
                            run_id=run_id,
                            request_index=warmup_sequence,
                            phase="warmup",
                            concurrency_level=_level,
                            timeout_seconds=plan.timeout_seconds,
                            cancellation_requested=cancellation_requested,
                            counts=warmup_counts,
                            retain_content=False,
                        )
                        warmup_sequence += 1
                        if cancellation_requested.is_set():
                            break
                        if outcome is not RequestOutcome.SUCCEEDED:
                            raise RunSetupError(
                                "warmup_failed",
                                "A warmup request did not complete successfully.",
                            )
                    if cancellation_requested.is_set():
                        break

            if cancellation_requested.is_set():
                current = RecordRepository(connection, BenchmarkRun).get(run_id)
                if current is not None and current.status is BenchmarkRunStatus.CANCELLING:
                    _finish_stop_request(connection, current)
                return 0

            transition_run(connection, run_id, BenchmarkRunStatus.RUNNING)
            counts: Counter[str] = Counter()
            emit_run_event(
                connection,
                run_id,
                "system_sample",
                {
                    "scope": "benchmark_worker",
                    "process_id": os.getpid(),
                    "available": False,
                    "reason": "host_sampler_not_configured",
                },
            )
            request_limit = plan.measured_requests
            if workload.request_count is not None:
                request_limit = (
                    min(request_limit, workload.request_count)
                    if request_limit is not None
                    else workload.request_count
                )
            duration_limit = plan.measurement_duration_seconds
            if workload.duration_seconds is not None:
                duration_limit = (
                    min(duration_limit, workload.duration_seconds)
                    if duration_limit is not None
                    else workload.duration_seconds
                )
            if request_limit is None and duration_limit is None:
                raise RunSetupError(
                    "measurement_limit_missing", "The run has no bounded measurement limit."
                )

            measured_sequence = 0
            for level in sweep_levels:
                concurrency = min(level, MAX_WORKER_CONCURRENCY)
                if level > concurrency:
                    emit_run_event(
                        connection,
                        run_id,
                        "warning",
                        {
                            "code": "worker_concurrency_limited",
                            "message": (
                                f"This worker supports at most {MAX_WORKER_CONCURRENCY} "
                                "simultaneous requests."
                            ),
                            "requested": level,
                            "applied": concurrency,
                        },
                    )
                measured_start = time.perf_counter()
                next_index = 0
                index_lock = asyncio.Lock()
                sequence_base = measured_sequence

                async def next_request_index(
                    lock: asyncio.Lock = index_lock,
                    started: float = measured_start,
                ) -> int | None:
                    nonlocal next_index
                    async with lock:
                        if cancellation_requested.is_set():
                            return None
                        if request_limit is not None and next_index >= request_limit:
                            return None
                        if (
                            duration_limit is not None
                            and time.perf_counter() - started >= duration_limit
                        ):
                            return None
                        index = next_index
                        next_index += 1
                        return index

                async def request_loop(
                    started: float = measured_start,
                    sequence: int = sequence_base,
                    concurrency_level: int = level,
                ) -> None:
                    while True:
                        index = await next_request_index()
                        if index is None:
                            return
                        if (
                            workload.traffic_mode.value == "open_loop"
                            and workload.arrival_rate_per_second
                        ):
                            target_time = started + index / workload.arrival_rate_per_second
                            delay = target_time - time.perf_counter()
                            if delay > 0:
                                try:
                                    await asyncio.wait_for(
                                        cancellation_requested.wait(), timeout=delay
                                    )
                                    return
                                except TimeoutError:
                                    pass
                        if (
                            duration_limit is not None
                            and time.perf_counter() - started >= duration_limit
                        ):
                            return
                        await _execute_request(
                            connection=connection,
                            catalog=catalog,
                            adapter_id=engine.adapter_id,
                            config=config,
                            model_ref=model_ref,
                            prompt=prompt_for(index),
                            parameters=workload.sampling_config
                            | {
                                "max_tokens": workload.target_output_tokens
                                or workload.max_output_tokens
                            },
                            run_id=run_id,
                            request_index=sequence + index,
                            phase="measurement",
                            concurrency_level=concurrency_level,
                            timeout_seconds=plan.timeout_seconds,
                            cancellation_requested=cancellation_requested,
                            counts=counts,
                            retain_content=plan.retain_prompt_output,
                        )

                await asyncio.gather(*(request_loop() for _ in range(concurrency)))
                measured_sequence += next_index
                if cancellation_requested.is_set():
                    break
            current = RecordRepository(connection, BenchmarkRun).get(run_id)
            if current is None:
                return 2
            if cancellation_requested.is_set() or current.status is BenchmarkRunStatus.CANCELLING:
                if current.status is BenchmarkRunStatus.CANCELLING:
                    _finish_stop_request(connection, current)
                return 0
            for name in ("succeeded", "failed", "timed_out", "cancelled"):
                emit_run_event(
                    connection,
                    run_id,
                    "run_metric",
                    {
                        "name": f"requests_{name}",
                        "value": counts[name],
                        "unit": "requests",
                        "source": "client_measured",
                    },
                )
            final = (
                BenchmarkRunStatus.FAILED
                if counts["succeeded"] == 0 and counts["failed"] > 0
                else BenchmarkRunStatus.COMPLETED
            )
            transition_run(connection, run_id, final)
        except AdapterDrainTimeoutError:
            current = RecordRepository(connection, BenchmarkRun).get(run_id)
            if current is not None and current.status is BenchmarkRunStatus.CANCELLING:
                _finish_stop_request(connection, current)
            elif current is not None and current.status in ACTIVE_RUN_STATUSES:
                transition_run(
                    connection,
                    run_id,
                    BenchmarkRunStatus.FAILED,
                    error_code="adapter_drain_timeout",
                    error_message=(
                        "An engine request did not stop within the configured drain bound."
                    ),
                )
            return 1
        except RunSetupError as error:
            current = RecordRepository(connection, BenchmarkRun).get(run_id)
            if current is not None and current.status is BenchmarkRunStatus.CANCELLING:
                _finish_stop_request(connection, current)
            elif current is not None and current.status in ACTIVE_RUN_STATUSES:
                transition_run(
                    connection,
                    run_id,
                    BenchmarkRunStatus.FAILED,
                    error_code=error.code,
                    error_message=str(error),
                )
            return 1
        except AdapterNotFoundError:
            current = RecordRepository(connection, BenchmarkRun).get(run_id)
            if current is not None and current.status is BenchmarkRunStatus.CANCELLING:
                _finish_stop_request(connection, current)
            elif current is not None and current.status in ACTIVE_RUN_STATUSES:
                transition_run(
                    connection,
                    run_id,
                    BenchmarkRunStatus.FAILED,
                    error_code="adapter_unavailable",
                    error_message="The selected adapter is not available in the worker process.",
                )
            return 1
        except Exception:
            current = RecordRepository(connection, BenchmarkRun).get(run_id)
            if current is not None and current.status is BenchmarkRunStatus.CANCELLING:
                _finish_stop_request(connection, current)
            elif current is not None and current.status in ACTIVE_RUN_STATUSES:
                transition_run(
                    connection,
                    run_id,
                    BenchmarkRunStatus.FAILED,
                    error_code="worker_execution_failed",
                    error_message="The benchmark worker could not finish this run.",
                )
            return 1
        finally:
            monitor_finished.set()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        return 0
    finally:
        await catalog.close()
        connection.close()
