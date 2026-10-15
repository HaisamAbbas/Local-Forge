"""Use-case presets, run targets, verdicts, and prefill-aware first-token estimates.

A verdict compares a finished run's own persisted metrics with the targets saved on its
plan. A missing metric, or one with too few samples, is UNKNOWN: it is never treated as a
pass or as zero. Explanations quote the run's own numbers.
"""

import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Literal
from uuid import UUID

from pydantic import Field

from localforge.core.models import (
    BenchmarkPlan,
    BenchmarkRun,
    BenchmarkRunStatus,
    JsonValue,
    MetricSample,
    MetricSource,
    RequestSample,
    RunTargets,
    SchemaModel,
    WorkloadPreset,
)
from localforge.quality import (
    QUALITY_METHOD_VERSION,
    QUALITY_METRIC_NAME,
    describe_reasons,
    quality_reason_counts,
)
from localforge.storage.repositories import MetricSampleRepository, RequestSampleRepository

VERDICT_METHOD_VERSION = "localforge.verdict.v1"
TargetStatus = Literal["pass", "fail", "unknown"]
OverallVerdict = Literal["meets_targets", "misses_targets", "not_enough_data", "no_targets"]
MIN_QUALITY_SAMPLES = 5
PREFILL_RATE_METRICS = ("ollama.prompt_eval_rate_tokens_per_second",)
LLAMA_CPP_PROMPT_TOKENS = "llamacpp:prompt_tokens_total"
LLAMA_CPP_PROMPT_SECONDS = "llamacpp:prompt_seconds_total"


# --- Use-case presets -------------------------------------------------------------------------


@dataclass(frozen=True)
class UseCasePreset:
    preset: WorkloadPreset
    title: str
    description: str
    input_tokens: int | None
    output_tokens: int
    concurrency: int
    request_count: int
    focus: Literal["latency", "throughput", "quality"]
    targets: RunTargets


USE_CASE_PRESETS: tuple[UseCasePreset, ...] = (
    UseCasePreset(
        preset=WorkloadPreset.INTERACTIVE_CHAT,
        title="Interactive chat",
        description=(
            "A person typing short questions and reading the reply as it streams. The first "
            "word should appear within about a second and text should stream faster than "
            "people read."
        ),
        input_tokens=300,
        output_tokens=200,
        concurrency=1,
        request_count=20,
        focus="latency",
        targets=RunTargets(
            ttft_p50_ms_max=1000,
            ttft_p95_ms_max=2000,
            generation_tokens_per_second_min=10,
            error_rate_percent_max=1,
        ),
    ),
    UseCasePreset(
        preset=WorkloadPreset.CODING_ASSISTANT,
        title="Coding assistant",
        description=(
            "An editor assistant that sends about 8k tokens of surrounding code with each "
            "question. Long prompts make prompt processing (prefill) the main wait before the "
            "first token."
        ),
        input_tokens=8192,
        output_tokens=300,
        concurrency=1,
        request_count=10,
        focus="latency",
        targets=RunTargets(
            ttft_p50_ms_max=5000,
            ttft_p95_ms_max=10000,
            generation_tokens_per_second_min=15,
            error_rate_percent_max=1,
        ),
    ),
    UseCasePreset(
        preset=WorkloadPreset.DOCUMENT_QA,
        title="Document Q&A / RAG",
        description=(
            "Answering questions over retrieved document passages: about 4k tokens of context "
            "and a short answer. The whole answer should arrive in well under half a minute."
        ),
        input_tokens=4096,
        output_tokens=200,
        concurrency=1,
        request_count=10,
        focus="latency",
        targets=RunTargets(
            ttft_p50_ms_max=3000,
            ttft_p95_ms_max=6000,
            latency_p50_ms_max=20000,
            generation_tokens_per_second_min=10,
            error_rate_percent_max=1,
        ),
    ),
    UseCasePreset(
        preset=WorkloadPreset.BATCH_SUMMARIZATION,
        title="Batch summarization",
        description=(
            "Summarizing many 2k-token documents with four requests in flight. Nobody waits "
            "on a single reply, so total documents per second matters most."
        ),
        input_tokens=2048,
        output_tokens=150,
        concurrency=4,
        request_count=20,
        focus="throughput",
        targets=RunTargets(request_throughput_min=0.2, error_rate_percent_max=2),
    ),
    UseCasePreset(
        preset=WorkloadPreset.LONG_CONTEXT_STRESS,
        title="Long context stress",
        description=(
            "About 16k tokens of input per request to find where this machine's context "
            "handling breaks down. Expect slow first tokens on CPU-only machines."
        ),
        input_tokens=16384,
        output_tokens=128,
        concurrency=1,
        request_count=5,
        focus="latency",
        targets=RunTargets(ttft_p50_ms_max=15000, error_rate_percent_max=5),
    ),
    UseCasePreset(
        preset=WorkloadPreset.QUALITY_SMOKE,
        title="Quality smoke",
        description=(
            "Twenty short arithmetic, capital-city, and JSON-formatting prompts with known "
            "answers. A quick check that a faster setup still answers correctly."
        ),
        input_tokens=None,
        output_tokens=64,
        concurrency=1,
        request_count=20,
        focus="quality",
        targets=RunTargets(quality_pass_rate_min=80, error_rate_percent_max=5),
    ),
)
USE_CASES_BY_PRESET = {item.preset: item for item in USE_CASE_PRESETS}


