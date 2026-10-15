"""FastAPI application factory and initial health route."""

import asyncio
import errno
import logging
import os
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from localforge import __version__
from localforge.adapters.catalog import AdapterCatalog, default_adapter_catalog
from localforge.adapters.contracts import AdapterManifest, AdapterProcessHandle
from localforge.api.experiments import router as experiment_router
from localforge.api.hardware_fit import router as hardware_fit_router
from localforge.api.model_suggestions import router as model_suggestion_router
from localforge.api.playground import router as playground_router
from localforge.api.profiles import router as profile_router
from localforge.api.recommendations import router as recommendation_router
from localforge.api.repeats import router as repeat_router
from localforge.api.runs import (
    monitor_run_workers,
    shutdown_run_workers,
)
from localforge.api.runs import (
    router as run_router,
)
from localforge.api.schemas import LocalSessionResponse
from localforge.api.settings import router as settings_router
from localforge.api.settings import visible_catalog_manifests
from localforge.api.targets import router as targets_router
from localforge.api.tunes import router as tune_router
from localforge.api.workloads import router as workload_router
from localforge.core.models import EngineIntegrationMode, EngineProfile, EngineProfileStatus
from localforge.metrics import backfill_engine_native_metrics_for_finalized_runs
from localforge.runs import recover_runs_on_startup
from localforge.security.local_session import (
    SESSION_HEADER_NAME,
    create_local_session_token,
    is_loopback_origin,
    is_loopback_request_host,
    local_session_token_matches,
)
from localforge.storage.database import initialize_database, open_database
from localforge.storage.paths import default_data_directory
from localforge.storage.repositories import RecordRepository
from localforge.web_ui import bundled_ui_directory, mount_browser_ui

MAX_DATASET_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


_DISK_FULL_ERRNOS = frozenset(
    code for code in (errno.ENOSPC, getattr(errno, "EDQUOT", None)) if code is not None
)
# ERROR_HANDLE_DISK_FULL and ERROR_DISK_FULL.
_DISK_FULL_WINERRORS = frozenset({39, 112})


def is_storage_full_error(error: BaseException) -> bool:
    """Return whether an exception means the data volume has no space left."""

    if isinstance(error, sqlite3.Error):
        if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            return True
        return "disk is full" in str(error).lower()
    if isinstance(error, OSError):
        return (
            error.errno in _DISK_FULL_ERRNOS
            or getattr(error, "winerror", None) in _DISK_FULL_WINERRORS
        )
    return False


def _disable_unowned_managed_profiles(connection: sqlite3.Connection) -> None:
    repository = RecordRepository(connection, EngineProfile)
    for profile in repository.list():
        if (
            profile.integration_mode is EngineIntegrationMode.MANAGED_PROCESS
            and profile.status is EngineProfileStatus.ENABLED
        ):
            repository.update(
                EngineProfile.model_validate(
                    profile.model_dump(mode="python") | {"status": EngineProfileStatus.DISABLED}
                )
            )


