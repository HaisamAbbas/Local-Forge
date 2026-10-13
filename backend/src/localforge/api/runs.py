"""Run coordination routes and persisted SSE event delivery."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from localforge.api.dependencies import DatabaseConnection
from localforge.api.schemas import (
    RunEventPage,
    RunMetricsResponse,
    RunSamplesResponse,
    RunStartRequest,
    RunStartResponse,
)
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkPlanStatus,
    BenchmarkRun,
    BenchmarkRunStatus,
    CapabilitySnapshot,
    EngineIntegrationMode,
    EngineProfile,
    JsonValue,
    MachineProfile,
    ModelArtifact,
)
from localforge.core.urls import endpoint_is_remote
from localforge.runs import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    InvalidRunTransition,
    ResourceLockConflict,
    create_queued_run,
    finalize_request_metrics,
    list_run_events,
    reconcile_stale_workers,
    transition_run,
)
from localforge.storage.database import open_database
from localforge.storage.repositories import (
    MetricSampleRepository,
    RecordRepository,
    RequestSampleRepository,
)

router = APIRouter(prefix="/api/v1")
WORKER_SHUTDOWN_GRACE_SECONDS = 3.0
WORKER_STALE_SECONDS = 15
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "benchmark_run_not_found", "message": "The benchmark run was not found."},
    )


def _machine_resource_keys(
    connection: sqlite3.Connection,
    machine_id: UUID | None,
) -> list[tuple[str, str]]:
    machine = (
        RecordRepository(connection, MachineProfile).get(machine_id)
        if machine_id is not None
        else None
    )
    accelerators = (
        machine.hardware.accelerators
        if machine is not None and machine.hardware is not None
        else None
    )
    host_name = machine.host_name if machine is not None else None
    machine_key = (
        hashlib.sha256(host_name.casefold().encode("utf-8")).hexdigest()[:16]
        if host_name
        else "local-machine"
    )
    if accelerators:
        return [
            ("accelerator", f"{machine_key}:index:{index}")
            for index, _accelerator in enumerate(accelerators)
        ]
    return [("cpu_group", f"{machine_key}:cpu")]


def _resource_keys(
    connection: sqlite3.Connection,
    plan: BenchmarkPlan,
    engine: EngineProfile,
) -> list[tuple[str, str]]:
    if engine.integration_mode is EngineIntegrationMode.ATTACHED_ENDPOINT:
        endpoint_resources = [("endpoint", str(engine.id))]
        if endpoint_is_remote(engine.endpoint_url):
            return endpoint_resources
        machine_id = plan.machine_profile_id or engine.machine_profile_id
        return endpoint_resources + _machine_resource_keys(connection, machine_id)
    if engine.integration_mode is EngineIntegrationMode.COMPOSED_SERVING_STACK:
        machine_id = plan.machine_profile_id or engine.machine_profile_id
        return [("serving_stack", str(engine.id))] + _machine_resource_keys(connection, machine_id)

    machine_id = plan.machine_profile_id or engine.machine_profile_id
    return _machine_resource_keys(connection, machine_id)


def _run_profile_snapshot(
    connection: sqlite3.Connection,
    plan: BenchmarkPlan,
    engine: EngineProfile,
) -> dict[str, JsonValue]:
    """Capture only non-secret engine and machine provenance for the run record."""

    machine_id = plan.machine_profile_id or engine.machine_profile_id
    machine = (
        RecordRepository(connection, MachineProfile).get(machine_id)
        if machine_id is not None
        else None
    )
    model = (
        RecordRepository(connection, ModelArtifact).get(plan.model_artifact_id)
        if plan.model_artifact_id is not None
        else None
    )
    capability_snapshot = (
        RecordRepository(connection, CapabilitySnapshot).get(engine.capability_snapshot_id)
        if engine.capability_snapshot_id is not None
        else None
    )
    capabilities = None
    if capability_snapshot is not None:
        capabilities = {
            name: {"state": value.state.value, "details": None, "source": value.source}
            for name, value in capability_snapshot.capabilities.items()
        }
    return cast(
        dict[str, JsonValue],
        {
            "captured_at": datetime.now(UTC).isoformat(),
            "machine_profile_id": str(machine_id) if machine_id is not None else None,
            "machine": (
                {
                    "id": str(machine.id),
                    "label": machine.label,
                    "status": machine.status.value,
                    "hardware": (
                        machine.hardware.model_dump(mode="json")
                        if machine.hardware is not None
                        else None
                    ),
                }
                if machine is not None
                else None
            ),
            "engine": {
                "id": str(engine.id),
                "label": engine.label,
                "adapter_id": engine.adapter_id,
                "engine_display_name": engine.engine_display_name,
                "adapter_version": engine.adapter_version,
                "engine_version": engine.engine_version,
                "integration_mode": engine.integration_mode.value,
                "capability_snapshot_id": (
                    str(engine.capability_snapshot_id)
                    if engine.capability_snapshot_id is not None
                    else None
                ),
                "capabilities": capabilities,
                "selected_model_ref": engine.selected_model_ref,
            },
            "model_artifact": (
                {
                    "id": str(model.id),
                    "display_name": model.display_name,
                    "engine_profile_id": (
                        str(model.engine_profile_id)
                        if model.engine_profile_id is not None
                        else None
                    ),
                    "model_ref": model.model_ref,
                    "revision": model.revision,
                    "format": model.format,
                    "size_bytes": model.size_bytes,
                    "tokenizer_identity": model.tokenizer_identity,
                    "max_context_tokens": model.max_context_tokens,
                    "engine_extensions": model.engine_extensions,
                }
                if model is not None
                else None
            ),
        },
    )


def _launch_worker(
    request: Request,
    run: BenchmarkRun,
    *,
    app_instance_id: str,
) -> subprocess.Popen[bytes]:
    backend_directory = Path(__file__).resolve().parents[3]
    source_directory = backend_directory / "src"
    environment = os.environ.copy()
    inherited_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(source_directory)
        if not inherited_path
        else str(source_directory) + os.pathsep + inherited_path
    )
    command = [
        sys.executable,
        "-m",
        "localforge.worker",
        "--run-id",
        str(run.id),
        "--database",
        str(request.app.state.database_path),
        "--data-directory",
        str(request.app.state.data_directory),
        "--app-instance-id",
        app_instance_id,
        "--worker-id",
        cast(str, run.worker_id),
    ]
    return subprocess.Popen(
        command,
        cwd=backend_directory,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@router.post("/runs", response_model=RunStartResponse, status_code=202, tags=["runs"])
def start_run(
    body: RunStartRequest,
    request: Request,
    connection: DatabaseConnection,
) -> RunStartResponse:
    reconcile_stale_workers(connection, stale_after_seconds=WORKER_STALE_SECONDS)
    plan = RecordRepository(connection, BenchmarkPlan).get(body.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "benchmark_plan_not_found",
                "message": "The benchmark plan was not found.",
            },
        )
    if plan.status is not BenchmarkPlanStatus.READY:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "benchmark_plan_not_ready",
                "message": "Resolve plan blockers before starting a run.",
            },
        )
    engine = RecordRepository(connection, EngineProfile).get(plan.engine_profile_id)
    if engine is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "engine_profile_missing",
                "message": "The plan's engine profile no longer exists.",
            },
        )

    resources = _resource_keys(connection, plan, engine)
    app_instance_id = cast(str, request.app.state.app_instance_id)
    worker_id = str(uuid4())
    run = BenchmarkRun(
        plan_id=plan.id,
        worker_id=worker_id,
        profile_snapshot=_run_profile_snapshot(connection, plan, engine),
    )
    try:
        run, conflicting_resources = create_queued_run(
            connection,
            run,
            app_instance_id=app_instance_id,
            resource_keys=resources,
            override_contention=body.override_contention,
        )
    except ResourceLockConflict as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "resource_lock_conflict",
                "message": (
                    "Another benchmark is using the same target. Retry with "
                    "override_contention=true to continue with a contention warning."
                ),
                "conflicting_resources": error.resources,
            },
        ) from error
    try:
        process = _launch_worker(request, run, app_instance_id=app_instance_id)
        processes = cast(dict[str, subprocess.Popen[bytes]], request.app.state.run_worker_processes)
        with request.app.state.run_worker_processes_lock:
            processes[str(run.id)] = process
        from localforge.runs import heartbeat_worker

        heartbeat_worker(
            connection,
            run_id=run.id,
            app_instance_id=app_instance_id,
            worker_id=worker_id,
            process_id=process.pid,
        )
    except OSError as error:
        failed = transition_run(
            connection,
            run.id,
            BenchmarkRunStatus.FAILED,
            error_code="worker_launch_failed",
            error_message="LOCALFORGE could not start the isolated benchmark worker.",
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "worker_launch_failed",
                "message": "LOCALFORGE could not start the benchmark worker.",
                "run_id": str(failed.id),
            },
        ) from error
    saved = RecordRepository(connection, BenchmarkRun).get(run.id)
    return RunStartResponse(run=saved or run, conflicting_resources=conflicting_resources)


@router.get("/runs", response_model=list[BenchmarkRun], tags=["runs"])
def list_runs(
    connection: DatabaseConnection,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[BenchmarkRun]:
    rows = connection.execute(
        """SELECT payload_json FROM domain_records
           WHERE entity_type = 'BenchmarkRun' ORDER BY created_at DESC, id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [BenchmarkRun.model_validate(json.loads(row["payload_json"])) for row in rows]


