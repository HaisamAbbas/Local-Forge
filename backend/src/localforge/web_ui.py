"""Serve the compiled browser UI from the API process when a built copy is packaged."""

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

BUNDLED_WEB_DIRECTORY = Path(__file__).parent / "web"


def bundled_ui_directory() -> Path | None:
    """Return the built UI directory, or None when this install has no bundled UI.

    Release packages ship the UI inside ``localforge/web``. ``LOCALFORGE_WEB_DIR`` points at
    another build, for example ``frontend/dist`` while testing a release locally.
    """

    configured = os.environ.get("LOCALFORGE_WEB_DIR")
    directory = Path(configured).expanduser() if configured else BUNDLED_WEB_DIRECTORY
    return directory if (directory / "index.html").is_file() else None


class _BrowserUiFiles(StaticFiles):
    """Static files where the page shell is always revalidated so upgrades load at once.

    Hashed asset names change on every build, so only the shell needs this.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path in {"", ".", "index.html"}:
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount_browser_ui(application: FastAPI, directory: Path) -> None:
    """Serve the UI at the site root. Mount last so API, health, and docs routes win."""

    application.mount("/", _BrowserUiFiles(directory=directory, html=True), name="browser-ui")
