"""Engine recommendations for the machine running LocalForge."""

from fastapi import APIRouter, HTTPException, Request

from localforge.api.dependencies import DatabaseConnection, LocalAdapterCatalog
from localforge.recommendations import (
    RecommendationResponse,
    build_recommendations,
    load_stored_context,
)
from localforge.security.local_session import is_loopback_request_host

router = APIRouter(prefix="/api/v1")


@router.get(
    "/engines/recommendations",
    response_model=RecommendationResponse,
    tags=["engines"],
)
async def engine_recommendations(
    request: Request,
    catalog: LocalAdapterCatalog,
    connection: DatabaseConnection,
) -> RecommendationResponse:
    """Rank engines by fit for this machine; read-only, scans 127.0.0.1 ports only."""

    if not is_loopback_request_host(request.url.hostname):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "loopback_only",
                "message": "Engine recommendations are available only through a loopback host.",
            },
        )
    self_ports: set[int] = set()
    server = request.scope.get("server")
    if isinstance(server, (tuple, list)) and len(server) == 2 and isinstance(server[1], int):
        self_ports.add(server[1])
    if request.url.port is not None:
        self_ports.add(request.url.port)
    context = load_stored_context(connection)
    return await build_recommendations(
        catalog.list_manifests(),
        saved_machine=context.saved_machine,
        saved_profiles=context.saved_profiles,
        measured=context.measured,
        read_gib_per_second=context.read_gib_per_second,
        self_ports=self_ports,
        discovery_transport=getattr(request.app.state, "engine_discovery_transport", None),
    )
