"""FastAPI application factory and initial health route."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from secrets import compare_digest
from typing import Literal, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.responses import JSONResponse

from localforge import __version__
from localforge.adapters.catalog import AdapterCatalog, default_adapter_catalog
from localforge.adapters.contracts import AdapterManifest
from localforge.api.profiles import router as profile_router
from localforge.api.schemas import LocalSessionResponse
from localforge.security.local_session import (
    SESSION_HEADER_NAME,
    create_local_session_token,
    is_loopback_origin,
    is_loopback_request_host,
)
from localforge.storage.database import initialize_database
from localforge.storage.paths import default_data_directory


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings that can be overridden in tests and local development."""

    data_directory: Path
    adapter_catalog_factory: Callable[[], AdapterCatalog] = default_adapter_catalog
    session_token_factory: Callable[[], str] = create_local_session_token

    @classmethod
    def from_environment(cls) -> "AppSettings":
        return cls(data_directory=default_data_directory())


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
        adapter_catalog = app_settings.adapter_catalog_factory()
        application.state.database_schema_version = schema_version
        application.state.database_path = database_path
        application.state.adapter_catalog = adapter_catalog
        application.state.local_session_token = app_settings.session_token_factory()
        application.state.engine_process_handles = {}
        application.state.engine_profile_lifecycle_locks = {}
        try:
            yield
        finally:
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
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if not is_loopback_request_host(request.url.hostname):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": "loopback_only",
                            "message": "Mutations are available only through a loopback host.",
                        }
                    },
                )
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
            if not compare_digest(supplied_token, expected_token):
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
        """List the adapters registered through the shared catalog path."""

        catalog = cast(AdapterCatalog, request.app.state.adapter_catalog)
        return catalog.list_manifests()

    application.include_router(profile_router)

    return application


app = create_app()
