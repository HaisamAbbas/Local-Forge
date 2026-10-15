"""Tune the launch settings of a managed llama.cpp profile from the browser.

A tune runs in a private, throwaway LocalForge API (as ``localforge tune`` does), on its own free
port, so it never touches your saved runs, engines or the engine port. The job lives in this
server process memory: it is listed until the server restarts. Applying the result is a separate,
explicit edit of the profile.
"""

import copy
import socket
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel

from localforge.api.dependencies import DatabaseConnection
from localforge.core.models import EngineIntegrationMode, EngineProfile, utc_now
from localforge.headless import ApiClient, BenchError, PlanError, private_server
from localforge.security.local_session import is_loopback_request_host
from localforge.storage.repositories import RecordRepository
from localforge.tune import (
    LLAMA_CPP_ADAPTER,
    OBJECTIVES,
    TuneCancelled,
    build_result,
    coordinate_search,
    make_evaluator,
    parse_tune_plan,
    search_space_for,
    trial_count,
)

router = APIRouter(prefix="/api/v1")
MAX_KEPT_JOBS = 10

TuneStatus = Literal["running", "completed", "failed", "cancelled"]


class TuneRequest(BaseModel):
    engine_profile_id: UUID
    objective: Literal["throughput", "latency", "ttft"] = "throughput"


class TuneTrialView(BaseModel):
    settings: dict[str, Any]
    changed: str | None
    score: float | None
    tokens_per_second: float | None = None
    ttft_ms: float | None = None
    latency_ms: float | None = None
    error: str | None


class TuneJobView(BaseModel):
    id: UUID
    engine_profile_id: UUID
    engine_label: str
    objective: str
    objective_description: str
    status: TuneStatus
    started_at: datetime
    finished_at: datetime | None
    total_trials: int
    trials: list[TuneTrialView]
    min_gain_percent: float
    baseline_score: float | None
    best_score: float | None
    improvement_percent: float | None
    best_settings: dict[str, Any]
    recommended_launch_settings: dict[str, Any] | None
    error: str | None
    stopping: bool


@dataclass
class _Job:
    id: UUID
    profile_id: UUID
    label: str
    objective: str
    description: str
    total_trials: int
    min_gain_percent: float
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: TuneStatus = "running"
    trials: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def view(self) -> TuneJobView:
        with self.lock:
            result = self.result or {}
            return TuneJobView(
                id=self.id,
                engine_profile_id=self.profile_id,
                engine_label=self.label,
                objective=self.objective,
                objective_description=self.description,
                status=self.status,
                started_at=self.started_at,
                finished_at=self.finished_at,
                total_trials=self.total_trials,
                trials=[TuneTrialView.model_validate(item) for item in self.trials],
                min_gain_percent=self.min_gain_percent,
                baseline_score=result.get("baseline_score"),
                best_score=result.get("best_score"),
                improvement_percent=result.get("improvement_percent"),
                best_settings=result.get("best_settings", {}),
                recommended_launch_settings=result.get("recommended_launch_settings"),
                error=self.error,
                stopping=self.status == "running" and self.stop.is_set(),
            )


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _jobs(app: FastAPI) -> dict[UUID, _Job]:
    jobs = getattr(app.state, "tune_jobs", None)
    if jobs is None:
        jobs = {}
        app.state.tune_jobs = jobs
    return cast(dict[UUID, _Job], jobs)


