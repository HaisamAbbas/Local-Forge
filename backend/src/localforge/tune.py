"""``localforge tune``: find the best launch settings for a managed llama.cpp on this machine.

The plan file is a ``localforge bench`` plan for a managed llama.cpp (``executable_path`` and
``model_path`` in ``engine.extensions.llama_cpp``) plus an optional ``tune`` section. The tuner
changes one setting at a time, starting from the engine's defaults, runs the same short benchmark
for each value, and keeps a change only when it is clearly better. It prints the winning settings
and every trial, so the result can be checked.

    localforge tune plan.json --out tuned.json
    localforge tune plan.json --objective latency

Tuned settings are measurements for this machine and model. Run it with other programs closed:
a busy machine makes every trial noisy, which is why a change must beat the current best by a
margin (default 5%) to be kept.
"""

import argparse
import copy
import json
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from localforge.headless import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    ApiClient,
    BenchError,
    PlanError,
    _metric_value,
    parse_plan,
    private_server,
    run_plan,
)

TUNE_SCHEMA = "localforge.tune.v1"
LLAMA_CPP_ADAPTER = "localforge.llama_cpp"
DEFAULT_MIN_GAIN_PERCENT = 5.0
GPU_LAYER_ALL = 999
BATCH_SIZES = (512, 1024)

# objective -> (metric key, True when a higher value is better, plain description)
OBJECTIVES: dict[str, tuple[str, bool, str]] = {
    "throughput": (
        "generation_throughput_tokens_per_second.run_rate",
        True,
        "generation speed (tokens per second)",
    ),
    "latency": ("request_latency_ms.p50", False, "median time to finish a request"),
    "ttft": ("ttft_ms.p50", False, "median time to the first token"),
}
# The order matters: later settings are tuned with the earlier winners already applied.
SETTINGS_ORDER = ("threads", "gpu_layers", "batch_size")
SETTING_LABELS = {
    "threads": "CPU threads",
    "gpu_layers": "GPU layers",
    "batch_size": "batch size",
}
TUNE_KEYS = {"objective", "min_gain_percent", "threads", "gpu_layers", "batch_size", "requests"}

Settings = dict[str, Any]
Evaluator = Callable[[Settings], dict[str, Any]]


class TuneCancelled(Exception):
    """The caller asked the search to stop; the trials so far are still reported."""


def trial_count(space: dict[str, list[Any]]) -> int:
    """How many benchmark runs a search over this space makes (the defaults, then each value)."""

    return 1 + sum(len(space.get(key, [])) for key in SETTINGS_ORDER)


