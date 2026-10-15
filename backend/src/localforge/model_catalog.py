"""Curated model catalog and model suggestions for this machine.

The catalog (``localforge/data/model_catalog.json``) lists well-known open-weight models with
their typical 4-bit download size and where to get them for each runtime. It never stores
quality scores. ``suggest_models`` ranks catalog entries for one use case on one machine.

Memory: a model needs about its Q4 file size plus 10% and 0.5 GB for the KV cache and runtime at a
4k context. It "fits" within 70% of the memory budget, is "tight" up to 100%, else "too_large".
The budget is the one ``model_size_guidance`` uses (85% of discrete GPU memory, or RAM minus 6 GB).

Speed: generating one token reads roughly every active weight once, so tokens/s is about
(bytes the machine can stream per second) / (model bytes read per token). When this machine has
completed runs, the streaming rate is measured from them ("estimated from your runs"). Otherwise
a known GPU's published memory bandwidth gives a rough estimate; CPU-only machines without runs
get no speed estimate.

Score (0-100) = memory fit (fits 30, tight 10, too large 0)
              + speed (0-30: 30 x estimated tok/s / use-case target, capped; see below when unknown)
              + size (0-25: grows with log of parameter count; larger models usually handle harder
                tasks, at the cost of speed and memory)
              + use-case match (0-10) + already downloaded (5).
Models that do not fit are always ranked after models that do.
"""

import asyncio
import json
import math
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx2
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from localforge.core.models import (
    AcceleratorInfo,
    EngineIntegrationMode,
    EngineProfile,
    EngineProfileStatus,
    HardwareSnapshot,
    ModelArtifact,
    ModelArtifactStatus,
    utc_now,
)
from localforge.core.urls import endpoint_is_remote
from localforge.recommendations import (
    RecommendationResponse,
    machine_summary,
    model_size_guidance,
    read_stored_context,
)

GIB = 1024**3
OLLAMA_ADAPTER_ID = "localforge.ollama"
GGUF_ADAPTER_IDS = frozenset({"localforge.llama_cpp", "localforge.koboldcpp"})
DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"

# Memory needed beyond the file: KV cache and runtime buffers at a ~4k context.
KV_OVERHEAD_FRACTION = 0.10
KV_OVERHEAD_GIB = 0.5
COMFORTABLE_BUDGET_FRACTION = 0.7
# Share of a GPU's published peak bandwidth that token generation typically achieves.
GPU_BANDWIDTH_EFFICIENCY = 0.6
SIZE_POINTS_REFERENCE_PARAMS_B = 72.0
# CPU-only machines without measured runs: models up to this size are a safe first try.
UNMEASURED_CPU_PARAMS_B = 4.0

UseCase = Literal["chat", "coding", "document_qa", "small_fast", "reasoning"]
Strength = Literal["chat", "coding", "reasoning", "multilingual", "small_fast"]
ModelFit = Literal["fits", "tight", "too_large"]
SpeedBasis = Literal["measured", "gpu_bandwidth_rough", "unknown"]

USE_CASES: tuple[UseCase, ...] = ("chat", "coding", "document_qa", "small_fast", "reasoning")
USE_CASE_LABELS: dict[UseCase, str] = {
    "chat": "Chat",
    "coding": "Coding",
    "document_qa": "Document Q&A",
    "small_fast": "Small and fast",
    "reasoning": "Reasoning",
}
# Generation speed each use case needs to feel responsive.
TARGET_TOKENS_PER_SECOND: dict[UseCase, float] = {
    "chat": 10.0,
    "coding": 10.0,
    "document_qa": 8.0,
    "small_fast": 20.0,
    "reasoning": 15.0,
}
FIT_POINTS: dict[ModelFit, int] = {"fits": 30, "tight": 10, "too_large": 0}
SPEED_POINTS = 30
SIZE_POINTS = 25
MATCH_POINTS = 10
DOWNLOADED_POINTS = 5

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
# Ollama model names: optional registry/namespace path, then an optional ":tag".
OLLAMA_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}(/[A-Za-z0-9][A-Za-z0-9._-]{0,79}){0,2}"
    r"(:[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?$"
)
_HF_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

# Registries LocalForge will tell Ollama to download from. Any other host would make a click in
# LocalForge send Ollama to an arbitrary server, so those names are refused.
ALLOWED_PULL_REGISTRIES = frozenset({"registry.ollama.ai", "hf.co"})


def ollama_registry_host(name: str) -> str | None:
    """The registry host an Ollama model name points at; None means Ollama's default registry.

    Follows Ollama's own parsing: ``host/namespace/model`` always names a host, and
    ``host/model`` does when the first part looks like one (contains a dot or a port).
    """

    parts = name.split(":", 1)[0].split("/")
    if len(parts) >= 3:
        return parts[0].casefold()
    if len(parts) == 2 and ("." in parts[0] or parts[0].casefold() == "localhost"):
        return parts[0].casefold()
    return None