def default_targets_for(preset: WorkloadPreset | None) -> RunTargets | None:
    use_case = USE_CASES_BY_PRESET.get(preset) if preset is not None else None
    return use_case.targets.model_copy() if use_case is not None else None


class UseCasePresetView(SchemaModel):
    preset: WorkloadPreset
    title: str
    description: str
    input_tokens: int | None
    output_tokens: int
    concurrency: int
    request_count: int
    focus: str
    default_targets: RunTargets
    uses_builtin_dataset: bool


def use_case_views() -> list[UseCasePresetView]:
    return [
        UseCasePresetView(
            preset=item.preset,
            title=item.title,
            description=item.description,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            concurrency=item.concurrency,
            request_count=item.request_count,
            focus=item.focus,
            default_targets=item.targets,
            uses_builtin_dataset=item.preset is WorkloadPreset.QUALITY_SMOKE,
        )
        for item in USE_CASE_PRESETS
    ]


# --- Formatting helpers -----------------------------------------------------------------------


def format_duration_ms(value: float) -> str:
    if value < 1000:
        return f"{value:.0f} ms"
    seconds = value / 1000
    if seconds < 10:
        return f"{seconds:.1f} s"
    if seconds < 120:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.1f} min"


def about_duration(seconds: float) -> str:
    if seconds < 1:
        return "under a second"
    if seconds < 45:
        return f"about {seconds:.0f} s"
    if seconds < 90:
        return "about a minute"
    return f"about {seconds / 60:.0f} minutes"


def _rate(value: float) -> str:
    return f"{value:.1f}" if value < 10 else f"{value:.0f}"


def _requests_rate(value: float) -> str:
    return f"{value:.3g}"


def _with_article(tokens: float) -> str:
    """'an 8,192-token' / 'a 4,096-token': pick the article by how the number is spoken."""

    digits = str(round(tokens))
    vowel_sound = digits.startswith("8") or (
        len(digits) in {2, 5, 8} and digits[:2] in {"11", "18"}
    )
    return f"{'an' if vowel_sound else 'a'} {round(tokens):,}-token"


def _percent(value: float) -> str:
    return f"{value:.0f}%" if value == round(value) else f"{value:.1f}%"


# --- Verdict models ---------------------------------------------------------------------------


class TargetEvaluation(SchemaModel):
    target: str
    label: str
    comparison: Literal["at_most", "at_least"]
    threshold: float
    unit: str
    observed: float | None = None
    sample_count: int = Field(default=0, ge=0)
    status: TargetStatus
    explanation: str


