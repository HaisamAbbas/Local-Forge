"""Benchmark one engine through a running LocalForge API and write a JSON result.

Standard library only, so it runs with any Python 3.9+ on the GPU machine.

    python3 bench_engine.py --api http://127.0.0.1:8765 --adapter localforge.vllm \
        --label "vLLM" --endpoint http://127.0.0.1:8101 --model qwen2.5-1.5b --out vllm.json

--model "*" picks the first model the engine reports. --machine-only records the detected
machine profile (CPU, memory, GPUs) and exits.
"""

import argparse
import json
import sys
import time
import traceback
import urllib.error
import urllib.request

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
KEY_METRICS = [
    ("ttft_ms", "p50"),
    ("ttft_ms", "p95"),
    ("request_latency_ms", "p50"),
    ("request_latency_ms", "p95"),
    ("tpot_ms_per_token", "p50"),
    ("generation_throughput_tokens_per_second", "run_rate"),
    ("output_throughput_tokens_per_second", "run_rate"),
    ("request_throughput_requests_per_second", "run_rate"),
    ("requests_succeeded", "run_total"),
    ("requests_failed", "run_total"),
    ("requests_timed_out", "run_total"),
]


class ApiError(Exception):
    pass


class Client:
    def __init__(self, api: str) -> None:
        self.api = api.rstrip("/")
        self.base = self.api + "/api/v1"
        self.headers: dict[str, str] = {}

    def call(self, method: str, path: str, body: object = None, timeout: float = 900) -> object:
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"content-type": "application/json", **self.headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:2000]
            raise ApiError(f"{method} {path} -> HTTP {error.code}: {detail}") from error

    def authorize(self) -> None:
        session = self.call("GET", "/session")
        assert isinstance(session, dict)
        self.headers = {session["header_name"]: session["token"], "origin": self.api}


def unwrap(value: object, key: str) -> dict:
    if isinstance(value, dict) and key in value and isinstance(value[key], dict):
        return value[key]
    assert isinstance(value, dict)
    return value