# --- Catalog schema ----------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GgufSource(_Strict):
    repo: str
    file: str | None = None

    @field_validator("repo")
    @classmethod
    def repo_is_a_hugging_face_id(cls, value: str) -> str:
        if not _HF_REPO.match(value):
            raise ValueError("expected a Hugging Face repo id like owner/name")
        return value

    @field_validator("file")
    @classmethod
    def file_is_a_gguf(cls, value: str | None) -> str | None:
        if value is not None and (not value.lower().endswith(".gguf") or "/" in value):
            raise ValueError("expected a .gguf file name")
        return value


class CatalogSources(_Strict):
    """Where to get the model per runtime; null means no identifier LocalForge can vouch for."""

    ollama: str | None = None
    ollama_aliases: tuple[str, ...] = ()
    gguf: GgufSource | None = None
    openvino: str | None = None
    onnx: str | None = None

    @field_validator("ollama")
    @classmethod
    def ollama_tag_is_explicit(cls, value: str | None) -> str | None:
        if value is not None and (not OLLAMA_NAME_PATTERN.match(value) or ":" not in value):
            raise ValueError("expected an explicit Ollama tag like qwen2.5:3b")
        return value

    @field_validator("ollama_aliases")
    @classmethod
    def aliases_are_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for alias in value:
            if not OLLAMA_NAME_PATTERN.match(alias) or ":" not in alias:
                raise ValueError("expected explicit Ollama tags")
        return value

    @field_validator("openvino", "onnx")
    @classmethod
    def repo_ids(cls, value: str | None) -> str | None:
        if value is not None and not _HF_REPO.match(value):
            raise ValueError("expected a Hugging Face repo id like owner/name")
        return value


class CatalogModel(_Strict):
    id: str
    name: str = Field(min_length=1, max_length=120)
    family: str = Field(min_length=1, max_length=60)
    publisher: str = Field(min_length=1, max_length=60)
    params_b: float = Field(gt=0, le=1000)
    # Mixture-of-experts models read only the active parameters per token.
    active_params_b: float | None = Field(default=None, gt=0)
    q4_size_gb: float = Field(gt=0, le=1000)
    # When Ollama's tag is not a 4-bit file, its actual download size.
    ollama_size_gb: float | None = Field(default=None, gt=0, le=1000)
    context_tokens: int = Field(ge=512)
    license: str = Field(min_length=1, max_length=80)
    strengths: tuple[Strength, ...] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=400)
    sources: CatalogSources

    @field_validator("id")
    @classmethod
    def id_is_a_slug(cls, value: str) -> str:
        if not _SLUG.match(value):
            raise ValueError("id must be a lowercase slug")
        return value

    @model_validator(mode="after")
    def active_params_within_total(self) -> "CatalogModel":
        if self.active_params_b is not None and self.active_params_b > self.params_b:
            raise ValueError("active_params_b cannot exceed params_b")
        if len(set(self.strengths)) != len(self.strengths):
            raise ValueError("strengths must be unique")
        return self

    @property
    def size_gib(self) -> float:
        return self.q4_size_gb * 1e9 / GIB

    @property
    def read_share(self) -> float:
        """Share of the weights read per token (below 1 for mixture-of-experts models)."""

        return (self.active_params_b / self.params_b) if self.active_params_b else 1.0

    @property
    def gib_read_per_token(self) -> float:
        return self.size_gib * self.read_share

    @property
    def download_size_gb(self) -> float:
        return self.ollama_size_gb or self.q4_size_gb

    @property
    def ollama_tags(self) -> tuple[str, ...]:
        if self.sources.ollama is None:
            return ()
        return (self.sources.ollama, *self.sources.ollama_aliases)


class ModelCatalog(_Strict):
    schema_version: Literal[1]
    catalog_version: str = Field(pattern=r"^\d{4}\.\d{1,2}\.\d+$")
    catalog_date: date
    about: str
    models: tuple[CatalogModel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "ModelCatalog":
        ids = [model.id for model in self.models]
        if len(set(ids)) != len(ids):
            raise ValueError("model ids must be unique")
        tags = [tag for model in self.models for tag in model.ollama_tags]
        if len(set(tags)) != len(tags):
            raise ValueError("Ollama tags and aliases must be unique across models")
        return self

    def by_ollama_tag(self, tag: str) -> CatalogModel | None:
        normalized = normalize_ollama_tag(tag)
        return next(
            (model for model in self.models if normalized in model.ollama_tags),
            None,
        )


@lru_cache(maxsize=1)
def load_model_catalog() -> ModelCatalog:
    """Load and validate the packaged catalog (cached; the file ships with LocalForge)."""

    text = resources.files("localforge").joinpath("data", "model_catalog.json").read_text("utf-8")
    return ModelCatalog.model_validate(json.loads(text))


def normalize_ollama_tag(tag: str) -> str:
    """Ollama treats a bare name as ``name:latest``; names are case-insensitive."""

    value = tag.strip()
    if ":" not in value.rsplit("/", 1)[-1]:
        value = f"{value}:latest"
    return value.casefold()


def gguf_page_url(source: GgufSource) -> str:
    return f"https://huggingface.co/{source.repo}"


# --- Machine facts -----------------------------------------------------------------------


# Published peak memory bandwidth (GB/s). Matched in order against the accelerator name; laptop
# variants differ from desktop cards, so they are not matched and get no estimate.
GPU_BANDWIDTH_GB_S: tuple[tuple[str, float], ...] = (
    (r"h100.*(sxm|hbm3)", 3350),
    (r"h100", 2000),
    (r"a100.*80\s*gb", 2039),
    (r"a100", 1555),
    (r"l40s?", 864),
    (r"\bl4\b", 300),
    (r"a10g", 600),
    (r"\ba10\b", 600),
    (r"\bt4\b", 320),
    (r"v100", 900),
    (r"rtx\s*5090", 1792),
    (r"rtx\s*5080", 960),
    (r"rtx\s*5070\s*ti", 896),
    (r"rtx\s*5070", 672),
    (r"rtx\s*4090", 1008),
    (r"rtx\s*4080", 717),
    (r"rtx\s*4070\s*ti\s*super", 672),
    (r"rtx\s*4070", 504),
    (r"rtx\s*4060\s*ti", 288),
    (r"rtx\s*4060", 272),
    (r"rtx\s*3090", 936),
    (r"rtx\s*3080\s*ti", 912),
    (r"rtx\s*3080", 760),
    (r"rtx\s*3070", 448),
    (r"rtx\s*3060\s*ti", 448),
    (r"rtx\s*3060", 360),
    (r"rx\s*7900\s*xtx", 960),
    (r"rx\s*7900\s*xt", 800),
    (r"m1\s*ultra", 800),
    (r"m1\s*max", 400),
    (r"m1\s*pro", 200),
    (r"\bm1\b", 68),
    (r"m2\s*ultra", 800),
    (r"m2\s*max", 400),
    (r"m2\s*pro", 200),
    (r"\bm2\b", 100),
    (r"m3\s*max", 400),
    (r"m3\s*pro", 150),
    (r"\bm3\b", 100),
    (r"m4\s*max", 546),
    (r"m4\s*pro", 273),
    (r"\bm4\b", 120),
)


def gpu_bandwidth_gb_s(accelerator: AcceleratorInfo) -> float | None:
    name = accelerator.name.casefold()
    if "laptop" in name or "mobile" in name or "max-q" in name:
        return None
    for pattern, bandwidth in GPU_BANDWIDTH_GB_S:
        if re.search(pattern, name):
            return bandwidth
    return None


@dataclass(frozen=True)
class SuggestionMachine:
    """What the suggestion engine needs to know about one machine."""

    memory_budget_bytes: int | None
    memory_basis: Literal["gpu_memory", "system_memory"]
    summary: str = ""
    read_gib_per_second: float | None = None
    gpu_name: str | None = None
    gpu_bandwidth_gb_s: float | None = None


def suggestion_machine(
    hardware: HardwareSnapshot | None,
    read_gib_per_second: float | None = None,
) -> SuggestionMachine:
    hardware = hardware or HardwareSnapshot()
    guidance = model_size_guidance(hardware, read_gib_per_second)
    gpu_name: str | None = None
    bandwidth: float | None = None
    for accelerator in hardware.accelerators or []:
        vendor = (accelerator.vendor or "").casefold()
        discrete = accelerator.integrated is not True and accelerator.memory_kind == "dedicated"
        apple = vendor == "apple" or accelerator.name.startswith("Apple ")
        if not (discrete or apple):
            continue
        estimate = gpu_bandwidth_gb_s(accelerator)
        if estimate is not None and (bandwidth is None or estimate > bandwidth):
            gpu_name, bandwidth = accelerator.name, estimate
    return SuggestionMachine(
        memory_budget_bytes=guidance.memory_budget_bytes,
        memory_basis=guidance.basis,
        summary=machine_summary(hardware),
        read_gib_per_second=read_gib_per_second
        if read_gib_per_second is not None and read_gib_per_second > 0
        else None,
        gpu_name=gpu_name,
        gpu_bandwidth_gb_s=bandwidth,
    )


# --- What is already downloaded ----------------------------------------------------------


class DownloadedLocation(BaseModel):
    source: Literal["ollama", "gguf"]
    model_ref: str
    engine_profile_id: UUID | None
    engine_label: str | None
    adapter_id: str


@dataclass
class InstalledModels:
    """Ollama tags and GGUF file names already on this machine, with where they were seen."""

    ollama: dict[str, list[DownloadedLocation]] = field(default_factory=dict)
    gguf: dict[str, list[DownloadedLocation]] = field(default_factory=dict)

    def add_ollama(self, location: DownloadedLocation) -> None:
        key = normalize_ollama_tag(location.model_ref)
        entries = self.ollama.setdefault(key, [])
        if all(item.engine_profile_id != location.engine_profile_id for item in entries):
            entries.append(location)

    def add_gguf(self, location: DownloadedLocation) -> None:
        key = gguf_file_name(location.model_ref)
        if key is None:
            return
        entries = self.gguf.setdefault(key, [])
        if all(item.engine_profile_id != location.engine_profile_id for item in entries):
            entries.append(location)

    def locations_for(self, model: CatalogModel) -> list[DownloadedLocation]:
        found: list[DownloadedLocation] = []
        for tag in model.ollama_tags:
            found.extend(self.ollama.get(normalize_ollama_tag(tag), []))
        if model.sources.gguf is not None and model.sources.gguf.file is not None:
            found.extend(self.gguf.get(model.sources.gguf.file.casefold(), []))
        return found


def gguf_file_name(path: str) -> str | None:
    """Lower-cased base name of a .gguf path (Windows or POSIX), or None for other refs."""

    name = PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name
    return name.casefold() if name.casefold().endswith(".gguf") else None


def _is_local(profile: EngineProfile) -> bool:
    try:
        return not endpoint_is_remote(profile.endpoint_url)
    except ValueError:
        return False


def installed_from_inventory(
    profiles: Sequence[EngineProfile],
    artifacts: Iterable[ModelArtifact],
    live_ollama_tags: Mapping[str, Sequence[str]] | None = None,
) -> InstalledModels:
    """Collect downloaded models from saved inventories and live Ollama tag lists.

    ``live_ollama_tags`` maps an Ollama base URL to the names its /api/tags returned. A live list
    replaces the saved (possibly stale) inventory of profiles pointing at that Ollama.
    """

    installed = InstalledModels()
    live = live_ollama_tags or {}
    local = {profile.id: profile for profile in profiles if _is_local(profile)}
    for artifact in artifacts:
        profile = local.get(artifact.engine_profile_id) if artifact.engine_profile_id else None
        if profile is None or artifact.model_ref is None:
            continue
        if artifact.status is ModelArtifactStatus.MISSING:
            continue
        if (
            profile.adapter_id == OLLAMA_ADAPTER_ID
            and profile.endpoint_url is not None
            and ollama_base_url(profile.endpoint_url) in live
        ):
            continue
        location = DownloadedLocation(
            source="ollama" if profile.adapter_id == OLLAMA_ADAPTER_ID else "gguf",
            model_ref=artifact.model_ref,
            engine_profile_id=profile.id,
            engine_label=profile.label,
            adapter_id=profile.adapter_id,
        )
        if profile.adapter_id == OLLAMA_ADAPTER_ID:
            installed.add_ollama(location)
        else:
            installed.add_gguf(location)
    for profile in local.values():
        if profile.adapter_id == OLLAMA_ADAPTER_ID:
            continue
        for settings in profile.engine_extensions.values():
            path = settings.get("model_path") if isinstance(settings, dict) else None
            if isinstance(path, str):
                installed.add_gguf(
                    DownloadedLocation(
                        source="gguf",
                        model_ref=path,
                        engine_profile_id=profile.id,
                        engine_label=profile.label,
                        adapter_id=profile.adapter_id,
                    )
                )
    for base_url, tags in live.items():
        owners: list[EngineProfile | None] = [
            profile
            for profile in local.values()
            if profile.adapter_id == OLLAMA_ADAPTER_ID
            and profile.endpoint_url is not None
            and ollama_base_url(profile.endpoint_url) == base_url
        ]
        for tag in tags:
            for owner in owners or [None]:  # An Ollama at the default address with no profile.
                installed.add_ollama(
                    DownloadedLocation(
                        source="ollama",
                        model_ref=tag,
                        engine_profile_id=owner.id if owner else None,
                        engine_label=owner.label if owner else None,
                        adapter_id=OLLAMA_ADAPTER_ID,
                    )
                )
    return installed


def ollama_base_url(endpoint_url: str) -> str:
    parsed = urlsplit(endpoint_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def local_ollama_endpoints(profiles: Sequence[EngineProfile]) -> list[str]:
    """Base URLs of local Ollama profiles, or Ollama's default address when none is saved."""

    endpoints: list[str] = []
    for profile in profiles:
        if (
            profile.adapter_id == OLLAMA_ADAPTER_ID
            and profile.endpoint_url is not None
            and _is_local(profile)
        ):
            base = ollama_base_url(profile.endpoint_url)
            if base not in endpoints:
                endpoints.append(base)
    return endpoints or [DEFAULT_OLLAMA_ENDPOINT]


TAGS_TIMEOUT = httpx2.Timeout(connect=0.5, read=2.0, write=0.5, pool=0.5)
MAX_TAGS = 500


async def fetch_ollama_tags(
    base_urls: Sequence[str],
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> dict[str, list[str]]:
    """Read-only GET /api/tags from each local Ollama; unreachable endpoints are skipped."""

    async with httpx2.AsyncClient(transport=transport, timeout=TAGS_TIMEOUT) as client:

        async def one(base_url: str) -> tuple[str, list[str] | None]:
            try:
                response = await client.get(f"{base_url}/api/tags")
                if response.status_code != 200 or len(response.content) > 4_000_000:
                    return base_url, None
                payload = response.json()
            except (httpx2.HTTPError, ValueError):
                return base_url, None
            entries = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                return base_url, None
            names: list[str] = []
            for entry in entries[:MAX_TAGS]:
                name = entry.get("name") or entry.get("model") if isinstance(entry, dict) else None
                if isinstance(name, str) and name.strip() and len(name) <= 200:
                    names.append(name.strip())
            return base_url, names

        results = await asyncio.gather(*(one(url) for url in base_urls))
    return {url: names for url, names in results if names is not None}


# --- Suggestions ---------------------------------------------------------------------------


class SpeedEstimate(BaseModel):
    tokens_per_second: float | None
    basis: SpeedBasis
    label: str
    verdict: str | None


class ModelSourcesView(BaseModel):
    ollama_tag: str | None
    gguf_repo: str | None
    gguf_file: str | None
    gguf_url: str | None
    openvino_repo: str | None
    onnx_repo: str | None


class ModelSuggestion(BaseModel):
    rank: int
    model_id: str
    name: str
    family: str
    publisher: str
    params_b: float
    active_params_b: float | None
    q4_size_gb: float
    download_size_gb: float
    context_tokens: int
    license: str
    strengths: list[Strength]
    notes: str | None
    fit: ModelFit
    memory_required_gb: float
    speed: SpeedEstimate
    score: int
    reasons: list[str]
    cautions: list[str]
    downloaded: list[DownloadedLocation]
    runnable_with: list[str]
    sources: ModelSourcesView


def memory_required_gib(model: CatalogModel) -> float:
    return model.size_gib * (1 + KV_OVERHEAD_FRACTION) + KV_OVERHEAD_GIB


def memory_fit(model: CatalogModel, machine: SuggestionMachine) -> ModelFit | None:
    if machine.memory_budget_bytes is None:
        return None
    budget = machine.memory_budget_bytes / GIB
    required = memory_required_gib(model)
    if required <= budget * COMFORTABLE_BUDGET_FRACTION:
        return "fits"
    if required <= budget:
        return "tight"
    return "too_large"


def speed_verdict(tokens_per_second: float) -> str:
    if tokens_per_second >= 20:
        return "fast enough for interactive use"
    if tokens_per_second >= 10:
        return "comfortable for chat"
    if tokens_per_second >= 5:
        return "usable for chat, a little slow"
    if tokens_per_second >= 2:
        return "usable for batch work, slow for chat"
    return "very slow here; only for offline batch jobs"


def _format_rate(value: float) -> str:
    return f"{value:.0f}" if value >= 3 else f"{value:.1f}"


def estimate_speed(
    model: CatalogModel, machine: SuggestionMachine, fit: ModelFit | None
) -> SpeedEstimate:
    read = model.gib_read_per_token
    if machine.read_gib_per_second is not None:
        rate = round(machine.read_gib_per_second / read, 1)
        return SpeedEstimate(
            tokens_per_second=rate,
            basis="measured",
            label="estimated from your runs",
            verdict=speed_verdict(float(_format_rate(rate))),
        )
    if machine.gpu_bandwidth_gb_s is not None and machine.gpu_name and fit != "too_large":
        effective_gib = machine.gpu_bandwidth_gb_s * 1e9 / GIB * GPU_BANDWIDTH_EFFICIENCY
        rate = round(effective_gib / read, 1)
        return SpeedEstimate(
            tokens_per_second=rate,
            basis="gpu_bandwidth_rough",
            label=f"rough estimate from the {machine.gpu_name} memory bandwidth",
            verdict=speed_verdict(float(_format_rate(rate))),
        )
    return SpeedEstimate(
        tokens_per_second=None,
        basis="unknown",
        label="unknown until you benchmark a model on this machine",
        verdict=None,
    )


def eligible(model: CatalogModel, use_case: UseCase) -> bool:
    strengths = set(model.strengths)
    if use_case == "document_qa":
        return "chat" in strengths and model.context_tokens >= 32768
    return use_case in strengths


def _match_points(model: CatalogModel, use_case: UseCase) -> tuple[int, str | None]:
    if use_case == "document_qa":
        if model.context_tokens >= 131072:
            return MATCH_POINTS, "Long 128k-token context for large documents"
        return MATCH_POINTS // 2, f"{model.context_tokens // 1024}k-token context"
    if use_case == "small_fast":
        return (
            (MATCH_POINTS, "Very small: quick to download and load")
            if model.params_b <= 2
            else (
                MATCH_POINTS // 2,
                None,
            )
        )
    if model.strengths[0] == use_case:
        label = {
            "coding": "Trained specifically for code",
            "reasoning": "Built for step-by-step reasoning",
        }.get(use_case)
        return MATCH_POINTS, label
    return MATCH_POINTS // 2, None


def _speed_points(
    model: CatalogModel, use_case: UseCase, machine: SuggestionMachine, speed: SpeedEstimate
) -> float:
    if speed.tokens_per_second is not None:
        target = TARGET_TOKENS_PER_SECOND[use_case]
        return SPEED_POINTS * min(1.0, speed.tokens_per_second / target)
    if machine.memory_basis == "system_memory" and machine.gpu_bandwidth_gb_s is None:
        # CPU-only and unmeasured: speed, not memory, is the limit; favour small models.
        effective = model.active_params_b or model.params_b
        return SPEED_POINTS * min(1.0, UNMEASURED_CPU_PARAMS_B / effective)
    return SPEED_POINTS * 0.8


def _size_points(model: CatalogModel) -> float:
    return SIZE_POINTS * min(
        1.0, math.log2(1 + model.params_b) / math.log2(1 + SIZE_POINTS_REFERENCE_PARAMS_B)
    )


def suggest_models(
    catalog: ModelCatalog,
    use_case: UseCase,
    machine: SuggestionMachine,
    *,
    installed: InstalledModels | None = None,
    installed_engines: Iterable[str] = (),
) -> list[ModelSuggestion]:
    """Rank the catalog's models for one use case on one machine (models that fit first)."""

    installed = installed or InstalledModels()
    engines = set(installed_engines)
    target = TARGET_TOKENS_PER_SECOND[use_case]
    budget_gb = (
        machine.memory_budget_bytes / 1e9 if machine.memory_budget_bytes is not None else None
    )
    ranked: list[tuple[tuple[int, float, int], ModelSuggestion]] = []
    for order, model in enumerate(catalog.models):
        if not eligible(model, use_case):
            continue
        fit = memory_fit(model, machine)
        speed = estimate_speed(model, machine, fit)
        downloaded = installed.locations_for(model)
        reasons: list[str] = []
        cautions: list[str] = []
        required_gb = memory_required_gib(model) * GIB / 1e9

        match_points, match_reason = _match_points(model, use_case)
        if match_reason:
            reasons.append(match_reason)
        if fit == "fits":
            reasons.append(
                f"Fits comfortably: needs about {required_gb:.1f} GB of the "
                f"{budget_gb:.1f} GB available for models"
            )
        elif fit == "tight":
            cautions.append(
                f"Tight fit: needs about {required_gb:.1f} GB of the {budget_gb:.1f} GB "
                "available; close other apps and keep contexts short"
            )
        elif fit == "too_large":
            cautions.append(
                f"Does not fit: needs about {required_gb:.1f} GB but only {budget_gb:.1f} GB "
                "is available for models"
            )
            if machine.memory_basis == "gpu_memory":
                cautions.append(
                    "It would spill into system RAM (partial GPU offload), which is much slower"
                )
        else:
            cautions.append("Memory size was not detected, so fit is unknown")

        if speed.tokens_per_second is not None and speed.verdict is not None:
            text = (
                f"~{_format_rate(speed.tokens_per_second)} tok/s here ({speed.label}): "
                f"{speed.verdict}"
            )
            (reasons if speed.tokens_per_second >= target * 0.5 else cautions).append(text)
        elif machine.memory_basis == "system_memory" and machine.gpu_bandwidth_gb_s is None:
            cautions.append(
                "Speed unknown: without a discrete GPU, benchmark one small model first so "
                "LocalForge can estimate the rest"
            )
        if model.active_params_b:
            reasons.append(
                f"Mixture of experts: reads only ~{model.active_params_b:g}B parameters per "
                "token, so it is faster than its size suggests"
            )
        if use_case == "reasoning" and speed.tokens_per_second is not None:
            if speed.tokens_per_second < target:
                cautions.append(
                    "Reasoning models write hundreds of thinking tokens before answering; at "
                    "this speed a reply can take minutes"
                )
        if use_case == "document_qa" and speed.basis != "unknown":
            cautions.append(
                "Long documents also need prompt processing time, which grows with length"
            )
        if downloaded:
            labels = list(dict.fromkeys(item.engine_label or item.model_ref for item in downloaded))
            where = ", ".join(labels[:2]) + (f" +{len(labels) - 2} more" if len(labels) > 2 else "")
            reasons.insert(0, f"Already downloaded ({where})")

        runnable: list[str] = []
        if model.sources.ollama and OLLAMA_ADAPTER_ID in engines:
            runnable.append(OLLAMA_ADAPTER_ID)
        if model.sources.gguf is not None:
            runnable.extend(sorted(GGUF_ADAPTER_IDS & engines))
        if model.sources.openvino and "localforge.openvino_genai" in engines:
            runnable.append("localforge.openvino_genai")
        if model.sources.onnx and "localforge.onnxruntime_genai" in engines:
            runnable.append("localforge.onnxruntime_genai")

        score_value = (
            FIT_POINTS[fit or "tight"]
            + _speed_points(model, use_case, machine, speed)
            + _size_points(model)
            + match_points
            + (DOWNLOADED_POINTS if downloaded else 0)
        )
        score = max(0, min(100, round(score_value)))
        gguf = model.sources.gguf
        suggestion = ModelSuggestion(
            rank=0,
            model_id=model.id,
            name=model.name,
            family=model.family,
            publisher=model.publisher,
            params_b=model.params_b,
            active_params_b=model.active_params_b,
            q4_size_gb=model.q4_size_gb,
            download_size_gb=model.download_size_gb,
            context_tokens=model.context_tokens,
            license=model.license,
            strengths=list(model.strengths),
            notes=model.notes,
            fit=fit or "tight",
            memory_required_gb=round(required_gb, 1),
            speed=speed,
            score=score,
            reasons=reasons,
            cautions=cautions,
            downloaded=downloaded,
            runnable_with=runnable,
            sources=ModelSourcesView(
                ollama_tag=model.sources.ollama,
                gguf_repo=gguf.repo if gguf else None,
                gguf_file=gguf.file if gguf else None,
                gguf_url=gguf_page_url(gguf) if gguf else None,
                openvino_repo=model.sources.openvino,
                onnx_repo=model.sources.onnx,
            ),
        )
        too_large = 1 if fit == "too_large" else 0
        ranked.append(((too_large, -score_value, order), suggestion))
    ranked.sort(key=lambda pair: pair[0])
    return [
        suggestion.model_copy(update={"rank": index})
        for index, (_key, suggestion) in enumerate(ranked, start=1)
    ]


SUGGESTION_METHOD = (
    "Ranked from a curated catalog by memory fit, estimated speed on this machine, model size, "
    "use-case match and what is already downloaded. Speeds are estimates (generation speed is "
    "about memory read speed divided by model size), not benchmark results; sizes are typical "
    "4-bit downloads. Benchmark a model to measure it."
)


class PullTarget(BaseModel):
    engine_profile_id: UUID
    label: str
    endpoint_url: str | None
    enabled: bool


class ModelSuggestionsResponse(BaseModel):
    generated_at: datetime
    use_case: UseCase
    use_case_label: str
    use_cases: list[UseCase]
    target_tokens_per_second: float
    catalog_version: str
    catalog_date: date
    method: str
    machine_summary: str
    memory_basis: Literal["gpu_memory", "system_memory"]
    memory_budget_bytes: int | None
    speed_basis: SpeedBasis
    read_gib_per_second: float | None
    ollama_reachable: bool
    pull_targets: list[PullTarget]
    suggestions: list[ModelSuggestion]


def pull_targets(profiles: Sequence[EngineProfile]) -> list[PullTarget]:
    """Local Ollama profiles a pull can go to (enabled ones first)."""

    targets = [
        PullTarget(
            engine_profile_id=profile.id,
            label=profile.label,
            endpoint_url=profile.endpoint_url,
            enabled=profile.status is EngineProfileStatus.ENABLED,
        )
        for profile in profiles
        if profile.adapter_id == OLLAMA_ADAPTER_ID
        and profile.integration_mode is EngineIntegrationMode.ATTACHED_ENDPOINT
        and profile.endpoint_url is not None
        and _is_local(profile)
    ]
    return sorted(targets, key=lambda target: not target.enabled)


def build_model_suggestions(
    use_case: UseCase,
    machine: SuggestionMachine,
    *,
    profiles: Sequence[EngineProfile] = (),
    installed: InstalledModels | None = None,
    installed_engines: Iterable[str] = (),
    ollama_reachable: bool = False,
    catalog: ModelCatalog | None = None,
) -> ModelSuggestionsResponse:
    catalog = catalog or load_model_catalog()
    if machine.read_gib_per_second is not None:
        speed_basis: SpeedBasis = "measured"
    elif machine.gpu_bandwidth_gb_s is not None:
        speed_basis = "gpu_bandwidth_rough"
    else:
        speed_basis = "unknown"
    return ModelSuggestionsResponse(
        generated_at=utc_now(),
        use_case=use_case,
        use_case_label=USE_CASE_LABELS[use_case],
        use_cases=list(USE_CASES),
        target_tokens_per_second=TARGET_TOKENS_PER_SECOND[use_case],
        catalog_version=catalog.catalog_version,
        catalog_date=catalog.catalog_date,
        method=SUGGESTION_METHOD,
        machine_summary=machine.summary,
        memory_basis=machine.memory_basis,
        memory_budget_bytes=machine.memory_budget_bytes,
        speed_basis=speed_basis,
        read_gib_per_second=machine.read_gib_per_second,
        ollama_reachable=ollama_reachable,
        pull_targets=pull_targets(profiles),
        suggestions=suggest_models(
            catalog,
            use_case,
            machine,
            installed=installed,
            installed_engines=installed_engines,
        ),
    )


# --- CLI ---------------------------------------------------------------------------------------


def _read_artifacts(database_path: Path) -> list[ModelArtifact]:
    if not database_path.is_file():
        return []
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            "SELECT payload_json FROM domain_records WHERE entity_type = ?",
            (ModelArtifact.__name__,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    artifacts: list[ModelArtifact] = []
    for row in rows:
        try:
            artifacts.append(ModelArtifact.model_validate(json.loads(row[0])))
        except ValueError:
            continue
    return artifacts


def suggestions_for_data_directory(
    data_directory: Path,
    recommendations: RecommendationResponse,
    use_cases: Sequence[UseCase] = ("chat", "coding"),
) -> list[ModelSuggestionsResponse]:
    """Model suggestions for ``localforge recommend`` (reads the database read-only)."""

    database_path = data_directory / "localforge.sqlite3"
    context = read_stored_context(database_path)
    view = recommendations.machine
    hardware = (
        context.saved_machine.hardware
        if context.saved_machine is not None and context.saved_machine.hardware is not None
        else HardwareSnapshot(
            cpu_model=view.cpu_model,
            memory_total_bytes=view.memory_total_bytes,
            accelerators=view.accelerators,
        )
    )
    machine = suggestion_machine(hardware, context.read_gib_per_second)
    machine = replace(machine, summary=view.summary)
    live = asyncio.run(fetch_ollama_tags(local_ollama_endpoints(context.saved_profiles)))
    installed = installed_from_inventory(
        context.saved_profiles, _read_artifacts(database_path), live
    )
    engines = {
        item.adapter_id
        for item in recommendations.recommendations
        if item.status in {"running", "installed"}
    }
    return [
        build_model_suggestions(
            use_case,
            machine,
            profiles=context.saved_profiles,
            installed=installed,
            installed_engines=engines,
            ollama_reachable=bool(live),
        )
        for use_case in use_cases
    ]


# --- Text rendering (CLI) ------------------------------------------------------------------


def render_model_suggestions_text(
    responses: Sequence[ModelSuggestionsResponse], *, limit: int = 3
) -> list[str]:
    """Plain-text "Models" section for ``localforge recommend``."""

    if not responses:
        return []
    first = responses[0]
    lines = [f"Models to try (catalog {first.catalog_version}, {first.catalog_date})"]
    for response in responses:
        lines.append(f"  {response.use_case_label}:")
        shown = [item for item in response.suggestions if item.fit != "too_large"][:limit]
        if not shown:
            lines.append("    No catalog model fits this machine's memory.")
        for item in shown:
            if item.speed.tokens_per_second is not None:
                speed = f"~{_format_rate(item.speed.tokens_per_second)} tok/s"
            else:
                speed = "speed unknown"
            where = item.sources.ollama_tag or item.sources.gguf_repo or item.model_id
            flags = ", downloaded" if item.downloaded else ""
            lines.append(
                f"    {item.rank}. {item.name} ({where}) Â· {item.download_size_gb:g} GB Â· "
                f"{speed} Â· {item.fit.replace('_', ' ')}{flags}"
            )
    basis = {
        "measured": "Speeds are estimated from your completed runs.",
        "gpu_bandwidth_rough": "Speeds are rough estimates from GPU memory bandwidth.",
        "unknown": "Benchmark one small model so LocalForge can estimate speeds here.",
    }[first.speed_basis]
    lines.append(f"  {basis} Open Models in the browser UI to pull one with Ollama.")
    return lines