@router.get("/runs/{run_id}", response_model=BenchmarkRun, tags=["runs"])
def get_run(run_id: UUID, connection: DatabaseConnection) -> BenchmarkRun:
    run = RecordRepository(connection, BenchmarkRun).get(run_id)
    if run is None:
        raise _not_found()
    return run


@router.post("/runs/{run_id}/cancel", response_model=BenchmarkRun, tags=["runs"])
def cancel_run(run_id: UUID, connection: DatabaseConnection) -> BenchmarkRun:
    run = RecordRepository(connection, BenchmarkRun).get(run_id)
    if run is None:
        raise _not_found()
    if run.status in ACTIVE_RUN_STATUSES and run.status is not BenchmarkRunStatus.CANCELLING:
        try:
            return transition_run(connection, run_id, BenchmarkRunStatus.CANCELLING)
        except InvalidRunTransition:
            latest = RecordRepository(connection, BenchmarkRun).get(run_id)
            if latest is not None and (
                latest.status in TERMINAL_RUN_STATUSES
                or latest.status is BenchmarkRunStatus.CANCELLING
            ):
                return latest
            raise
    return run


@router.get("/runs/{run_id}/samples", response_model=RunSamplesResponse, tags=["runs"])
def get_run_samples(
    run_id: UUID,
    connection: DatabaseConnection,
    after: int = Query(default=-1, ge=-1),
    limit: int = Query(default=500, ge=1, le=1000),
    incomplete_only: bool = Query(default=False),
) -> RunSamplesResponse:
    if RecordRepository(connection, BenchmarkRun).get(run_id) is None:
        raise _not_found()
    samples = RequestSampleRepository(connection).list_for_run(
        run_id,
        after_sequence_number=after,
        limit=limit,
        incomplete_only=incomplete_only,
    )
    return RunSamplesResponse(
        samples=samples,
        next_sequence_number=samples[-1].sequence_number if samples else None,
    )


