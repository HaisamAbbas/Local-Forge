"""Benchmark a real llama-server through a real LocalForge API and check the result.

The unit tests drive fake engines, which cannot catch problems that only appear against a real
server (settings the adapter requires, model ids, token counts, native metrics). This script
starts both processes, runs a tiny benchmark, and fails if the measurements are not real.

    python scripts/ci/real_engine_smoke.py --llama-server PATH --model PATH.gguf

It needs a LocalForge environment (run it with ``uv run``) and uses throwaway ports and data.
"""

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "gpu_test" / "bench_engine.py"
EXPECTED_REQUESTS = 4
MODEL_ALIAS = "ci-model"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for(url: str, timeout: float, process: subprocess.Popen[bytes], name: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"{name} exited early with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    raise SystemExit(f"{name} did not become ready at {url} within {timeout:.0f}s")


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_benchmark(api: str, endpoint: str, out: Path) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("bench_engine", BENCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.argv = [
        "bench_engine.py",
        "--api",
        api,
        "--adapter",
        "localforge.llama_cpp",
        "--label",
        "CI llama.cpp smoke",
        "--endpoint",
        endpoint,
        "--model",
        "*",
        "--requests",
        str(EXPECTED_REQUESTS),
        "--concurrency",
        "1",
        "--max-tokens",
        "24",
        "--run-timeout",
        "600",
        "--out",
        str(out),
    ]
    module.main()
    return json.loads(out.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def check(result: dict[str, object]) -> list[str]:
    problems: list[str] = []
    if not result.get("ok"):
        problems.append(f"benchmark failed at stage {result.get('stage')}: {result.get('error')}")
        return problems
    metrics = result.get("metrics") or {}
    assert isinstance(metrics, dict)

    def value(name: str) -> float | None:
        entry = metrics.get(name)
        return entry.get("value") if isinstance(entry, dict) else None

    if value("requests_succeeded.run_total") != EXPECTED_REQUESTS:
        problems.append(f"expected {EXPECTED_REQUESTS} succeeded requests: {metrics}")
    if value("requests_failed.run_total") not in (0, 0.0):
        problems.append("some requests failed")
    for name in ("ttft_ms.p50", "request_latency_ms.p50"):
        measured = value(name)
        if measured is None or measured <= 0:
            problems.append(f"{name} was not measured")
    rate = value("generation_throughput_tokens_per_second.run_rate")
    if rate is None or rate <= 0:
        problems.append("generation throughput missing: token counts did not reach LocalForge")
    native = result.get("engine_native_metrics") or []
    if not any(str(name).startswith("llamacpp:") for name in native):  # type: ignore[union-attr]
        problems.append("no llama.cpp native metrics were collected")
    if result.get("model_ref") != MODEL_ALIAS:
        problems.append(
            f"expected model {MODEL_ALIAS!r}, engine reported {result.get('model_ref')!r}"
        )
    probe = result.get("probe") or {}
    assert isinstance(probe, dict)
    if probe.get("status") != "succeeded":
        problems.append(f"probe status was {probe.get('status')}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--llama-server", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=0, help="llama-server threads (0 = auto)")
    arguments = parser.parse_args()
    if not arguments.llama_server.is_file() or not arguments.model.is_file():
        raise SystemExit("llama-server and the model file must both exist")

    work = Path(tempfile.mkdtemp(prefix="localforge-ci-"))
    api_port, engine_port = free_port(), free_port()
    api = f"http://127.0.0.1:{api_port}"
    engine = f"http://127.0.0.1:{engine_port}"
    server: subprocess.Popen[bytes] | None = None
    llama: subprocess.Popen[bytes] | None = None
    try:
        llama_command = [
            str(arguments.llama_server),
            "-m",
            str(arguments.model),
            "--alias",
            MODEL_ALIAS,
            "-c",
            "2048",
            "--host",
            "127.0.0.1",
            "--port",
            str(engine_port),
            "--metrics",
        ]
        if arguments.threads:
            llama_command += ["-t", str(arguments.threads)]
        llama = subprocess.Popen(
            llama_command,
            stdout=(work / "llama.log").open("wb"),
            stderr=subprocess.STDOUT,
            cwd=arguments.llama_server.parent,
        )
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "localforge.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--log-level",
                "warning",
            ],
            stdout=(work / "api.log").open("wb"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "LOCALFORGE_DATA_DIR": str(work / "data")},
        )
        wait_for(f"{engine}/health", 180, llama, "llama-server")
        wait_for(f"{api}/health", 120, server, "LocalForge API")
        result = run_benchmark(api, engine, work / "result.json")
        problems = check(result)
    finally:
        stop(server)
        stop(llama)

    summary = {
        key: result.get(key) for key in ("ok", "model_ref", "run", "metrics", "run_warnings")
    }
    print(json.dumps(summary, indent=2, default=str))
    if problems:
        print("\nFAILED:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        for log in ("llama.log", "api.log"):
            tail = (work / log).read_text(encoding="utf-8", errors="replace")[-1500:]
            print(f"\n--- tail of {log} ---\n{tail}", file=sys.stderr)
        return 1
    print("\nreal-engine smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
