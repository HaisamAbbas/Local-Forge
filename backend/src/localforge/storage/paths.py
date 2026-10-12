"""Platform-aware default location for LOCALFORGE's local data."""

import os
import sys
from pathlib import Path


def default_data_directory() -> Path:
    """Return the user's local application-data directory without creating it."""

    configured_directory = os.environ.get("LOCALFORGE_DATA_DIR")
    if configured_directory:
        return Path(configured_directory).expanduser()

    if sys.platform == "win32":
        base_directory = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base_directory = Path.home() / "Library" / "Application Support"
    else:
        base_directory = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    return base_directory / "LOCALFORGE"
