"""Repeat one benchmark plan N times as an experiment group with only a baseline."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from localforge.api.dependencies import DatabaseConnection, LocalAdapterCatalog
from localforge.api.schemas import (
    BenchmarkPlanCreate,
    ExperimentGroupCreateResponse,
)
from localforge.api.workloads import _preflight
from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkPlanStatus,
    ExperimentGroup,
    ExperimentTrial,
    SchemaModel,
)
from localforge.storage.database import transaction
from localforge.storage.repositories import RecordRepository

router = APIRouter(prefix="/api/v1")
MIN_REPEATS = 2
MAX_REPEATS = 10


class PlanRepeatRequest(SchemaModel):
    count: int = Field(ge=MIN_REPEATS, le=MAX_REPEATS)
    name: str | None = Field(default=None, min_length=1, max_length=160)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post(
    "/benchmark-plans/{plan_id}/repeat",
    response_model=ExperimentGroupCreateResponse,
    status_code=201,
    tags=["experiments"],
)
def repeat_benchmark_plan(
    plan_id: UUID,
    body: PlanRepeatRequest,
    request: Request,
    connection: DatabaseConnection,
    catalog: LocalAdapterCatalog,
) -> ExperimentGroupCreateResponse:
    """Create a draft experiment group that runs the same plan ``count`` times in a row.

    Start it and launch its trials with the existing experiment routes; its summary reports the
    median, min-max range, and coefficient of variation of each metric across the repeats.
    """

    plan = RecordRepository(connection, BenchmarkPlan).get(plan_id)
    if plan is None:
        raise _error(404, "benchmark_plan_not_found", "The benchmark plan was not found.")
    if plan.status is not BenchmarkPlanStatus.READY:
        raise _error(409, "benchmark_plan_not_ready", "Resolve plan blockers before repeating.")
    preflight = _preflight(
        BenchmarkPlanCreate(
            name=plan.name,
            engine_profile_id=plan.engine_profile_id,
            machine_profile_id=plan.machine_profile_id,
            model_artifact_id=plan.model_artifact_id,
            workload_id=plan.workload_id,
            retain_prompt_output=plan.retain_prompt_output,
        ),
        connection=connection,
        data_directory=Path(request.app.state.data_directory),
        catalog=catalog,
        expected_snapshot_hash=plan.dataset_snapshot_hash,
    )
    if not preflight.valid:
        first = preflight.blockers[0].message if preflight.blockers else "Preflight failed."
        raise _error(422, "benchmark_plan_not_runnable", f"The plan is not runnable: {first}")
    per_run = preflight.estimated_duration_seconds
    estimated = per_run * body.count if per_run is not None else None
    name = (body.name or f"{plan.name} — repeated ×{body.count}")[:160]
    group = ExperimentGroup(
        name=name,
        description=(
            "Repeats one plan to measure run-to-run spread. Nothing is varied between repeats."
        ),
        baseline_plan_id=plan.id,
        independent_variable=None,
        baseline_value=None,
        variants=[],
        repetitions=body.count,
        randomize_order=False,
        estimated_duration_seconds=estimated,
        trials=[
            ExperimentTrial(
                order=index,
                variant_name="Baseline",
                variant_value=None,
                repetition=index + 1,
                plan_id=plan.id,
            )
            for index in range(body.count)
        ],
    )
    with transaction(connection):
        RecordRepository(connection, ExperimentGroup).create(group)
    return ExperimentGroupCreateResponse(
        group=group,
        estimated_run_count=body.count,
        estimated_duration_seconds=estimated,
    )
