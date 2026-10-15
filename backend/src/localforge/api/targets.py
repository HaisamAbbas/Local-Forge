"""Use-case presets, plan targets, run verdicts, and prefill-aware first-token estimates.

Routes:
- ``GET /api/v1/workload-presets/use-cases``: use-case presets with descriptions and default
  targets.
- ``PUT /api/v1/benchmark-plans/{plan_id}/targets``: replace (or clear with null) a plan's
  targets. Runs keep the targets captured when they started.
- ``GET /api/v1/runs/{run_id}/verdict``: per-target PASS / FAIL / UNKNOWN and an overall
  verdict, evaluated on read from the run's persisted metrics.
- ``GET /api/v1/prefill-estimate``: expected first-token time for a context size, from the
  prompt-processing rate measured in earlier completed runs of the same engine and model.
"""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from localforge.api.dependencies import DatabaseConnection
from localforge.api.schemas import PlanTargetsUpdate
from localforge.core.models import BenchmarkPlan, BenchmarkRun, JsonValue
from localforge.storage.repositories import RecordRepository
from localforge.targets import (
    PrefillEstimate,
    RunVerdict,
    UseCasePresetView,
    estimate_prefill,
    use_case_views,
    verdict_for_run,
)

router = APIRouter(prefix="/api/v1")


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


@router.get(
    "/workload-presets/use-cases",
    response_model=list[UseCasePresetView],
    tags=["workloads"],
)
def list_use_case_presets() -> list[UseCasePresetView]:
    return use_case_views()


@router.put(
    "/benchmark-plans/{plan_id}/targets",
    response_model=BenchmarkPlan,
    tags=["benchmark-plans"],
)
def update_plan_targets(
    plan_id: UUID, body: PlanTargetsUpdate, connection: DatabaseConnection
) -> BenchmarkPlan:
    repository = RecordRepository(connection, BenchmarkPlan)
    plan = repository.get(plan_id)
    if plan is None:
        raise _not_found("benchmark_plan_not_found", "The benchmark plan was not found.")
    targets = body.targets
    snapshot = dict(plan.configuration_snapshot)
    snapshot["targets"] = (
        cast(JsonValue, targets.model_dump(mode="json")) if targets is not None else None
    )
    updated = BenchmarkPlan.model_validate(
        plan.model_dump(mode="python") | {"targets": targets, "configuration_snapshot": snapshot}
    )
    return repository.update(updated)


@router.get("/runs/{run_id}/verdict", response_model=RunVerdict, tags=["runs"])
def get_run_verdict(run_id: UUID, connection: DatabaseConnection) -> RunVerdict:
    run = RecordRepository(connection, BenchmarkRun).get(run_id)
    if run is None:
        raise _not_found("benchmark_run_not_found", "The benchmark run was not found.")
    verdict = verdict_for_run(connection, run)
    if verdict is None:
        raise _not_found("benchmark_plan_not_found", "The run's benchmark plan no longer exists.")
    return verdict


@router.get("/prefill-estimate", response_model=PrefillEstimate, tags=["workloads"])
def get_prefill_estimate(
    connection: DatabaseConnection,
    engine_profile_id: UUID,
    input_tokens: int = Query(ge=1, le=10_000_000),
    model_artifact_id: UUID | None = None,
) -> PrefillEstimate:
    return estimate_prefill(
        connection,
        engine_profile_id=engine_profile_id,
        model_artifact_id=model_artifact_id,
        input_tokens=input_tokens,
    )