def default_search_space(
    *, logical_cores: int | None, physical_cores: int | None, has_gpu: bool
) -> dict[str, list[Any]]:
    """Values worth trying on this machine. The engine's own default is always measured first."""

    space: dict[str, list[Any]] = {}
    logical = logical_cores or os.cpu_count() or 4
    physical = physical_cores or max(1, logical // 2)
    threads = sorted({max(1, physical // 2), physical, logical})
    space["threads"] = threads
    if has_gpu:
        space["gpu_layers"] = [0, GPU_LAYER_ALL]
    space["batch_size"] = list(BATCH_SIZES)
    return space


def parse_tune_plan(raw: object) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a tune plan into the bench plan and its validated ``tune`` section."""

    if not isinstance(raw, dict):
        raise PlanError("the plan must be a JSON object")
    body = dict(raw)
    tune = body.pop("tune", {})
    if not isinstance(tune, dict):
        raise PlanError("tune must be an object")
    unknown = set(tune) - TUNE_KEYS
    if unknown:
        raise PlanError(f"unknown tune field(s): {', '.join(sorted(unknown))}")
    workload = dict(body.get("workload") or {})
    # A short run per trial: enough requests to average out, few enough to finish quickly.
    workload.setdefault("requests", tune.get("requests", 6))
    workload.setdefault("concurrency", 1)
    workload.setdefault("max_output_tokens", 64)
    workload.setdefault("warmup", 1)
    body["workload"] = workload
    body.setdefault("label", "llama.cpp tune")
    plan = parse_plan(body)
    engine = plan["engine"]
    if engine["adapter"] != LLAMA_CPP_ADAPTER or engine["mode"] != "managed_process":
        raise PlanError(
            "tuning needs a managed llama.cpp: engine.adapter localforge.llama_cpp and "
            "engine.mode managed_process, so LocalForge can restart it with each setting"
        )
    extension = engine["extensions"].get("llama_cpp")
    if not isinstance(extension, dict) or not (
        extension.get("executable_path") and extension.get("model_path")
    ):
        raise PlanError(
            "engine.extensions.llama_cpp needs executable_path (llama-server) and model_path "
            "(a .gguf file)"
        )
    objective = tune.get("objective", "throughput")
    if objective not in OBJECTIVES:
        raise PlanError(f"tune.objective must be one of {', '.join(OBJECTIVES)}")
    gain = tune.get("min_gain_percent", DEFAULT_MIN_GAIN_PERCENT)
    if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not 0 <= gain <= 100:
        raise PlanError("tune.min_gain_percent must be a number from 0 to 100")
    space: dict[str, list[Any]] = {}
    for key in SETTINGS_ORDER:
        if key not in tune:
            continue
        values = tune[key]
        if (
            not isinstance(values, list)
            or not values
            or not all(
                isinstance(item, int) and not isinstance(item, bool) and item >= 0
                for item in values
            )
        ):
            raise PlanError(f"tune.{key} must be a list of whole numbers")
        space[key] = list(dict.fromkeys(values))
    return plan, {"objective": objective, "min_gain_percent": float(gain), "space": space}


def _better(candidate: float, best: float, higher_is_better: bool, min_gain_percent: float) -> bool:
    if best == 0:
        return candidate > 0 if higher_is_better else False
    margin = min_gain_percent / 100
    if higher_is_better:
        return candidate >= best * (1 + margin)
    return candidate <= best * (1 - margin)


def coordinate_search(
    space: dict[str, list[Any]],
    evaluate: Evaluator,
    *,
    objective: str,
    min_gain_percent: float,
    on_trial: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Tune one setting at a time. ``evaluate(settings)`` returns a bench result for them.

    ``on_trial`` is called with each finished trial; ``should_stop`` is checked before each one.
    """

    metric_key, higher_is_better, _ = OBJECTIVES[objective]
    trials: list[dict[str, Any]] = []

    def measure(settings: Settings, changed: str | None) -> float | None:
        if should_stop is not None and should_stop():
            raise TuneCancelled
        entry: dict[str, Any] = {"settings": dict(settings), "changed": changed}
        try:
            result = evaluate(dict(settings))
        except BenchError as error:
            entry.update(score=None, error=str(error))
        else:
            score = _metric_value(result, metric_key)
            entry.update(
                score=score,
                ttft_ms=_metric_value(result, "ttft_ms.p50"),
                tokens_per_second=_metric_value(
                    result, "generation_throughput_tokens_per_second.run_rate"
                ),
                latency_ms=_metric_value(result, "request_latency_ms.p50"),
                error=None if score is not None else "the run produced no measurements",
            )
        trials.append(entry)
        if on_trial is not None:
            on_trial(entry)
        score = entry["score"]
        return score if isinstance(score, (int, float)) else None

    current: Settings = {}
    baseline = measure(current, None)
    if baseline is None:
        return {
            "trials": trials,
            "best_settings": {},
            "baseline_score": None,
            "best_score": None,
            "changes": [],
            "error": trials[0]["error"],
        }
    best = baseline
    changes: list[dict[str, Any]] = []
    for key in SETTINGS_ORDER:
        winner: tuple[Any, float] | None = None
        for value in space.get(key, []):
            score = measure({**current, key: value}, key)
            if score is None:
                continue
            reference = winner[1] if winner else best
            if _better(score, reference, higher_is_better, min_gain_percent):
                winner = (value, score)
        if winner is not None:
            changes.append(
                {"setting": key, "value": winner[0], "from_score": best, "to_score": winner[1]}
            )
            current[key] = winner[0]
            best = winner[1]
    return {
        "trials": trials,
        "best_settings": current,
        "baseline_score": baseline,
        "best_score": best,
        "changes": changes,
        "error": None,
    }


def _improvement_percent(baseline: float | None, best: float | None, higher: bool) -> float | None:
    if baseline in (None, 0) or best is None:
        return None
    assert baseline is not None
    change = (best - baseline) / baseline * 100
    return round(change if higher else -change, 1)


def make_evaluator(client: ApiClient, plan: dict[str, Any]) -> Evaluator:
    def evaluate(settings: Settings) -> dict[str, Any]:
        trial = copy.deepcopy(plan)
        trial["engine"]["extensions"]["llama_cpp"].update(settings)
        shown = ", ".join(f"{key}={value}" for key, value in settings.items()) or "defaults"
        trial["label"] = f"tune: {shown}"
        return run_plan(client, trial)

    return evaluate


def search_space_for(plan: dict[str, Any], requested: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """The requested values, filling in the rest from this machine's hardware."""

    from localforge.system.inspection import inspect_local_machine

    hardware = inspect_local_machine().hardware
    defaults = default_search_space(
        logical_cores=hardware.cpu_logical_cores if hardware else None,
        physical_cores=hardware.cpu_physical_cores if hardware else None,
        has_gpu=bool(hardware and hardware.accelerators),
    )
    return {key: requested.get(key, defaults.get(key, [])) for key in SETTINGS_ORDER}


def build_result(
    plan: dict[str, Any], tune: dict[str, Any], search: dict[str, Any]
) -> dict[str, Any]:
    metric_key, higher, description = OBJECTIVES[tune["objective"]]
    extension = plan["engine"]["extensions"]["llama_cpp"]
    result: dict[str, Any] = {
        "schema": TUNE_SCHEMA,
        "label": plan["label"],
        "objective": tune["objective"],
        "objective_description": description,
        "metric": metric_key,
        "min_gain_percent": tune["min_gain_percent"],
        "model_path": extension.get("model_path"),
        "trials": search["trials"],
        "changes": search["changes"],
        "best_settings": search["best_settings"],
        "recommended_launch_settings": {**extension, **search["best_settings"]},
        "baseline_score": search["baseline_score"],
        "best_score": search["best_score"],
        "improvement_percent": _improvement_percent(
            search["baseline_score"], search["best_score"], higher
        ),
        "error": search["error"],
    }
    result["ok"] = search["error"] is None
    return result


def render_summary(result: dict[str, Any]) -> str:
    lines = [f"LocalForge tune: {result['label']}  (goal: {result['objective_description']})"]
    for number, trial in enumerate(result["trials"], start=1):
        shown = ", ".join(f"{key}={value}" for key, value in trial["settings"].items())
        score = trial["score"]
        text = f"{score:.1f}" if isinstance(score, (int, float)) else f"failed: {trial['error']}"
        lines.append(f"  {number:>2}. {shown or 'engine defaults':<40}{text}")
    if result["error"]:
        lines.append(f"  FAILED: {result['error']}")
        return "\n".join(lines)
    if not result["changes"]:
        lines.append(
            f"  No setting beat the engine defaults by {result['min_gain_percent']:g}%; "
            "keep the defaults."
        )
    else:
        best = ", ".join(f"{key}={value}" for key, value in result["best_settings"].items())
        gain = result["improvement_percent"]
        lines.append(
            f"  Best: {best}" + (f"  ({gain:+.1f}% vs defaults)" if gain is not None else "")
        )
    lines.append(
        "  These are measurements for this machine and model. If other programs were running, "
        "run the tune again."
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localforge tune",
        description=(
            "Find the best launch settings for a managed llama.cpp by benchmarking each "
            "candidate. Exits 0 when the tune completes, 1 when it fails, 2 for a bad plan."
        ),
    )
    parser.add_argument("plan", type=Path, help="A bench plan for a managed llama.cpp.")
    parser.add_argument("--objective", choices=sorted(OBJECTIVES), help="What to optimise.")
    parser.add_argument("--out", type=Path, help="Write the result JSON here.")
    parser.add_argument("--data-dir", type=Path, help="Keep the private run database here.")
    parser.add_argument("--api", help="Use an already running LocalForge API.")
    parser.add_argument("--json", action="store_true", help="Print only the result JSON.")
    return parser


def tune_main(argv: Sequence[str]) -> int:
    arguments = build_parser().parse_args(list(argv))
    try:
        try:
            raw = json.loads(arguments.plan.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise PlanError(f"plan file not found: {arguments.plan}") from error
        except (OSError, ValueError) as error:
            raise PlanError(f"plan file {arguments.plan} is not valid JSON: {error}") from error
        plan, tune = parse_tune_plan(raw)
    except PlanError as error:
        print(f"localforge tune: {error}", file=sys.stderr)
        return EXIT_USAGE
    if arguments.objective:
        tune["objective"] = arguments.objective
    space = search_space_for(plan, tune["space"])

    def search_with(client: ApiClient) -> dict[str, Any]:
        return coordinate_search(
            space,
            make_evaluator(client, plan),
            objective=tune["objective"],
            min_gain_percent=tune["min_gain_percent"],
        )

    if arguments.api:
        search = search_with(ApiClient(arguments.api))
    else:
        data_directory = arguments.data_dir or Path(tempfile.mkdtemp(prefix="localforge-tune-"))
        with private_server(data_directory) as api:
            search = search_with(ApiClient(api))
    result = build_result(plan, tune, search)
    text = json.dumps(result, indent=2, default=str)
    if arguments.out:
        arguments.out.write_text(text + "\n", encoding="utf-8")
    print(text if arguments.json else render_summary(result))
    return EXIT_OK if result["ok"] else EXIT_FAILED