class RunVerdict(SchemaModel):
    run_id: UUID
    plan_id: UUID
    run_status: BenchmarkRunStatus
    overall: OverallVerdict
    headline: str
    summary: str
    targets: RunTargets | None = None
    target_source: Literal["run_plan_snapshot", "plan", "none"] = "none"
    evaluations: list[TargetEvaluation] = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    unknown: int = 0
    concurrency_level: str | None = None
    method_version: str = VERDICT_METHOD_VERSION
    quality_method_version: str | None = None


@dataclass(frozen=True)
class _TargetSpec:
    key: str
    label: str
    metric: str
    aggregation: str | None
    comparison: Literal["at_most", "at_least"]
    unit: str


_TARGET_SPECS: tuple[_TargetSpec, ...] = (
    _TargetSpec("ttft_p50_ms_max", "First token (p50)", "ttft_ms", "p50", "at_most", "ms"),
    _TargetSpec("ttft_p95_ms_max", "First token (p95)", "ttft_ms", "p95", "at_most", "ms"),
    _TargetSpec(
        "latency_p50_ms_max", "Full response (p50)", "request_latency_ms", "p50", "at_most", "ms"
    ),
    _TargetSpec(
        "generation_tokens_per_second_min",
        "Generation speed",
        "generation_throughput_tokens_per_second",
        None,
        "at_least",
        "tokens/s",
    ),
    _TargetSpec(
        "request_throughput_min",
        "Requests per second",
        "request_throughput_requests_per_second",
        None,
        "at_least",
        "requests/s",
    ),
    _TargetSpec("error_rate_percent_max", "Error rate", "error_rate_percent", None, "at_most", "%"),
    _TargetSpec(
        "quality_pass_rate_min",
        "Quality pass rate",
        QUALITY_METRIC_NAME,
        None,
        "at_least",
        "%",
    ),
)


@dataclass
class _RunContext:
    metrics: dict[tuple[str, str], MetricSample]
    prefill_rate: float | None
    mean_prompt_tokens: float | None
    mean_completion_tokens: float | None
    planned_input_tokens: int | None
    concurrency: int | None
    attempted: int
    quality_reasons: list[tuple[str, int]]


def _run_level_metrics(metrics: Sequence[MetricSample]) -> tuple[list[MetricSample], str | None]:
    """Keep summary rows; for a concurrency sweep use the highest level (most demanding)."""

    summaries = [
        metric
        for metric in metrics
        if metric.source in {MetricSource.DERIVED, MetricSource.CLIENT_MEASURED}
        and metric.aggregation not in {"raw", "delta"}
        and "request_id" not in metric.labels
        and "snapshot" not in metric.labels
    ]
    levels = {metric.labels.get("concurrency_level") for metric in summaries}
    if len(levels) <= 1:
        return summaries, None

    def level_value(level: str | None) -> int:
        return int(level) if level is not None and level.isdigit() else -1

    chosen = max(levels, key=level_value)
    return [metric for metric in summaries if metric.labels.get("concurrency_level") == chosen], (
        chosen
    )


def _prefill_rate(metrics: Sequence[MetricSample]) -> float | None:
    for name in PREFILL_RATE_METRICS:
        for metric in metrics:
            if (
                metric.metric_name == name
                and metric.aggregation == "run_rate"
                and metric.value is not None
                and metric.value > 0
            ):
                return metric.value
    tokens = seconds = None
    for metric in metrics:
        if metric.aggregation != "delta" or metric.value is None:
            continue
        if metric.metric_name == LLAMA_CPP_PROMPT_TOKENS:
            tokens = metric.value
        elif metric.metric_name == LLAMA_CPP_PROMPT_SECONDS:
            seconds = metric.value
    if tokens and seconds and tokens > 0 and seconds > 0:
        return tokens / seconds
    return None


