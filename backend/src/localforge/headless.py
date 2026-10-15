"""``localforge bench``: run a benchmark plan without the browser, for scripts and CI.

A plan file describes an engine, a model and a workload. The command starts a private LocalForge
API (or uses one you point it at), runs the plan, prints the measurements as JSON, and exits
non-zero when the run fails or a metric regresses against a saved baseline result.

    localforge bench plan.json --out result.json
    localforge bench plan.json --baseline last-good.json --tolerance 10
"""

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

RESULT_SCHEMA = "localforge.bench.v1"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
DEFAULT_TOLERANCE_PERCENT = 10.0

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# (metric_name, aggregation) -> True when a higher value is better.
KEY_METRICS: dict[tuple[str, str], bool] = {
    ("ttft_ms", "p50"): False,
    ("ttft_ms", "p95"): False,
    ("request_latency_ms", "p50"): False,
    ("request_latency_ms", "p95"): False,
    ("tpot_ms_per_token", "p50"): False,
    ("generation_throughput_tokens_per_second", "run_rate"): True,
    ("output_throughput_tokens_per_second", "run_rate"): True,
    ("request_throughput_requests_per_second", "run_rate"): True,
    ("error_rate_percent", "run_rate"): False,
}
COUNT_METRICS = (
    ("requests_attempted", "run_total"),
    ("requests_succeeded", "run_total"),
    ("requests_failed", "run_total"),
    ("requests_timed_out", "run_total"),
)


class PlanError(ValueError):
    """The plan file is unusable; the message says what to fix."""


class BenchError(RuntimeError):
    """The benchmark could not be completed."""


# --- Plan file ---------------------------------------------------------------------------


