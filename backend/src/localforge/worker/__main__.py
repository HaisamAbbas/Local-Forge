"""Standalone worker diagnostic entry point."""

import argparse
import json
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOCALFORGE background worker")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the worker process can start independently",
    )
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("No background jobs are configured yet; use --check for a process check.")

    print(
        json.dumps(
            {
                "status": "ready",
                "worker_protocol_version": 1,
                "job_execution": "not_configured",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