def _planned_input_tokens(plan: BenchmarkPlan) -> int | None:
    workload = plan.configuration_snapshot.get("workload")
    if isinstance(workload, dict):
        value = workload.get("target_input_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _context(
    plan: BenchmarkPlan,
    metrics: Sequence[MetricSample],
    samples: Sequence[RequestSample],
) -> tuple[_RunContext, str | None]:
    selected, level = _run_level_metrics(metrics)
    by_key = {(metric.metric_name, metric.aggregation): metric for metric in selected}
    measured = [sample for sample in samples if sample.phase == "measurement"]
    prompt_counts = [s.prompt_tokens for s in measured if s.prompt_tokens is not None]
    completion_counts = [
        s.completion_tokens
        for s in measured
        if s.completion_tokens is not None and s.outcome.value == "succeeded"
    ]
    attempted_metric = by_key.get(("requests_attempted", "run_total"))
    reasons = quality_reason_counts(measured).most_common()
    return (
        _RunContext(
            metrics=by_key,
            prefill_rate=_prefill_rate(metrics),
            mean_prompt_tokens=fmean(prompt_counts) if prompt_counts else None,
            mean_completion_tokens=fmean(completion_counts) if completion_counts else None,
            planned_input_tokens=_planned_input_tokens(plan),
            concurrency=plan.concurrency,
            attempted=int(attempted_metric.value or 0) if attempted_metric is not None else 0,
            quality_reasons=reasons,
        ),
        level,
    )


def _find_metric(context: _RunContext, spec: _TargetSpec) -> MetricSample | None:
    if spec.aggregation is not None:
        return context.metrics.get((spec.metric, spec.aggregation))
    for (name, _aggregation), metric in context.metrics.items():
        if name == spec.metric:
            return metric
    return None


def _threshold_text(spec: _TargetSpec, value: float) -> str:
    if spec.unit == "ms":
        return format_duration_ms(value)
    if spec.unit == "tokens/s":
        return f"{_rate(value)} tokens/s"
    if spec.unit == "requests/s":
        return f"{_requests_rate(value)} requests/s"
    return _percent(value)


def _ttft_miss(context: _RunContext, observed_ms: float, percentile: str, target: str) -> str:
    text = f"First token took {format_duration_ms(observed_ms)} at {percentile} "
    if percentile == "p95":
        text += "(the slowest 1 in 20 requests) "
    text += f"against a {target} target."
    prompt_tokens = context.mean_prompt_tokens or context.planned_input_tokens
    if context.prefill_rate is not None and prompt_tokens:
        prefill_seconds = prompt_tokens / context.prefill_rate
        text += (
            f" Prompt processing ran at ~{_rate(context.prefill_rate)} tokens/s, so "
            f"{_with_article(prompt_tokens)} prompt needs {about_duration(prefill_seconds)} on "
            "this machine"
        )
        if prefill_seconds * 1000 < observed_ms * 0.5:
            text += (
                ", which is less than half of the wait; model loading, queueing, or "
                "concurrency added the rest. Try warmup requests or lower concurrency."
            )
        else:
            text += "; try a smaller context, a GPU engine, or a smaller model."
        return text
    return text + (
        " This engine did not report a prompt-processing rate. Long prompts, model loading, "
        "and queueing all delay the first token; try a smaller context, warmup requests, a "
        "GPU engine, or a smaller model."
    )


