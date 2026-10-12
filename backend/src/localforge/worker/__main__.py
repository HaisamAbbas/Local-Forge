"""Standalone CLI for independently owned benchmark workers."""

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from localforge.worker.execution import execute_run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOCALFORGE background worker")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the worker process can start independently",
    )
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--data-directory", type=Path)
    parser.add_argument("--app-instance-id")
    parser.add_argument("--worker-id")
    arguments = parser.parse_args(argv)
    if arguments.check:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "worker_protocol_version": 1,
                    "job_execution": "configured",
                }
            )
        )
        return 0
    required = (
        arguments.run_id,
        arguments.database,
        arguments.data_directory,
        arguments.app_instance_id,
        arguments.worker_id,
    )
    if any(value is None for value in required):
        parser.error("Run workers require --run-id, --database, --data-directory, and worker IDs.")
    return asyncio.run(
        execute_run(
            run_id=arguments.run_id,
            database_path=arguments.database,
            data_directory=arguments.data_directory,
            app_instance_id=arguments.app_instance_id,
            worker_id=arguments.worker_id,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
