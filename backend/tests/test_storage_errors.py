import errno
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app, is_storage_full_error


def _app_with_failing_route(tmp_path: Path, error: Exception) -> TestClient:
    app = create_app(AppSettings(data_directory=tmp_path))

    @app.get("/api/v1/test-storage-failure")
    def failing_route() -> None:
        raise error

    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("database or disk is full"),
        OSError(errno.ENOSPC, "No space left on device"),
    ],
)
def test_disk_full_errors_return_storage_full(tmp_path: Path, error: Exception) -> None:
    with _app_with_failing_route(tmp_path, error) as client:
        response = client.get("/api/v1/test-storage-failure")

    assert response.status_code == 507
    assert response.json()["detail"]["code"] == "storage_full"


def test_other_storage_errors_stay_generic(tmp_path: Path) -> None:
    with _app_with_failing_route(tmp_path, sqlite3.OperationalError("no such table: x")) as client:
        response = client.get("/api/v1/test-storage-failure")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "internal_error"
    assert "no such table" not in response.text


def test_windows_disk_full_error_is_detected() -> None:
    error = OSError(0, "There is not enough space on the disk")
    error.winerror = 112  # type: ignore[attr-defined]

    assert is_storage_full_error(error)
    assert not is_storage_full_error(OSError(errno.ENOENT, "missing"))
