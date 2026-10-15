"""``localforge``: start the local API with the bundled browser UI and open it."""

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from localforge import __version__
from localforge.core.urls import is_loopback_host
from localforge.web_ui import bundled_ui_directory

if TYPE_CHECKING:
    from localforge.recommendations import RecommendationResponse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localforge",
        description=(
            "Run LocalForge, the local LLM inference benchmarking lab, and open it in your "
            "browser. Everything stays on this machine."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port to listen on (default {DEFAULT_PORT}, or a free port if it is taken).",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Loopback address to listen on (default 127.0.0.1). Other hosts are refused.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Folder for the LocalForge database and datasets (default: your app-data folder).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    parser.add_argument("--version", action="version", version=f"localforge {__version__}")
    parser.epilog = (
        "Run `localforge recommend` to rank inference engines for this machine "
        "(add --json for machine-readable output), or `localforge bench plan.json` to run a "
        "benchmark from a file without the browser (for scripts and CI), or "
        "`localforge tune plan.json` to find the best launch settings for a managed llama.cpp."
    )
    return parser


def port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def free_port(host: str) -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def choose_port(host: str, requested: int | None) -> int:
    """Use the requested port, or the default when free, otherwise any free port."""

    if requested is not None:
        if not port_is_free(host, requested):
            raise SystemExit(f"localforge: port {requested} is already in use on {host}.")
        return requested
    return DEFAULT_PORT if port_is_free(host, DEFAULT_PORT) else free_port(host)


def browser_url(host: str, port: int) -> str:
    return f"http://[{host}]:{port}/" if ":" in host else f"http://{host}:{port}/"


def open_when_ready(url: str, open_browser: Callable[[str], object] = webbrowser.open) -> None:
    """Open the browser once the API answers, so the first page load does not fail."""

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}health", timeout=1) as response:
                if response.status == 200:
                    open_browser(url)
                    return
        except OSError:
            pass
        time.sleep(0.25)


def build_recommend_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localforge recommend",
        description=(
            "Rank inference engines by fit for this machine's hardware and what is already "
            "installed. Works without the LocalForge server running."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="LocalForge data folder to read saved profiles and measured runs from.",
    )
    return parser


def _print(text: str = "") -> None:
    """Print, replacing characters the console encoding cannot show."""

    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, "replace").decode(encoding))


def _data_directory(requested: Path | None) -> Path:
    if requested is not None:
        return requested.expanduser().resolve()
    from localforge.storage.paths import default_data_directory

    return default_data_directory()


def recommend_main(argv: Sequence[str]) -> int:
    args = build_recommend_parser().parse_args(argv)
    from localforge.recommendations import recommend_for_data_directory, render_text

    response = recommend_for_data_directory(_data_directory(args.data_dir))
    if args.json:
        _print(response.model_dump_json(indent=2))
    else:
        for line in render_text(response):
            _print(line)
        for line in _model_suggestion_lines(_data_directory(args.data_dir), response):
            _print(line)
    return 0


def _model_suggestion_lines(data_directory: Path, response: "RecommendationResponse") -> list[str]:
    """The "Models to try" section (top 3 for chat and coding); empty if it cannot be built."""

    try:
        from localforge import model_catalog

        suggestions = model_catalog.suggestions_for_data_directory(
            data_directory,
            response,
        )
        return ["", *model_catalog.render_model_suggestions_text(suggestions, limit=3)]
    except Exception:
        return []


STARTUP_RECOMMENDATION_SECONDS = 3.0
STARTUP_TOP_ENGINES = 5


def startup_recommendation_lines(data_directory: Path, first_run: bool, port: int) -> list[str]:
    """Lines to print before the server starts; empty on any error or after ~3 seconds."""

    result: list[str] = []

    def compute() -> None:
        from localforge.recommendations import (
            best_fit_line,
            recommend_for_data_directory,
            render_text,
        )

        response = recommend_for_data_directory(
            data_directory, self_ports=(port,), discovery_budget_seconds=2.0
        )
        if first_run:
            result.extend([*render_text(response, limit=STARTUP_TOP_ENGINES), ""])
        else:
            line = best_fit_line(response)
            if line:
                result.append(line)

    def guarded() -> None:
        try:
            compute()
        except Exception:
            result.clear()

    worker = threading.Thread(target=guarded, daemon=True)
    worker.start()
    worker.join(STARTUP_RECOMMENDATION_SECONDS)
    return list(result) if not worker.is_alive() else []


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["recommend"]:
        return recommend_main(arguments[1:])
    if arguments[:1] == ["tune"]:
        from localforge.tune import tune_main

        return tune_main(arguments[1:])
    if arguments[:1] == ["bench"]:
        from localforge.headless import bench_main

        return bench_main(arguments[1:])
    parser = build_parser()
    args = parser.parse_args(arguments)
    if not is_loopback_host(args.host):
        parser.error(
            "LocalForge only listens on loopback addresses (for example 127.0.0.1) so its "
            "control API is never exposed to other machines."
        )
    if args.data_dir is not None:
        # The API and its benchmark worker processes both read this.
        os.environ["LOCALFORGE_DATA_DIR"] = str(args.data_dir.expanduser().resolve())

    data_directory = _data_directory(args.data_dir)
    first_run = not (data_directory / "localforge.sqlite3").exists()
    port = choose_port(args.host, args.port)
    url = browser_url(args.host, port)
    has_ui = bundled_ui_directory() is not None

    for line in startup_recommendation_lines(data_directory, first_run, port):
        _print(line)
    print(f"LocalForge {__version__} is running at {url}")
    if not has_ui:
        print(
            "  This install has no bundled browser UI; only the API is served. "
            "Use a release package, or run the frontend dev server."
        )
    print("  Press Ctrl+C to stop.", flush=True)
    if has_ui and not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()

    import uvicorn

    uvicorn.run(
        "localforge.api.main:create_app",
        factory=True,
        host=args.host,
        port=port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