def _explain_fail(
    spec: _TargetSpec, observed: float, threshold: float, context: _RunContext
) -> str:
    target = _threshold_text(spec, threshold)
    if spec.key == "ttft_p50_ms_max":
        return _ttft_miss(context, observed, "p50", target)
    if spec.key == "ttft_p95_ms_max":
        return _ttft_miss(context, observed, "p95", target)
    if spec.key == "latency_p50_ms_max":
        text = (
            f"Requests took {format_duration_ms(observed)} end to end at p50 against a "
            f"{target} target."
        )
        ttft = context.metrics.get(("ttft_ms", "p50"))
        if ttft is not None and ttft.value is not None:
            text += f" The first token alone took {format_duration_ms(ttft.value)}."
        generation = context.metrics.get(("generation_throughput_tokens_per_second", "run_rate"))
        if (
            generation is not None
            and generation.value
            and context.mean_completion_tokens is not None
        ):
            seconds = context.mean_completion_tokens / generation.value
            text += (
                f" Generating about {context.mean_completion_tokens:.0f} tokens at "
                f"~{_rate(generation.value)} tokens/s took {about_duration(seconds)}."
            )
        return text + " Try fewer output tokens, a smaller context, or a faster engine or model."
    if spec.key == "generation_tokens_per_second_min":
        return (
            f"Generation ran at {_rate(observed)} tokens/s against a {target} target. "
            "Generation speed is mostly limited by model size and memory bandwidth; try a "
            "smaller or more heavily quantized model, or a GPU engine."
        )
    if spec.key == "request_throughput_min":
        concurrency = (
            f" with {context.concurrency} requests in flight" if context.concurrency else ""
        )
        return (
            f"The run completed {_requests_rate(observed)} requests/s{concurrency} "
            f"(about {observed * 3600:,.0f} per hour) against a {target} target "
            f"({threshold * 3600:,.0f} per hour). Try shorter prompts or outputs, a smaller "
            "model, a GPU engine, or a server that batches parallel requests."
        )
    if spec.key == "error_rate_percent_max":
        failed = round(observed * context.attempted / 100) if context.attempted else None
        count = f"{failed} of {context.attempted} requests" if failed is not None else "Requests"
        return (
            f"{count} did not complete ({_percent(observed)}) against a {target} limit. "
            "Open the run's events to see whether they timed out or the engine returned errors."
        )
    # Quality pass rate.
    quality = context.metrics.get((QUALITY_METRIC_NAME, "run_rate"))
    passed = quality.labels.get("passed") if quality is not None else None
    if quality is not None and passed is not None:
        text = (
            f"{passed} of {quality.sample_count} checked outputs passed "
            f"({_percent(observed)}) against a {target} target."
        )
    else:
        text = f"{_percent(observed)} of checked outputs passed against a {target} target."
    if context.quality_reasons:
        reason, reason_count = context.quality_reasons[0]
        text += f" Most common problem: {describe_reasons([reason])[0]} ({reason_count})."
    return text + " Try a larger model, a less aggressive quantization, or clearer prompts."


def _explain_pass(spec: _TargetSpec, observed: float, threshold: float) -> str:
    comparator = "≤" if spec.comparison == "at_most" else "≥"
    return (
        f"{spec.label}: {_threshold_text(spec, observed)} "
        f"(target {comparator} {_threshold_text(spec, threshold)})."
    )


def _explain_unknown(spec: _TargetSpec, metric: MetricSample | None) -> str:
    if metric is None:
        if spec.key == "quality_pass_rate_min":
            return (
                "No quality checks ran in this run. Enable quality checks on the workload to "
                "measure a pass rate."
            )
        return f"This run did not record {spec.label.lower()}."
    flags = set(metric.quality_flags)
    if "insufficient_samples_for_stable_tail" in flags:
        return (
            f"{spec.label} needs at least 20 measured requests for a stable value; this run "
            f"has {metric.sample_count}."
        )
    if spec.key == "quality_pass_rate_min" and metric.sample_count < MIN_QUALITY_SAMPLES:
        return (
            f"Only {metric.sample_count} outputs could be checked; at least "
            f"{MIN_QUALITY_SAMPLES} are needed for a pass rate."
        )
    if "unavailable_token_counts_missing" in flags:
        return "The engine did not report token counts, so generation speed is unavailable."
    if "unavailable_no_samples" in flags or metric.sample_count == 0:
        return f"No measured requests produced {spec.label.lower()}."
    return f"{spec.label} was unavailable for this run."


def _evaluate_target(spec: _TargetSpec, threshold: float, context: _RunContext) -> TargetEvaluation:
    metric = _find_metric(context, spec)
    observed = metric.value if metric is not None else None
    sample_count = metric.sample_count if metric is not None else 0
    enough = (
        observed is not None
        and math.isfinite(observed)
        and sample_count >= 1
        and (spec.key != "quality_pass_rate_min" or sample_count >= MIN_QUALITY_SAMPLES)
    )
    if not enough or observed is None:
        status: TargetStatus = "unknown"
        explanation = _explain_unknown(spec, metric)
    else:
        meets = observed <= threshold if spec.comparison == "at_most" else observed >= threshold
        status = "pass" if meets else "fail"
        explanation = (
            _explain_pass(spec, observed, threshold)
            if meets
            else _explain_fail(spec, observed, threshold, context)
        )
        if sample_count < 5 and spec.key != "quality_pass_rate_min":
            explanation += f" Based on only {sample_count} measured requests."
    return TargetEvaluation(
        target=spec.key,
        label=spec.label,
        comparison=spec.comparison,
        threshold=threshold,
        unit=spec.unit,
        observed=observed,
        sample_count=sample_count,
        status=status,
        explanation=explanation,
    )