def _bounded_int(section: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PlanError(f"workload.{key} must be a whole number from {low} to {high}")
    return value


def parse_plan(raw: object) -> dict[str, Any]:
    """Validate a plan and fill defaults; raises PlanError with a plain explanation."""

    if not isinstance(raw, dict):
        raise PlanError("the plan must be a JSON object")
    unknown = set(raw) - {"label", "engine", "model", "workload", "run_timeout_seconds"}
    if unknown:
        raise PlanError(f"unknown plan field(s): {', '.join(sorted(unknown))}")
    engine = raw.get("engine")
    if not isinstance(engine, dict):
        raise PlanError('"engine" is required, e.g. {"adapter": "localforge.ollama", ...}')
    unknown_engine = set(engine) - {"adapter", "endpoint", "mode", "extensions"}
    if unknown_engine:
        raise PlanError(f"unknown engine field(s): {', '.join(sorted(unknown_engine))}")
    adapter = engine.get("adapter")
    if not isinstance(adapter, str) or not adapter.startswith("localforge."):
        raise PlanError('engine.adapter must be an adapter id such as "localforge.ollama"')
    endpoint = engine.get("endpoint", "")
    if not isinstance(endpoint, str):
        raise PlanError("engine.endpoint must be a URL string")
    mode = engine.get("mode", "attached_endpoint")
    if mode not in {"attached_endpoint", "managed_process", "library_pipeline"}:
        raise PlanError(
            "engine.mode must be attached_endpoint, managed_process or library_pipeline"
        )
    extensions = engine.get("extensions", {})
    if not isinstance(extensions, dict):
        raise PlanError("engine.extensions must be an object")
    model = raw.get("model", "*")
    if not isinstance(model, str) or not model:
        raise PlanError('model must be a model id, or "*" for the first model the engine lists')
    workload = raw.get("workload", {})
    if not isinstance(workload, dict):
        raise PlanError("workload must be an object")
    unknown_workload = set(workload) - {
        "preset",
        "requests",
        "concurrency",
        "max_output_tokens",
        "warmup",
    }
    if unknown_workload:
        raise PlanError(f"unknown workload field(s): {', '.join(sorted(unknown_workload))}")
    preset = workload.get("preset", "mixed_chat")
    if not isinstance(preset, str):
        raise PlanError("workload.preset must be a preset id such as mixed_chat")
    timeout = raw.get("run_timeout_seconds", 1800)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 10:
        raise PlanError("run_timeout_seconds must be a number of at least 10")
    label = raw.get("label", f"{adapter} bench")
    if not isinstance(label, str) or not 1 <= len(label) <= 120:
        raise PlanError("label must be 1 to 120 characters")
    return {
        "label": label,
        "engine": {
            "adapter": adapter,
            "endpoint": endpoint,
            "mode": mode,
            "extensions": extensions,
        },
        "model": model,
        "workload": {
            "preset": preset,
            "requests": _bounded_int(workload, "requests", 20, 1, 10_000),
            "concurrency": _bounded_int(workload, "concurrency", 1, 1, 1_000),
            "max_output_tokens": _bounded_int(workload, "max_output_tokens", 128, 1, 100_000),
            "warmup": _bounded_int(workload, "warmup", 2, 0, 1_000),
        },
        "run_timeout_seconds": float(timeout),
    }


def load_plan(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PlanError(f"plan file not found: {path}") from error
    except (OSError, ValueError) as error:
        raise PlanError(f"plan file {path} is not valid JSON: {error}") from error
    return parse_plan(raw)


# --- Baseline comparison -----------------------------------------------------------------


def _metric_value(result: dict[str, Any], key: str) -> float | None:
    entry = (result.get("metrics") or {}).get(key)
    value = entry.get("value") if isinstance(entry, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def compare_to_baseline(
    result: dict[str, Any], baseline: dict[str, Any], tolerance_percent: float
) -> list[dict[str, Any]]:
    """Check each metric the baseline has against this run; worse than the tolerance fails.

    A metric the baseline measured but this run did not is a failure, never a silent pass: a
    gate that cannot verify a metric must say so.
    """

    checks: list[dict[str, Any]] = []
    for (name, aggregation), higher_is_better in KEY_METRICS.items():
        key = f"{name}.{aggregation}"
        before = _metric_value(baseline, key)
        if before is None:
            continue
        after = _metric_value(result, key)
        check: dict[str, Any] = {
            "metric": key,
            "baseline": before,
            "current": after,
            "limit_percent": tolerance_percent,
            "direction": "higher_is_better" if higher_is_better else "lower_is_better",
        }
        if after is None:
            check.update(change_percent=None, status="missing")
        elif before == 0:
            # A relative change from zero is undefined. Zero errors is a real baseline, so any
            # error is a regression; a zero time or rate is measurement noise, not a baseline.
            if name == "error_rate_percent":
                check.update(change_percent=None, status="regressed" if after > 0 else "ok")
            else:
                check.update(change_percent=None, status="skipped")
        else:
            change = (after - before) / abs(before) * 100
            worse_by = -change if higher_is_better else change
            check.update(
                change_percent=round(change, 2),
                status="regressed" if worse_by > tolerance_percent else "ok",
            )
        checks.append(check)
    return checks


def failures_from(result: dict[str, Any], checks: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    run = result.get("run") or {}
    if run.get("status") != "completed":
        message = run.get("error_message") or run.get("error_code") or "no details"
        problems.append(f"run {run.get('status') or 'did not start'}: {message}")
    failed = _metric_value(result, "requests_failed.run_total")
    attempted = _metric_value(result, "requests_attempted.run_total")
    if failed:
        problems.append(f"{failed:g} of {attempted:g} measured requests failed")
    for check in checks:
        if check["status"] == "regressed":
            change = check["change_percent"]
            how = "regressed" if change is None else f"regressed {change:+.1f}%"
            limit = "" if change is None else f", limit {check['limit_percent']:g}%"
            problems.append(
                f"{check['metric']} {how} ({check['baseline']:g} -> {check['current']:g}{limit})"
            )
        elif check["status"] == "missing":
            problems.append(f"{check['metric']} was not measured but the baseline has it")
    return problems


# --- API client and private server ---------------------------------------------------------


class ApiClient:
    def __init__(self, api: str) -> None:
        self.api = api.rstrip("/")
        self.base = self.api + "/api/v1"
        self.headers: dict[str, str] = {}

    def call(self, method: str, path: str, body: object = None, timeout: float = 900) -> Any:
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"content-type": "application/json", **self.headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:600]
            raise BenchError(f"{method} {path} -> HTTP {error.code}: {detail}") from error
        except OSError as error:
            raise BenchError(f"cannot reach LocalForge at {self.api}: {error}") from error
        return json.loads(raw) if raw else None

    def authorize(self) -> None:
        session = self.call("GET", "/session")
        self.headers = {session["header_name"]: session["token"], "origin": self.api}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextlib.contextmanager
def private_server(data_directory: Path) -> Iterator[str]:
    """Run a LocalForge API on a free loopback port for the duration of the block."""

    data_directory.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    api = f"http://127.0.0.1:{port}"
    log_path = data_directory / "bench-api.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "localforge.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "LOCALFORGE_DATA_DIR": str(data_directory)},
        )
        try:
            deadline = time.monotonic() + 120
            while True:
                if process.poll() is not None:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
                    raise BenchError(f"the private LocalForge API exited early:\n{tail}")
                try:
                    with urllib.request.urlopen(f"{api}/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise BenchError("the private LocalForge API did not start within 120 s")
                time.sleep(0.5)
            yield api
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


# --- Running a plan ------------------------------------------------------------------------


def _unwrap(value: Any, key: str) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get(key), dict):
        return value[key]  # type: ignore[no-any-return]
    return value  # type: ignore[no-any-return]


def run_plan(client: ApiClient, plan: dict[str, Any]) -> dict[str, Any]:
    """Create the engine, workload and plan, run it, and collect the measurements."""

    client.authorize()
    engine_spec = plan["engine"]
    body: dict[str, Any] = {
        "label": plan["label"],
        "adapter_id": engine_spec["adapter"],
        "integration_mode": engine_spec["mode"],
    }
    if engine_spec["endpoint"]:
        body["endpoint_url"] = engine_spec["endpoint"]
    if engine_spec["extensions"]:
        body["engine_extensions"] = engine_spec["extensions"]
    result: dict[str, Any] = {"schema": RESULT_SCHEMA, "label": plan["label"]}
    profile = _unwrap(client.call("POST", "/engines", body), "profile")
    started_managed = False
    try:
        if engine_spec["mode"] == "managed_process":
            client.call("POST", f"/engines/{profile['id']}/start", {})
            started_managed = True
        probe = client.call("POST", f"/engines/{profile['id']}/probe", {})
        probed = probe.get("profile", {})
        problems = [
            item.get("message")
            for item in (probe.get("result", {}).get("diagnostics") or [])
            if item.get("severity") == "error"
        ]
        result["engine"] = {
            "adapter": engine_spec["adapter"],
            "version": probed.get("engine_version"),
            "probe": probe.get("result", {}).get("status"),
            "capabilities": {
                name: value.get("state")
                for name, value in sorted((probed.get("capabilities") or {}).items())
            },
        }
        if probe.get("result", {}).get("status") != "succeeded":
            raise BenchError("the engine probe failed: " + ("; ".join(problems) or "no details"))
        client.call("POST", f"/engines/{profile['id']}/enable", {})
        models = client.call("GET", f"/engines/{profile['id']}/models")
        available = [item for item in models if item.get("status") != "missing"]
        wanted = plan["model"]
        model = (
            available[0]
            if wanted == "*" and available
            else next((item for item in available if item.get("model_ref") == wanted), None)
        )
        if model is None:
            listed = [item.get("model_ref") for item in models]
            raise BenchError(f"model {wanted!r} is not available; the engine lists {listed}")
        result["model_ref"] = model.get("model_ref")

        workload_spec = plan["workload"]
        workload = _unwrap(
            client.call(
                "POST",
                "/workloads/presets",
                {
                    "name": f"{plan['label']} workload",
                    "preset": workload_spec["preset"],
                    "request_count": workload_spec["requests"],
                    "concurrency": workload_spec["concurrency"],
                    "max_output_tokens": workload_spec["max_output_tokens"],
                    "warmup_count": workload_spec["warmup"],
                },
            ),
            "workload",
        )
        created = client.call(
            "POST",
            "/benchmark-plans",
            {
                "name": plan["label"],
                "engine_profile_id": profile["id"],
                "model_artifact_id": model["id"],
                "workload_id": workload["id"],
            },
        )
        blockers = [b.get("message") for b in (created.get("preflight") or {}).get("blockers", [])]
        saved = _unwrap(created, "plan")
        if saved.get("status") != "ready":
            raise BenchError("the plan is blocked: " + ("; ".join(blockers) or "no details"))
        run = _unwrap(client.call("POST", "/runs", {"plan_id": saved["id"]}), "run")
        started = time.monotonic()
        while True:
            current = client.call("GET", f"/runs/{run['id']}")
            if current.get("status") in TERMINAL_STATUSES:
                break
            if time.monotonic() - started > plan["run_timeout_seconds"]:
                client.call("POST", f"/runs/{run['id']}/cancel", {})
                raise BenchError(f"the run exceeded {plan['run_timeout_seconds']:g} s")
            time.sleep(2)
        result["run"] = {
            "id": run["id"],
            "status": current.get("status"),
            "seconds": round(time.monotonic() - started, 1),
            "error_code": current.get("error_code"),
            "error_message": current.get("error_message"),
        }
        records = client.call("GET", f"/runs/{run['id']}/metrics").get("metrics", [])
        summary: dict[str, Any] = {}
        for name, aggregation in [*KEY_METRICS, *COUNT_METRICS]:
            match = next(
                (
                    record
                    for record in records
                    if record["metric_name"] == name
                    and record["aggregation"] == aggregation
                    and "request_id" not in record.get("labels", {})
                ),
                None,
            )
            summary[f"{name}.{aggregation}"] = (
                None
                if match is None
                else {"value": match["value"], "flags": match.get("quality_flags", [])}
            )
        result["metrics"] = summary
        return result
    finally:
        if started_managed:
            with contextlib.suppress(BenchError):
                client.call("POST", f"/engines/{profile['id']}/stop", {})


# --- Command line --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localforge bench",
        description=(
            "Run a benchmark plan from a JSON file without the browser. Exits 0 when the run "
            "completes (and stays within tolerance of --baseline), 1 when it fails or regresses, "
            "and 2 for a bad plan or arguments."
        ),
    )
    parser.add_argument("plan", type=Path, help="Plan file (engine, model, workload).")
    parser.add_argument("--out", type=Path, help="Write the result JSON here.")
    parser.add_argument("--baseline", type=Path, help="A previous result to compare against.")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE_PERCENT,
        help=f"Allowed worsening in percent (default {DEFAULT_TOLERANCE_PERCENT:g}).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Keep the private run database here (default: a throwaway folder).",
    )
    parser.add_argument(
        "--api", help="Use an already running LocalForge API instead of starting one."
    )
    parser.add_argument("--json", action="store_true", help="Print only the result JSON.")
    return parser


