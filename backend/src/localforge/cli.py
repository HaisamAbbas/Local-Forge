"""``localforge``: start the local API with the bundled browser UI and open it."""

import argparse
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path

from localforge import __version__
from localforge.core.urls import is_loopback_host
from localforge.web_ui import bundled_ui_directory

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not is_loopback_host(args.host):
        parser.error(
            "LocalForge only listens on loopback addresses (for example 127.0.0.1) so its "
            "control API is never exposed to other machines."
        )
    if args.data_dir is not None:
        # The API and its benchmark worker processes both read this.
        os.environ["LOCALFORGE_DATA_DIR"] = str(args.data_dir.expanduser().resolve())

    port = choose_port(args.host, args.port)
    url = browser_url(args.host, port)
    has_ui = bundled_ui_directory() is not None

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
