from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from localforge.api.main import AppSettings, create_app
from localforge.web_ui import bundled_ui_directory


def _built_ui(root: Path) -> Path:
    web = root / "web"
    (web / "assets").mkdir(parents=True)
    (web / "images").mkdir()
    (web / "index.html").write_text("<!doctype html><title>LocalForge</title>", encoding="utf-8")
    (web / "assets" / "index-abc123.js").write_text("console.log('ui');", encoding="utf-8")
    (web / "images" / "hero.jpg").write_bytes(b"\xff\xd8\xff")
    return web


def test_bundled_ui_is_served_without_shadowing_the_api(tmp_path: Path) -> None:
    app = create_app(
        AppSettings(data_directory=tmp_path / "data", web_directory=_built_ui(tmp_path))
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        page = client.get("/")
        asset = client.get("/assets/index-abc123.js")
        image = client.get("/images/hero.jpg")
        health = client.get("/api/v1/health")
        root_health = client.get("/health")
        docs = client.get("/docs")
        missing_api = client.get("/api/v1/does-not-exist")
        missing_file = client.get("/nope.js")

    assert page.status_code == 200
    assert "<title>LocalForge</title>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200
    assert "cache-control" not in asset.headers
    assert image.status_code == 200
    assert health.json()["status"] == "ok"
    assert root_health.json()["status"] == "ok"
    assert docs.status_code == 200
    assert missing_api.status_code == 404
    assert missing_file.status_code == 404


def test_api_only_when_no_ui_is_bundled(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_directory=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/v1/health").status_code == 200


def test_web_directory_override_requires_a_built_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALFORGE_WEB_DIR", str(tmp_path / "empty"))
    assert bundled_ui_directory() is None

    web = _built_ui(tmp_path)
    monkeypatch.setenv("LOCALFORGE_WEB_DIR", str(web))
    assert bundled_ui_directory() == web