def targets_for_run(
    run: BenchmarkRun, plan: BenchmarkPlan
) -> tuple[RunTargets | None, Literal["run_plan_snapshot", "plan", "none"]]:
    """Prefer the targets captured when the run started; fall back to the plan's current ones."""

    captured = run.profile_snapshot.get("targets")
    if isinstance(captured, dict):
        return RunTargets.model_validate(captured), "run_plan_snapshot"
    if plan.targets is not None:
        return plan.targets, "plan"
    snapshot_targets = plan.configuration_snapshot.get("targets")
    if isinstance(snapshot_targets, dict):
        return RunTargets.model_validate(snapshot_targets), "plan"
    return None, "none"


_TERMINAL = {
    BenchmarkRunStatus.COMPLETED,
    BenchmarkRunStatus.FAILED,
    BenchmarkRunStatus.CANCELLED,
    BenchmarkRunStatus.INTERRUPTED,
}


def evaluate_verdict(
    run: BenchmarkRun,
    plan: BenchmarkPlan,
    metrics: Sequence[MetricSample],
    samples: Sequence[RequestSample],
) -> RunVerdict:
    targets, source = targets_for_run(run, plan)

    def verdict(**values: object) -> RunVerdict:
        return RunVerdict.model_validate(
            {
                "run_id": run.id,
                "plan_id": plan.id,
                "run_status": run.status,
                "targets": targets,
                "target_source": source,
                **values,
            }
        )

    active = [
        (spec, getattr(targets, spec.key))
        for spec in _TARGET_SPECS
        if targets is not None and getattr(targets, spec.key) is not None
    ]
    if not active:
        return verdict(
            overall="no_targets",
            headline="No targets set",
            summary=(
                "This plan has no targets. Pick a use-case preset or add targets in Workloads "
                "to get a pass/fail verdict."
            ),
        )
    if run.status not in _TERMINAL:
        evaluations = [
            TargetEvaluation(
                target=spec.key,
                label=spec.label,
                comparison=spec.comparison,
                threshold=float(threshold),
                unit=spec.unit,
                status="unknown",
                explanation="The run has not finished yet.",
            )
            for spec, threshold in active
        ]
        return verdict(
            overall="not_enough_data",
            headline="Not enough data",
            summary="The run is still in progress; the verdict appears when it finishes.",
            evaluations=evaluations,
            unknown=len(evaluations),
        )
    context, level = _context(plan, metrics, samples)
    evaluations = [_evaluate_target(spec, float(value), context) for spec, value in active]
    passed = sum(item.status == "pass" for item in evaluations)
    failed = sum(item.status == "fail" for item in evaluations)
    unknown = sum(item.status == "unknown" for item in evaluations)
    if failed:
        overall: OverallVerdict = "misses_targets"
        headline = f"Misses {failed} target{'s' if failed != 1 else ''}"
    elif unknown:
        overall = "not_enough_data"
        headline = "Not enough data"
    else:
        overall = "meets_targets"
        headline = "Meets your targets"
    summary = f"{passed} of {len(evaluations)} targets met"
    if failed:
        summary += f", {failed} missed"
    if unknown:
        summary += f", {unknown} could not be checked"
    summary += "."
    if level is not None:
        summary += f" Judged at concurrency {level}, the most demanding level in this run."
    quality_used = any(
        item.target == "quality_pass_rate_min" and item.status != "unknown" for item in evaluations
    )
    return verdict(
        overall=overall,
        headline=headline,
        summary=summary,
        evaluations=evaluations,
        passed=passed,
        failed=failed,
        unknown=unknown,
        concurrency_level=level,
        quality_method_version=QUALITY_METHOD_VERSION if quality_used else None,
    )


