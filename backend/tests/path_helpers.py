"""Portable test helpers for filesystem redirection cases."""

import os
import subprocess
from pathlib import Path


def create_directory_link(link: Path, target: Path) -> None:
    """Create a directory symlink or, on Windows, an unprivileged NTFS junction."""

    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return

    environment = os.environ.copy()
    environment["LOCALFORGE_TEST_LINK_PATH"] = str(link)
    environment["LOCALFORGE_TEST_TARGET_PATH"] = str(target)
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                "New-Item -ItemType Junction -Path $env:LOCALFORGE_TEST_LINK_PATH "
                "-Target $env:LOCALFORGE_TEST_TARGET_PATH | Out-Null"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )
