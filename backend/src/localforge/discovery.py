"""Scan loopback ports for already running inference engines without generating anything.

Discovery only issues bounded, credential-free GET requests to 127.0.0.1. It never accepts
another host, never POSTs, and never reads routes that can expose launch arguments (for
example SGLang's /server_info).
"""

import asyncio
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from pydantic import BaseModel

from localforge.adapters.koboldcpp import KOBOLDCPP_ADAPTER_ID, KoboldCppEngineAdapter
from localforge.adapters.llama_cpp import LLAMA_CPP_ADAPTER_ID
from localforge.adapters.lmdeploy import LMDEPLOY_ADAPTER_ID
from localforge.adapters.localai import LOCALAI_ADAPTER_ID, LocalAIEngineAdapter
from localforge.adapters.ollama import OLLAMA_ADAPTER_ID
from localforge.adapters.openai_compatible import OPENAI_COMPATIBLE_ADAPTER_ID
from localforge.adapters.serving_engine import _version_from_payload
from localforge.adapters.sglang import SGLANG_ADAPTER_ID, SglangEngineAdapter
from localforge.adapters.tgi import TGI_ADAPTER_ID, TgiEngineAdapter
from localforge.adapters.vllm import VLLM_ADAPTER_ID, VllmEngineAdapter
from localforge.core.urls import is_loopback_host

DISCOVERY_HOST = "127.0.0.1"
DEFAULT_DISCOVERY_PORTS: tuple[int, ...] = (
    11434,  # Ollama
    8080,  # llama.cpp, TGI, LocalAI, MLX-LM
    8081,
    5001,  # KoboldCpp
    8000,  # vLLM, TensorRT-LLM, LightLLM, Dynamo, MLC
    30000,  # SGLang
    23333,  # LMDeploy
    1234,  # mistral.rs, LM Studio
    9931,  # LocalForge-managed llama-server
)
MAX_DISCOVERY_PORTS = 32
MAX_RESPONSE_BYTES = 262_144
MAX_MODELS = 200
DEFAULT_TIMEOUT = httpx2.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0)
DEFAULT_PORT_BUDGET_SECONDS = 5.0

# Routes LocalForge itself serves; used to skip a LocalForge API found on a scanned port.
_LOCALFORGE_HEALTH_ROUTE = "/api/v1/health"
_MODELS_ROUTE = "/v1/models"
_OLLAMA_VERSION_ROUTE = "/api/version"
_LLAMA_CPP_PROPS_ROUTE = "/props"
_LOCALAI_READY_ROUTE = "/readyz"
_SGLANG_LEGACY_MODEL_INFO_ROUTE = "/get_model_info"

ScanStatus = Literal["found", "no_response", "unrecognized", "skipped_self"]


class DiscoveryPortResult(BaseModel):
    port: int
    status: ScanStatus


class DiscoveryCandidate(BaseModel):
    adapter_id: str
    display_name: str
    endpoint_url: str
    port: int
    engine_version: str | None
    models: list[str]
    evidence: str
    existing_profile_ids: list[UUID]


class DiscoveryResponse(BaseModel):
    scanned: list[DiscoveryPortResult]
    candidates: list[DiscoveryCandidate]
    duration_ms: int


@dataclass(frozen=True)
class SavedEndpoint:
    """The minimal saved-profile shape needed to mark already configured candidates."""

    profile_id: UUID
    adapter_id: str
    endpoint_url: str | None


@dataclass(frozen=True)
class _Identification:
    adapter_id: str
    endpoint_url: str
    engine_version: str | None
    models: list[str]
    evidence: str


class _NoResponse(Exception):
    """The port did not accept a connection or did not answer in time."""


def parse_ports(raw: str | None) -> list[int]:
    """Parse a comma-separated port override; raise ValueError with a user-facing message."""

    if raw is None:
        return list(DEFAULT_DISCOVERY_PORTS)
    ports: list[int] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        if not text.isdecimal() or not 1 <= int(text) <= 65535:
            raise ValueError("Ports must be whole numbers from 1 to 65535.")
        port = int(text)
        if port not in ports:
            ports.append(port)
    if not ports:
        raise ValueError("Provide at least one port to scan.")
    if len(ports) > MAX_DISCOVERY_PORTS:
        raise ValueError(f"Scan at most {MAX_DISCOVERY_PORTS} ports at a time.")
    return ports


def _normalized_endpoint(endpoint_url: str, adapter_id: str) -> tuple[str, str, int, str] | None:
    try:
        parsed = urlsplit(endpoint_url.strip())
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if not scheme or not host:
        return None
    if is_loopback_host(host):
        host = DISCOVERY_HOST
    if port is None:
        port = 443 if scheme == "https" else 80
    path = parsed.path.rstrip("/")
    # Every engine-specific adapter accepts its server root with or without the /v1 suffix;
    # the generic connector uses the configured base as given.
    if adapter_id != OPENAI_COMPATIBLE_ADAPTER_ID and path.endswith("/v1"):
        path = path[:-3]
    return scheme, host, port, path


def matching_profile_ids(
    adapter_id: str, endpoint_url: str, saved: Iterable[SavedEndpoint]
) -> list[UUID]:
    target = _normalized_endpoint(endpoint_url, adapter_id)
    if target is None:
        return []
    return [
        item.profile_id
        for item in saved
        if item.adapter_id == adapter_id
        and item.endpoint_url is not None
        and _normalized_endpoint(item.endpoint_url, adapter_id) == target
    ]


class _PortProbe:
    """GET-only, size-bounded HTTP access to one loopback port."""

    def __init__(self, client: httpx2.AsyncClient, port: int) -> None:
        self._client = client
        self._root = f"http://{DISCOVERY_HOST}:{port}"
        self.responded = False

    @property
    def root(self) -> str:
        return self._root

    async def get_json(self, route: str) -> tuple[int | None, object]:
        """Return (status, parsed JSON or None). Raise _NoResponse if nothing answers."""

        status, body = await self.get(route)
        if status != 200 or body is None:
            return status, None
        try:
            return status, json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return status, None

    async def get(self, route: str) -> tuple[int | None, bytes | None]:
        try:
            async with self._client.stream(
                "GET", f"{self._root}{route}", headers={"Accept": "application/json"}
            ) as response:
                self.responded = True
                status = response.status_code
                if status != 200:
                    return status, None
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return status, None
                return status, bytes(body)
        except (httpx2.ConnectError, httpx2.TimeoutException) as error:
            if not self.responded:
                raise _NoResponse from error
            return None, None
        except httpx2.HTTPError:
            # Something accepted the connection but did not speak usable HTTP.
            self.responded = True
            return None, None


def _model_list(payload: object) -> tuple[list[str], set[str]] | None:
    """Return model ids and owned_by values from an OpenAI list payload, or None."""

    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    ids: list[str] = []
    owners: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip() and len(ids) < MAX_MODELS:
            ids.append(model_id.strip()[:240])
        owner = item.get("owned_by")
        if isinstance(owner, str):
            owners.add(owner.strip().casefold())
    return ids, owners


def _is_localforge_health(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("api_version") == "v1"
        and isinstance(payload.get("app_version"), str)
        and isinstance(payload.get("database"), dict)
    )


def _text(value: object, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


async def _identify(
    probe: _PortProbe, implemented: Mapping[str, str]
) -> _Identification | Literal["skipped_self"] | None:
    root = probe.root
    _status, health = await probe.get_json(_LOCALFORGE_HEALTH_ROUTE)
    if _is_localforge_health(health):
        return "skipped_self"

    _status, models_payload = await probe.get_json(_MODELS_ROUTE)
    listed = _model_list(models_payload)
    models, owners = listed if listed is not None else ([], set())

    def found(
        adapter_id: str, evidence: str, version: str | None = None, endpoint: str = root
    ) -> _Identification:
        return _Identification(adapter_id, endpoint, version, list(models), evidence)

    if KOBOLDCPP_ADAPTER_ID in implemented:
        route = KoboldCppEngineAdapter.default_version_route or "/api/extra/version"
        _status, payload = await probe.get_json(route)
        if isinstance(payload, dict) and payload.get("result") == "KoboldCpp":
            return found(
                KOBOLDCPP_ADAPTER_ID,
                f'GET {route} returned result "KoboldCpp"',
                _version_from_payload(payload),
            )

    if OLLAMA_ADAPTER_ID in implemented:
        _status, payload = await probe.get_json(_OLLAMA_VERSION_ROUTE)
        version = _version_from_payload(payload) if isinstance(payload, dict) else None
        if version is not None:
            if not models:
                _status, tags = await probe.get_json("/api/tags")
                entries = tags.get("models") if isinstance(tags, dict) else None
                if isinstance(entries, list):
                    models = [
                        name.strip()[:240]
                        for entry in entries[:MAX_MODELS]
                        if isinstance(entry, dict)
                        and isinstance(name := entry.get("name"), str)
                        and name.strip()
                    ]
            return found(
                OLLAMA_ADAPTER_ID,
                f"GET {_OLLAMA_VERSION_ROUTE} returned an Ollama version",
                version,
            )

    if LLAMA_CPP_ADAPTER_ID in implemented:
        _status, props = await probe.get_json(_LLAMA_CPP_PROPS_ROUTE)
        build_info = _text(props.get("build_info")) if isinstance(props, dict) else None
        if build_info is not None:
            return found(
                LLAMA_CPP_ADAPTER_ID,
                f"GET {_LLAMA_CPP_PROPS_ROUTE} returned llama.cpp build_info",
                build_info,
            )
        if "llamacpp" in owners:
            return found(
                LLAMA_CPP_ADAPTER_ID, f"GET {_MODELS_ROUTE} listed models owned by llamacpp"
            )

    if VLLM_ADAPTER_ID in implemented and "vllm" in owners:
        route = VllmEngineAdapter.default_version_route or "/version"
        _status, payload = await probe.get_json(route)
        return found(
            VLLM_ADAPTER_ID,
            f"GET {_MODELS_ROUTE} listed models owned by vllm",
            _version_from_payload(payload),
        )

    if LMDEPLOY_ADAPTER_ID in implemented and "lmdeploy" in owners:
        return found(LMDEPLOY_ADAPTER_ID, f"GET {_MODELS_ROUTE} listed models owned by lmdeploy")

    if SGLANG_ADAPTER_ID in implemented:
        if "sglang" in owners:
            return found(SGLANG_ADAPTER_ID, f"GET {_MODELS_ROUTE} listed models owned by sglang")
        for route in (
            SglangEngineAdapter.default_model_info_route or "/model_info",
            _SGLANG_LEGACY_MODEL_INFO_ROUTE,
        ):
            _status, payload = await probe.get_json(route)
            if isinstance(payload, dict) and (
                isinstance(payload.get("is_generation"), bool)
                or isinstance(payload.get("served_model_name"), str)
            ):
                return found(SGLANG_ADAPTER_ID, f"GET {route} returned SGLang model info")

    if TGI_ADAPTER_ID in implemented:
        route = TgiEngineAdapter.default_version_route or "/info"
        _status, payload = await probe.get_json(route)
        if isinstance(payload, dict) and _text(payload.get("model_id"), 240) is not None:
            if not models:
                models = [_text(payload.get("model_id"), 240) or ""]
            return found(
                TGI_ADAPTER_ID,
                f"GET {route} returned a TGI model_id",
                _version_from_payload(payload),
            )

    if LOCALAI_ADAPTER_ID in implemented:
        ready_status, _body = await probe.get(_LOCALAI_READY_ROUTE)
        if ready_status == 200:
            route = LocalAIEngineAdapter.default_version_route or "/version"
            _status, payload = await probe.get_json(route)
            if isinstance(payload, dict) and isinstance(payload.get("version"), str):
                return found(
                    LOCALAI_ADAPTER_ID,
                    f"GET {_LOCALAI_READY_ROUTE} and GET {route} answered like LocalAI",
                    _version_from_payload(payload),
                )

    if OPENAI_COMPATIBLE_ADAPTER_ID in implemented and listed is not None:
        return found(
            OPENAI_COMPATIBLE_ADAPTER_ID,
            f"GET {_MODELS_ROUTE} returned an OpenAI-compatible model list",
            endpoint=f"{root}/v1",
        )
    return None


async def _scan_port(
    client: httpx2.AsyncClient,
    port: int,
    implemented: Mapping[str, str],
    budget_seconds: float,
) -> tuple[DiscoveryPortResult, _Identification | None]:
    probe = _PortProbe(client, port)
    try:
        outcome = await asyncio.wait_for(_identify(probe, implemented), timeout=budget_seconds)
    except (_NoResponse, TimeoutError):
        status: ScanStatus = "unrecognized" if probe.responded else "no_response"
        return DiscoveryPortResult(port=port, status=status), None
    if outcome == "skipped_self":
        return DiscoveryPortResult(port=port, status="skipped_self"), None
    if outcome is None:
        return DiscoveryPortResult(port=port, status="unrecognized"), None
    return DiscoveryPortResult(port=port, status="found"), outcome


async def discover_engines(
    *,
    implemented_adapters: Mapping[str, str],
    saved_endpoints: Sequence[SavedEndpoint] = (),
    ports: Sequence[int] = DEFAULT_DISCOVERY_PORTS,
    self_ports: Iterable[int] = (),
    transport: httpx2.AsyncBaseTransport | None = None,
    timeout: httpx2.Timeout = DEFAULT_TIMEOUT,
    port_budget_seconds: float = DEFAULT_PORT_BUDGET_SECONDS,
) -> DiscoveryResponse:
    """Probe each loopback port concurrently and describe recognizable engines.

    ``implemented_adapters`` maps implemented adapter ids to their display names; adapters
    missing from it are never suggested.
    """

    started = time.perf_counter()
    skip = set(self_ports)
    client = httpx2.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    try:

        async def scan(port: int) -> tuple[DiscoveryPortResult, _Identification | None]:
            if port in skip:
                return DiscoveryPortResult(port=port, status="skipped_self"), None
            return await _scan_port(client, port, implemented_adapters, port_budget_seconds)

        results = await asyncio.gather(*(scan(port) for port in ports))
    finally:
        await client.aclose()

    candidates = [
        DiscoveryCandidate(
            adapter_id=found.adapter_id,
            display_name=implemented_adapters[found.adapter_id],
            endpoint_url=found.endpoint_url,
            port=result.port,
            engine_version=found.engine_version,
            models=found.models,
            evidence=found.evidence,
            existing_profile_ids=matching_profile_ids(
                found.adapter_id, found.endpoint_url, saved_endpoints
            ),
        )
        for result, found in results
        if found is not None
    ]
    return DiscoveryResponse(
        scanned=[result for result, _found in results],
        candidates=candidates,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