def verdict_for_run(connection: sqlite3.Connection, run: BenchmarkRun) -> RunVerdict | None:
    from localforge.storage.repositories import RecordRepository

    plan = RecordRepository(connection, BenchmarkPlan).get(run.plan_id)
    if plan is None:
        return None
    metrics = MetricSampleRepository(connection).list_for_run(run.id)
    samples = list(RequestSampleRepository(connection).iter_for_run(run.id, limit=None))
    return evaluate_verdict(run, plan, metrics, samples)


# --- Prefill-aware first-token estimate -------------------------------------------------------


class PrefillEstimate(SchemaModel):
    available: bool
    input_tokens: int
    prompt_tokens_per_second: float | None = None
    expected_first_token_seconds: float | None = None
    run_count: int = 0
    request_count: int = 0
    source_metric: str | None = None
    message: str


def _completed_runs_for(
    connection: sqlite3.Connection, engine_profile_id: UUID, model_artifact_id: UUID | None
) -> list[BenchmarkRun]:
    plan_rows = connection.execute(
        "SELECT payload_json FROM domain_records WHERE entity_type = 'BenchmarkPlan'"
    ).fetchall()
    plan_ids: set[str] = set()
    for row in plan_rows:
        payload = json.loads(row["payload_json"])
        if payload.get("engine_profile_id") != str(engine_profile_id):
            continue
        if model_artifact_id is not None and payload.get("model_artifact_id") != str(
            model_artifact_id
        ):
            continue
        plan_ids.add(str(payload.get("id")))
    if not plan_ids:
        return []
    run_rows = connection.execute(
        "SELECT payload_json FROM domain_records WHERE entity_type = 'BenchmarkRun' "
        "ORDER BY created_at DESC, id DESC"
    ).fetchall()
    runs: list[BenchmarkRun] = []
    for row in run_rows:
        run_payload: dict[str, JsonValue] = json.loads(row["payload_json"])
        if run_payload.get("plan_id") in plan_ids and run_payload.get("status") == "completed":
            runs.append(BenchmarkRun.model_validate(run_payload))
    return runs


def estimate_prefill(
    connection: sqlite3.Connection,
    *,
    engine_profile_id: UUID,
    model_artifact_id: UUID | None,
    input_tokens: int,
    max_runs: int = 5,
) -> PrefillEstimate:
    """Estimate time-to-first-token from prompt-processing rates measured in earlier runs."""

    repository = MetricSampleRepository(connection)
    weighted_total = 0.0
    weights = 0
    used_runs = 0
    source: str | None = None
    for run in _completed_runs_for(connection, engine_profile_id, model_artifact_id):
        metrics = repository.list_for_run(run.id)
        rate = _prefill_rate(metrics)
        if rate is None:
            continue
        rate_metric = next(
            (
                metric
                for metric in metrics
                if metric.metric_name in PREFILL_RATE_METRICS and metric.aggregation == "run_rate"
            ),
            None,
        )
        weight = max(1, rate_metric.sample_count if rate_metric is not None else 1)
        source = rate_metric.metric_name if rate_metric is not None else "llamacpp:prompt_rate"
        weighted_total += rate * weight
        weights += weight
        used_runs += 1
        if used_runs >= max_runs:
            break
    if not weights:
        return PrefillEstimate(
            available=False,
            input_tokens=input_tokens,
            message=(
                "No earlier run of this engine and model reported a prompt-processing rate, "
                "so first-token time cannot be estimated yet."
            ),
        )
    rate = weighted_total / weights
    seconds = input_tokens / rate
    return PrefillEstimate(
        available=True,
        input_tokens=input_tokens,
        prompt_tokens_per_second=rate,
        expected_first_token_seconds=seconds,
        run_count=used_runs,
        request_count=weights,
        source_metric=source,
        message=(
            f"Expected first token ≈ {format_duration_ms(seconds * 1000)} for this "
            f"{input_tokens:,}-token context (from your earlier runs at "
            f"~{_rate(rate)} tokens/s prompt processing)."
        ),
    )