@router.get("/runs/{run_id}/metrics", response_model=RunMetricsResponse, tags=["runs"])
def get_run_metrics(run_id: UUID, connection: DatabaseConnection) -> RunMetricsResponse:
    if RecordRepository(connection, BenchmarkRun).get(run_id) is None:
        raise _not_found()
    metrics = MetricSampleRepository(connection).list_for_run(run_id)
    return RunMetricsResponse(metrics=metrics)


@router.get("/runs/{run_id}/events", response_model=RunEventPage, tags=["runs"])
def get_run_events(
    run_id: UUID,
    connection: DatabaseConnection,
    after: int = Query(default=-1, ge=-1),
    limit: int = Query(default=500, ge=1, le=1000),
) -> RunEventPage:
    run = RecordRepository(connection, BenchmarkRun).get(run_id)
    if run is None:
        raise _not_found()
    events = list_run_events(connection, run_id, after=after, limit=limit)
    return RunEventPage(
        events=events,
        next_sequence_number=events[-1].sequence_number if events else None,
        terminal=run.status in TERMINAL_RUN_STATUSES,
    )


def _event_cursor(header: str | None, run_id: UUID, query_after: int) -> int:
    if not header:
        return query_after
    if ":" not in header:
        try:
            return max(query_after, int(header))
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Last-Event-ID header.") from error
    event_run, raw_sequence = header.rsplit(":", 1)
    try:
        if UUID(event_run) != run_id:
            raise ValueError
        return max(query_after, int(raw_sequence))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid Last-Event-ID header.") from error