class DatasetUploadBodyLimitMiddleware:
    """Bound multipart bytes before FastAPI spools uploaded files to temporary storage."""

    def __init__(self, app: ASGIApp, *, max_upload_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_upload_bytes + MAX_DATASET_MULTIPART_OVERHEAD_BYTES
        self.error_body = JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "dataset_too_large",
                    "message": "Dataset upload exceeds the configured request-size limit.",
                }
            },
        ).body
        self.error_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(self.error_body)).encode("ascii")),
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/datasets/import"
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._send_error(send)
                    return
            except ValueError:
                pass

        received_bytes = 0
        exceeded = False
        error_sent = False

        async def limited_receive() -> Message:
            nonlocal received_bytes, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message: Message) -> None:
            nonlocal error_sent
            if exceeded:
                if not error_sent and message["type"] == "http.response.start":
                    error_sent = True
                    await self._send_error(send)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not error_sent:
            await self._send_error(send)

    async def _send_error(self, send: Send) -> None:
        await send({"type": "http.response.start", "status": 413, "headers": self.error_headers})
        await send({"type": "http.response.body", "body": self.error_body})


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings that can be overridden in tests and local development."""

    data_directory: Path
    adapter_catalog_factory: Callable[[], AdapterCatalog] = default_adapter_catalog
    session_token_factory: Callable[[], str] = create_local_session_token
    max_dataset_upload_bytes: int = 100 * 1024 * 1024
    # A built browser UI to serve at "/"; None leaves the UI to the Vite dev server.
    web_directory: Path | None = None

    def __post_init__(self) -> None:
        if self.max_dataset_upload_bytes < 1:
            raise ValueError("max_dataset_upload_bytes must be a positive integer")

    @classmethod
    def from_environment(cls) -> "AppSettings":
        raw_limit = os.environ.get("LOCALFORGE_MAX_DATASET_UPLOAD_BYTES", str(100 * 1024 * 1024))
        try:
            maximum_upload_bytes = int(raw_limit)
        except ValueError as error:
            raise ValueError(
                "LOCALFORGE_MAX_DATASET_UPLOAD_BYTES must be a positive integer"
            ) from error
        return cls(
            data_directory=default_data_directory(),
            max_dataset_upload_bytes=maximum_upload_bytes,
            web_directory=bundled_ui_directory(),
        )


class DatabaseHealth(BaseModel):
    status: Literal["ready"]
    schema_version: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    api_version: Literal["v1"]
    app_version: str
    database: DatabaseHealth


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Create the API and initialize its local database during application startup."""

    app_settings = settings or AppSettings.from_environment()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database_path = app_settings.data_directory / "localforge.sqlite3"
        schema_version = initialize_database(database_path)
        app_instance_id = str(uuid4())
        recovery_connection = open_database(database_path)
        try:
            _disable_unowned_managed_profiles(recovery_connection)
            recover_runs_on_startup(
                recovery_connection,
                app_instance_id=app_instance_id,
            )
            try:
                backfill_engine_native_metrics_for_finalized_runs(recovery_connection)
            except (sqlite3.Error, ValueError):
                # Backfilled aggregates are additive conveniences; startup must not depend on them.
                logger.warning("Engine-native metric backfill was skipped.", exc_info=True)
        finally:
            recovery_connection.close()
        adapter_catalog = app_settings.adapter_catalog_factory()
        application.state.database_schema_version = schema_version
        application.state.database_path = database_path
        application.state.data_directory = app_settings.data_directory
        application.state.max_dataset_upload_bytes = app_settings.max_dataset_upload_bytes
        application.state.adapter_catalog = adapter_catalog
        application.state.local_session_token = app_settings.session_token_factory()
        application.state.app_instance_id = app_instance_id
        application.state.run_worker_processes = {}
        application.state.run_worker_processes_lock = threading.RLock()
        application.state.engine_process_handles = {}
        application.state.engine_profile_lifecycle_locks = {}
        worker_monitor = asyncio.create_task(monitor_run_workers(application))
        try:
            yield
        finally:
            worker_monitor.cancel()
            await asyncio.gather(worker_monitor, return_exceptions=True)
            pull_manager = getattr(application.state, "model_pull_manager", None)
            if pull_manager is not None:
                await pull_manager.close()  # Closing the streams stops Ollama's downloads.
            await shutdown_run_workers(application)
            process_handles = cast(
                dict[UUID, AdapterProcessHandle], application.state.engine_process_handles
            )
            shutdown_connection = open_database(database_path)
            try:
                profile_repository = RecordRepository(shutdown_connection, EngineProfile)
                for profile_id, handle in list(process_handles.items()):
                    profile = profile_repository.get(profile_id)
                    if profile is not None:
                        try:
                            await adapter_catalog.stop(profile.adapter_id, handle)
                        except Exception:
                            logger.exception(
                                "Could not stop managed engine process during shutdown: %s",
                                profile_id,
                            )
                        else:
                            if profile.status is not EngineProfileStatus.DISABLED:
                                profile_repository.update(
                                    EngineProfile.model_validate(
                                        profile.model_dump(mode="python")
                                        | {"status": EngineProfileStatus.DISABLED}
                                    )
                                )
                    process_handles.pop(profile_id, None)
            finally:
                shutdown_connection.close()
            await adapter_catalog.close()

    application = FastAPI(
        title="LOCALFORGE Inference Lab API",
        version=__version__,
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def protect_local_mutations(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        mutating = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if not is_loopback_request_host(request.url.hostname):
            # Refuse every request, not only mutations. A page on another site can make a
            # browser send requests to this port under its own hostname (DNS rebinding); without
            # this it could read the session token, run history and settings.
            return JSONResponse(
                status_code=403,
                content={
                    "detail": {
                        "code": "loopback_only",
                        "message": (
                            "Mutations are available only through a loopback host."
                            if mutating
                            else "LocalForge answers only requests addressed to a loopback "
                            "host (127.0.0.1 or localhost)."
                        ),
                    }
                },
            )
        if mutating:
            origin = request.headers.get("origin")
            if origin is not None and not is_loopback_origin(origin):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "origin_rejected",
                            "message": "This browser origin cannot mutate local profiles.",
                        }
                    },
                )
            supplied_token = request.headers.get(SESSION_HEADER_NAME.lower(), "")
            expected_token = cast(str, request.app.state.local_session_token)
            if not local_session_token_matches(supplied_token, expected_token):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "local_session_required",
                            "message": "Fetch the local session token before changing profiles.",
                        }
                    },
                )
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def safe_validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request
        safe_fields = [
            {
                "field": ".".join(str(part) for part in issue.get("loc", ())),
                "error": str(issue.get("type", "invalid")),
            }
            for issue in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "invalid_request",
                    "message": "Request validation failed; submitted values were omitted.",
                    "fields": safe_fields,
                }
            },
        )

    async def storage_error_handler(request: Request, error: Exception) -> JSONResponse:
        if not is_storage_full_error(error):
            logger.exception(
                "Unhandled storage error for %s %s",
                request.method,
                request.url.path,
                exc_info=error,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": {
                        "code": "internal_error",
                        "message": "The local API could not complete the request.",
                    }
                },
            )
        logger.error("Local storage is full while handling %s", request.url.path)
        return JSONResponse(
            status_code=507,
            content={
                "detail": {
                    "code": "storage_full",
                    "message": (
                        "The disk holding LocalForge data is full. Free disk space or set "
                        "LOCALFORGE_DATA_DIR to a drive with free space, then restart the API."
                    ),
                }
            },
        )

    application.add_exception_handler(sqlite3.DatabaseError, storage_error_handler)
    application.add_exception_handler(OSError, storage_error_handler)

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    @application.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health(request: Request) -> HealthResponse:
        return HealthResponse(
            status="ok",
            api_version="v1",
            app_version=__version__,
            database=DatabaseHealth(
                status="ready",
                schema_version=request.app.state.database_schema_version,
            ),
        )

    @application.get("/api/v1/session", response_model=LocalSessionResponse, tags=["system"])
    def local_session(request: Request, response: Response) -> LocalSessionResponse:
        """Return the in-memory mutation token to the same-origin local UI."""

        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return LocalSessionResponse(
            token=cast(str, request.app.state.local_session_token),
            header_name=SESSION_HEADER_NAME,
        )

    @application.get(
        "/api/v1/engines/catalog",
        response_model=list[AdapterManifest],
        tags=["engines"],
    )
    def engine_catalog(request: Request) -> list[AdapterManifest]:
        """List registered adapters; test fixtures are hidden unless enabled in Settings."""

        catalog = cast(AdapterCatalog, request.app.state.adapter_catalog)
        return visible_catalog_manifests(catalog, cast(Path, request.app.state.database_path))

    # Before the profile router so "/engines/recommendations" is not read as a profile id.
    application.include_router(recommendation_router)
    application.include_router(model_suggestion_router)
    application.include_router(hardware_fit_router)
    application.include_router(tune_router)
    application.include_router(profile_router)
    application.include_router(playground_router)
    application.include_router(workload_router)
    application.include_router(targets_router)
    application.include_router(run_router)
    application.include_router(experiment_router)
    application.include_router(repeat_router)
    application.include_router(settings_router)
    application.add_middleware(
        DatasetUploadBodyLimitMiddleware,
        max_upload_bytes=app_settings.max_dataset_upload_bytes,
    )
    if app_settings.web_directory is not None:
        mount_browser_ui(application, app_settings.web_directory)

    return application


app = create_app()