def run_benchmark(args: argparse.Namespace, client: Client, result: dict) -> None:
    profile_body: dict[str, object] = {
        "label": args.label,
        "adapter_id": args.adapter,
        "integration_mode": args.mode,
    }
    if args.endpoint:
        profile_body["endpoint_url"] = args.endpoint
    if args.extensions:
        profile_body["engine_extensions"] = json.loads(args.extensions)
    result["stage"] = "create_profile"
    try:  # record this machine so comparisons can verify the hardware matched
        machine = client.call("PUT", "/machine-profiles/current", {})
        profile_body["machine_profile_id"] = machine["id"]
    except Exception:  # noqa: BLE001 - the benchmark is still valid without it
        pass
    profile = unwrap(client.call("POST", "/engines", profile_body), "profile")
    if args.mode == "managed_process":
        result["stage"] = "managed_start"
        started = client.call("POST", f"/engines/{profile['id']}/start", {})
        result["managed_start"] = {
            "status": unwrap(started, "profile").get("status"),
            "process_running": unwrap(started, "profile").get("managed_process_running"),
        }
    try:
        result["stage"] = "probe"
        probe = client.call("POST", f"/engines/{profile['id']}/probe", {})
        assert isinstance(probe, dict)
        probed = probe.get("profile", {})
        result["probe"] = {
            "status": probe.get("result", {}).get("status"),
            "engine_version": probed.get("engine_version"),
            "capabilities": {
                name: value.get("state")
                for name, value in sorted((probed.get("capabilities") or {}).items())
            },
            "diagnostics": probe.get("result", {}).get("diagnostics"),
        }
        result["stage"] = "enable"
        client.call("POST", f"/engines/{profile['id']}/enable", {})
        result["stage"] = "models"
        models = client.call("GET", f"/engines/{profile['id']}/models")
        assert isinstance(models, list)
        result["models"] = [m.get("model_ref") for m in models]
        available = [m for m in models if m.get("status") != "missing"]
        if args.model == "*":
            model = available[0] if available else None
        else:
            model = next((m for m in available if m.get("model_ref") == args.model), None)
        if model is None:
            raise ApiError(f"model {args.model!r} not reported; engine listed {result['models']}")
        result["model_ref"] = model.get("model_ref")

        result["stage"] = "workload"
        workload = unwrap(
            client.call(
                "POST",
                "/workloads/presets",
                {
                    "name": f"{args.label} GPU smoke",
                    "preset": "mixed_chat",
                    "request_count": args.requests,
                    "concurrency": args.concurrency,
                    "max_output_tokens": args.max_tokens,
                    "warmup_count": 2,
                },
            ),
            "workload",
        )
        result["stage"] = "plan"
        created = client.call(
            "POST",
            "/benchmark-plans",
            {
                "name": f"{args.label} x {model.get('model_ref')}",
                "engine_profile_id": profile["id"],
                "model_artifact_id": model["id"],
                "workload_id": workload["id"],
            },
        )
        assert isinstance(created, dict)
        preflight = created.get("preflight") or {}
        plan = unwrap(created, "plan")
        result["preflight"] = {
            "status": plan.get("status"),
            "blockers": [b.get("code") for b in preflight.get("blockers", [])],
            "warnings": [w.get("code") for w in preflight.get("warnings", [])],
        }
        if plan.get("status") != "ready":
            raise ApiError(f"plan blocked: {result['preflight']['blockers']}")

        result["stage"] = "run"
        run = unwrap(client.call("POST", "/runs", {"plan_id": plan["id"]}), "run")
        started_at = time.monotonic()
        while True:
            current = client.call("GET", f"/runs/{run['id']}")
            assert isinstance(current, dict)
            if current.get("status") in TERMINAL:
                break
            if time.monotonic() - started_at > args.run_timeout:
                client.call("POST", f"/runs/{run['id']}/cancel", {})
                raise ApiError(f"run exceeded {args.run_timeout}s and was cancelled")
            time.sleep(3)
        result["run"] = {
            "id": run["id"],
            "status": current.get("status"),
            "seconds": round(time.monotonic() - started_at, 1),
            "error_code": current.get("error_code"),
            "error_message": current.get("error_message"),
        }
        metrics = client.call("GET", f"/runs/{run['id']}/metrics")
        assert isinstance(metrics, dict)
        rows = metrics.get("metrics", [])
        summary = {}
        for name, aggregation in KEY_METRICS:
            match = next(
                (
                    m
                    for m in rows
                    if m["metric_name"] == name
                    and m["aggregation"] == aggregation
                    and "request_id" not in m.get("labels", {})
                ),
                None,
            )
            summary[f"{name}.{aggregation}"] = (
                None
                if match is None
                else {"value": match["value"], "flags": match.get("quality_flags", [])}
            )
        result["metrics"] = summary
        result["engine_native_metrics"] = sorted(
            {
                m["metric_name"]
                for m in rows
                if m["source"] == "engine_native" and m["value"] is not None
            }
        )
        events: list[dict] = []
        after = -1
        while True:  # the API pages events 1000 at a time
            page = client.call("GET", f"/runs/{run['id']}/events?after={after}&limit=1000")
            assert isinstance(page, dict)
            events.extend(page.get("events", []))
            if page.get("next_sequence_number") is None or not page.get("events"):
                break
            after = page["events"][-1]["sequence_number"]
        warnings: dict[str, int] = {}
        for event in events:
            if event.get("event_type") == "warning":
                code = str(event.get("payload", {}).get("code"))
                warnings[code] = warnings.get(code, 0) + 1
        result["run_warnings"] = warnings
        result["stage"] = "done"
        result["ok"] = current.get("status") == "completed"
    finally:
        if args.mode == "managed_process":
            try:
                stopped = client.call("POST", f"/engines/{profile['id']}/stop", {})
                result["managed_stop"] = {
                    "status": unwrap(stopped, "profile").get("status"),
                    "process_running": unwrap(stopped, "profile").get("managed_process_running"),
                }
            except Exception as error:  # noqa: BLE001 - recorded for the report
                result["managed_stop_error"] = str(error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    parser.add_argument("--adapter")
    parser.add_argument("--label")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--mode", default="attached_endpoint")
    parser.add_argument("--model", default="*")
    parser.add_argument("--extensions", default="", help="engine_extensions JSON")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--run-timeout", type=float, default=1800)
    parser.add_argument("--machine-only", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    client = Client(args.api)
    result: dict[str, object] = {"adapter": args.adapter, "label": args.label, "ok": False}
    try:
        client.authorize()
        if args.machine_only:
            machine = client.call("PUT", "/machine-profiles/current", {})
            result = {"machine_profile": machine, "ok": True}
        else:
            run_benchmark(args, client, result)
    except Exception as error:  # noqa: BLE001 - every failure belongs in the report
        result["error"] = str(error)
        result["traceback"] = traceback.format_exc()[-4000:]
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, default=str)
    print(json.dumps({k: result.get(k) for k in ("label", "ok", "stage", "error")}, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