def bench_main(argv: Sequence[str]) -> int:
    arguments = build_parser().parse_args(list(argv))
    if arguments.tolerance < 0:
        print("localforge bench: --tolerance cannot be negative", file=sys.stderr)
        return EXIT_USAGE
    try:
        plan = load_plan(arguments.plan)
        baseline = None
        if arguments.baseline is not None:
            try:
                baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise PlanError(f"baseline {arguments.baseline} is unusable: {error}") from error
    except PlanError as error:
        print(f"localforge bench: {error}", file=sys.stderr)
        return EXIT_USAGE

    result: dict[str, Any]
    try:
        if arguments.api:
            result = run_plan(ApiClient(arguments.api), plan)
        else:
            data_directory = arguments.data_dir or Path(
                tempfile.mkdtemp(prefix="localforge-bench-")
            )
            with private_server(data_directory) as api:
                result = run_plan(ApiClient(api), plan)
    except BenchError as error:
        result = {"schema": RESULT_SCHEMA, "label": plan["label"], "error": str(error)}
        checks: list[dict[str, Any]] = []
        problems = [str(error)]
    else:
        checks = compare_to_baseline(result, baseline, arguments.tolerance) if baseline else []
        problems = failures_from(result, checks)
    result["checks"] = checks
    result["failures"] = problems
    result["ok"] = not problems
    text = json.dumps(result, indent=2, default=str)
    if arguments.out:
        arguments.out.write_text(text + "\n", encoding="utf-8")
    if arguments.json:
        print(text)
    else:
        print(render_summary(result))
    return EXIT_OK if result["ok"] else EXIT_FAILED


def render_summary(result: dict[str, Any]) -> str:
    lines = [f"LocalForge bench: {result.get('label')}"]
    engine = result.get("engine") or {}
    if engine:
        lines.append(f"  engine  {engine.get('adapter')} {engine.get('version') or ''}".rstrip())
    if result.get("model_ref"):
        lines.append(f"  model   {result['model_ref']}")
    for key in (
        "ttft_ms.p50",
        "request_latency_ms.p50",
        "generation_throughput_tokens_per_second.run_rate",
    ):
        value = _metric_value(result, key)
        if value is not None:
            lines.append(f"  {key:<56}{value:>12.2f}")
    for check in result.get("checks", []):
        change = check["change_percent"]
        note = "n/a" if change is None else f"{change:+.1f}%"
        lines.append(f"  vs baseline {check['metric']:<44}{note:>8}  {check['status']}")
    lines.append("  PASS" if result["ok"] else "  FAIL")
    for problem in result["failures"]:
        lines.append(f"    - {problem}")
    return "\n".join(lines)