@router.get("/runs/{run_id}/events/stream", tags=["runs"])
async def stream_run_events(
    run_id: UUID,
    request: Request,
    connection: DatabaseConnection,
    after: int = Query(default=-1, ge=-1),
) -> StreamingResponse:
    run = RecordRepository(connection, BenchmarkRun).get(run_id)
    if run is None:
        raise _not_found()
    last_sequence = _event_cursor(request.headers.get("last-event-id"), run_id, after)

    async def event_stream() -> AsyncIterator[str]:
        nonlocal last_sequence
        last_heartbeat = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            events = list_run_events(connection, run_id, after=last_sequence, limit=200)
            for event in events:
                data = json.dumps(
                    event.model_dump(mode="json"), ensure_ascii=False, allow_nan=False
                )
                yield (
                    f"id: {run_id}:{event.sequence_number}\nevent: {event.event_type}\n"
                    f"data: {data}\n\n"
                )
                last_sequence = event.sequence_number
            current = RecordRepository(connection, BenchmarkRun).get(run_id)
            if current is None or (current.status in TERMINAL_RUN_STATUSES and not events):
                break
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                heartbeat = json.dumps(
                    {
                        "run_id": str(run_id),
                        "type": "heartbeat",
                        "occurred_at": datetime.now(UTC).isoformat(),
                    },
                    separators=(",", ":"),
                )
                yield f"event: heartbeat\ndata: {heartbeat}\n\n"
                last_heartbeat = now
            if not events:
                await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def monitor_run_workers(application: FastAPI) -> None:
    """Reconcile stale worker leases and detect child processes that exited unexpectedly."""

    while True:
        await asyncio.sleep(0.5)
        database_path = cast(Path, application.state.database_path)
        connection = open_database(database_path)
        try:
            reconcile_stale_workers(connection, stale_after_seconds=WORKER_STALE_SECONDS)
            processes = cast(
                dict[str, subprocess.Popen[bytes]], application.state.run_worker_processes
            )
            with application.state.run_worker_processes_lock:
                process_items = list(processes.items())
            for run_key, process in process_items:
                exit_code = process.poll()
                run_id = UUID(run_key)
                run = RecordRepository(connection, BenchmarkRun).get(run_id)
                if run is not None and run.status in ACTIVE_RUN_STATUSES:
                    lease = connection.execute(
                        "SELECT heartbeat_at FROM worker_leases WHERE run_id = ?", (run_key,)
                    ).fetchone()
                    stale = lease is None or datetime.fromisoformat(lease[0]) < (
                        datetime.now(UTC) - timedelta(seconds=WORKER_STALE_SECONDS)
                    )
                    if exit_code is not None or stale:
                        finalize_request_metrics(connection, run_id)
                        transition_run(
                            connection,
                            run_id,
                            BenchmarkRunStatus.INTERRUPTED,
                            error_code="worker_exited"
                            if exit_code is not None
                            else "worker_lease_expired",
                            error_message=(
                                "The isolated benchmark worker stopped before the run finished."
                            ),
                        )
                        run = RecordRepository(connection, BenchmarkRun).get(run_id)
                if (
                    run is not None
                    and run.status is BenchmarkRunStatus.INTERRUPTED
                    and run.error_code == "worker_lease_expired"
                    and process.poll() is None
                ):
                    process.terminate()
                    try:
                        await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=1.0)
                    except TimeoutError:
                        process.kill()
                        await asyncio.to_thread(process.wait)
                    exit_code = process.returncode
                if (
                    run is not None
                    and run.status in TERMINAL_RUN_STATUSES
                    and process.poll() is None
                    and datetime.now(UTC) - run.updated_at
                    > timedelta(seconds=WORKER_SHUTDOWN_GRACE_SECONDS)
                ):
                    process.terminate()
                    try:
                        await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=1.0)
                    except TimeoutError:
                        process.kill()
                        await asyncio.to_thread(process.wait)
                    exit_code = process.returncode
                if exit_code is not None:
                    with application.state.run_worker_processes_lock:
                        if processes.get(run_key) is process:
                            processes.pop(run_key, None)
        finally:
            connection.close()


async def shutdown_run_workers(application: FastAPI) -> None:
    """Ask owned workers to stop, then terminate only child Popen handles owned here."""

    processes = cast(dict[str, subprocess.Popen[bytes]], application.state.run_worker_processes)
    with application.state.run_worker_processes_lock:
        process_items = list(processes.items())
    database_path = cast(Path, application.state.database_path)
    connection = open_database(database_path)
    try:
        for run_key, _process in process_items:
            run_id = UUID(run_key)
            run = RecordRepository(connection, BenchmarkRun).get(run_id)
            if run is not None and run.status in ACTIVE_RUN_STATUSES:
                if run.status is not BenchmarkRunStatus.CANCELLING:
                    transition_run(
                        connection,
                        run_id,
                        BenchmarkRunStatus.CANCELLING,
                        error_code="app_shutdown",
                        error_message="The app is stopping this worker before shutdown.",
                    )
    finally:
        connection.close()

    deadline = asyncio.get_running_loop().time() + WORKER_SHUTDOWN_GRACE_SECONDS
    while asyncio.get_running_loop().time() < deadline and any(
        process.poll() is None for _, process in process_items
    ):
        await asyncio.sleep(0.05)
    for run_key, process in process_items:
        if process.poll() is None:
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=1.0)
            except TimeoutError:
                process.kill()
                await asyncio.to_thread(process.wait)
        connection = open_database(database_path)
        try:
            run = RecordRepository(connection, BenchmarkRun).get(UUID(run_key))
            if run is not None and run.status in ACTIVE_RUN_STATUSES:
                finalize_request_metrics(connection, run.id)
                transition_run(
                    connection,
                    run.id,
                    BenchmarkRunStatus.INTERRUPTED,
                    error_code="worker_stopped_on_app_shutdown",
                    error_message="The app stopped its owned worker before the run finished.",
                )
        finally:
            connection.close()
        with application.state.run_worker_processes_lock:
            if processes.get(run_key) is process:
                processes.pop(run_key, None)
