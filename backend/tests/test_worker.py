"""The worker has a standalone process entry point."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_worker_check_runs_in_a_separate_process() -> None:
    backend_directory = Path(__file__).resolve().parents[1]
    source_directory = backend_directory / "src"
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(source_directory)
        if not existing_pythonpath
        else str(source_directory) + os.pathsep + existing_pythonpath
    )

    result = subprocess.run(
        [sys.executable, "-m", "localforge.worker", "--check"],
        cwd=backend_directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert json.loads(result.stdout) == {
        "status": "ready",
        "worker_protocol_version": 1,
        "job_execution": "configured",
    }