def _require_loopback(request: Request) -> None:
    if not is_loopback_request_host(request.url.hostname):
        raise _error(403, "loopback_only", "Tuning is available only through a loopback host.")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _plan_for(profile: EngineProfile, objective: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The bench plan and tune options for a saved managed llama.cpp profile."""

    if (
        profile.adapter_id != LLAMA_CPP_ADAPTER
        or profile.integration_mode is not EngineIntegrationMode.MANAGED_PROCESS
    ):
        raise _error(
            422,
            "tune_unsupported",
            "Only a managed llama.cpp engine can be tuned: LocalForge has to restart it with "
            "each setting.",
        )
    extension = dict(profile.engine_extensions.get("llama_cpp", {}))
    # The tune runs on its own port so the saved engine is never in the way.
    raw = {
        "label": f"tune {profile.label}"[:120],
        "engine": {
            "adapter": LLAMA_CPP_ADAPTER,
            "mode": "managed_process",
            "endpoint": f"http://127.0.0.1:{_free_port()}",
            "extensions": {"llama_cpp": copy.deepcopy(extension)},
        },
        "tune": {"objective": objective},
    }
    try:
        return parse_tune_plan(raw)
    except PlanError as error:
        raise _error(422, "tune_plan_invalid", str(error)) from error


def _run(job: _Job, plan: dict[str, Any], options: dict[str, Any]) -> None:
    def record(entry: dict[str, Any]) -> None:
        with job.lock:
            job.trials.append(entry)

    try:
        space = search_space_for(plan, options["space"])
        with tempfile.TemporaryDirectory(prefix="localforge-tune-") as directory:
            with private_server(Path(directory)) as api:
                client = ApiClient(api)
                search = coordinate_search(
                    space,
                    make_evaluator(client, plan),
                    objective=options["objective"],
                    min_gain_percent=options["min_gain_percent"],
                    on_trial=record,
                    should_stop=job.stop.is_set,
                )
        result = build_result(plan, options, search)
        with job.lock:
            job.result = result
            job.error = result["error"]
            job.status = "failed" if result["error"] else "completed"
    except TuneCancelled:
        with job.lock:
            job.status = "cancelled"
    except (BenchError, OSError, RuntimeError) as error:
        with job.lock:
            job.status = "failed"
            job.error = str(error)
    except Exception as error:  # noqa: BLE001 - a background job must always end in a state
        with job.lock:
            job.status = "failed"
            job.error = f"The tune stopped unexpectedly: {error}"
    finally:
        with job.lock:
            job.finished_at = utc_now()


@router.post("/tunes", response_model=TuneJobView, status_code=202, tags=["engines"])
def start_tune(body: TuneRequest, request: Request, connection: DatabaseConnection) -> TuneJobView:
    """Start tuning one managed llama.cpp profile; benchmarks run for several minutes."""

    _require_loopback(request)
    profile = RecordRepository(connection, EngineProfile).get(body.engine_profile_id)
    if profile is None:
        raise _error(404, "engine_profile_not_found", "The engine profile was not found.")
    jobs = _jobs(request.app)
    if any(job.status == "running" for job in jobs.values()):
        raise _error(
            409, "tune_running", "A tune is already running. Wait for it or stop it first."
        )
    plan, options = _plan_for(profile, body.objective)
    space = search_space_for(plan, options["space"])
    job = _Job(
        id=uuid4(),
        profile_id=profile.id,
        label=profile.label,
        objective=options["objective"],
        description=OBJECTIVES[options["objective"]][2],
        total_trials=trial_count(space),
        min_gain_percent=options["min_gain_percent"],
    )
    jobs[job.id] = job
    for old in sorted(
        (item for item in jobs.values() if item.status != "running"),
        key=lambda item: item.started_at,
    )[: max(0, len(jobs) - MAX_KEPT_JOBS)]:
        del jobs[old.id]
    threading.Thread(target=_run, args=(job, plan, options), daemon=True).start()
    return job.view()


@router.get("/tunes", response_model=list[TuneJobView], tags=["engines"])
def list_tunes(request: Request) -> list[TuneJobView]:
    _require_loopback(request)
    jobs = sorted(_jobs(request.app).values(), key=lambda item: item.started_at, reverse=True)
    return [job.view() for job in jobs]


def _job(request: Request, job_id: UUID) -> _Job:
    job = _jobs(request.app).get(job_id)
    if job is None:
        raise _error(404, "tune_not_found", "That tune is not in this session any more.")
    return job


@router.get("/tunes/{job_id}", response_model=TuneJobView, tags=["engines"])
def get_tune(job_id: UUID, request: Request) -> TuneJobView:
    _require_loopback(request)
    return _job(request, job_id).view()


@router.post("/tunes/{job_id}/cancel", response_model=TuneJobView, tags=["engines"])
def cancel_tune(job_id: UUID, request: Request) -> TuneJobView:
    """Stop after the benchmark that is running now; the trials so far are kept."""

    _require_loopback(request)
    job = _job(request, job_id)
    job.stop.set()
    return job.view()
