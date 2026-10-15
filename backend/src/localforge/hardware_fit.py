"""Which models this machine can run, and how well: detected hardware plus a per-model fit.

For every catalog model this picks the best quantization the machine can hold, says where it would
run (GPU memory, a GPU plus system RAM, or system RAM and the CPU), and grades the result:

- ``perfect``: fully in fast memory with room to spare (within 70% of the memory budget) and, when
  a speed estimate exists, at least 10 tokens/s.
- ``good``: fits in its memory budget and is usable.
- ``marginal``: only runs with part of the model in system RAM, is very close to the budget, or is
  estimated below 5 tokens/s.
- ``too_tight``: does not fit even at the smallest quantization.

Sizes scale the catalog's 4-bit size by each format's bits per weight. Speeds are estimates (the
same method as model suggestions), never benchmark results.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from localforge.core.models import AcceleratorInfo, HardwareSnapshot, utc_now
from localforge.model_catalog import (
    GIB,
    KV_OVERHEAD_FRACTION,
    KV_OVERHEAD_GIB,
    CatalogModel,
    InstalledModels,
    ModelCatalog,
    ModelSourcesView,
    SpeedBasis,
    Strength,
    SuggestionMachine,
    _size_points,
    estimate_speed,
    gguf_page_url,
)
from localforge.recommendations import (
    SYSTEM_MEMORY_HEADROOM_BYTES,
    friendly_cpu,
    machine_summary,
)

RunMode = Literal["gpu", "gpu_and_ram", "cpu", "none"]
FitTier = Literal["perfect", "good", "marginal", "too_tight"]
ComputePath = Literal["discrete_gpu", "unified_memory", "cpu_only"]
AcceleratorKind = Literal["discrete", "integrated", "unified", "unknown"]

# GGUF quantizations, best quality first, with approximate bits per weight.
QUANTIZATIONS: tuple[tuple[str, float], ...] = (
    ("Q8_0", 8.5),
    ("Q6_K", 6.56),
    ("Q5_K_M", 5.69),
    ("Q4_K_M", 4.85),
    ("Q3_K_M", 3.91),
    ("Q2_K", 2.96),
)
BASE_QUANT_BITS = 4.85
# Below this, quality loss is large enough that the result says so.
LOWER_QUALITY_BELOW_BITS = 4.5
GPU_BUDGET_FRACTION = 0.85
UNIFIED_BUDGET_FRACTION = 0.75
COMFORTABLE_FRACTION = 0.7
PERFECT_TOKENS_PER_SECOND = 10.0
MARGINAL_TOKENS_PER_SECOND = 5.0
# With no measurement, grade as if memory streamed at this rate (typical laptop or desktop RAM),
# so a large model is not called "good" on a CPU just because it fits.
ASSUMED_READ_GIB_PER_SECOND = 20.0
QUALITY_POINTS = 10
SPEED_POINTS = 15
TIER_POINTS: dict[FitTier, int] = {"perfect": 50, "good": 35, "marginal": 15, "too_tight": 0}
TIER_ORDER: dict[FitTier, int] = {"perfect": 0, "good": 1, "marginal": 2, "too_tight": 3}


class FitAccelerator(BaseModel):
    name: str
    vendor: str | None
    memory_gb: float | None
    kind: AcceleratorKind
    runtime: str | None
    driver_version: str | None


class FitMachine(BaseModel):
    summary: str
    os: str | None
    cpu_model: str | None
    cpu_logical_cores: int | None
    cpu_physical_cores: int | None
    memory_total_gb: float | None
    accelerators: list[FitAccelerator]
    accelerators_detected: bool
    compute_path: ComputePath
    compute_path_label: str
    gpu_budget_gb: float | None
    ram_budget_gb: float | None
    notes: list[str]


class FitModel(BaseModel):
    model_id: str
    name: str
    family: str
    publisher: str
    params_b: float
    active_params_b: float | None
    context_tokens: int
    license: str
    strengths: list[Strength]
    notes: str | None
    quantization: str
    quantization_bits: float
    lower_quality: bool
    size_gb: float
    memory_required_gb: float
    run_mode: RunMode
    tier: FitTier
    tokens_per_second: float | None
    speed_basis: SpeedBasis
    speed_label: str
    score: int
    explanation: str
    downloaded: bool
    sources: ModelSourcesView


class HardwareFitResponse(BaseModel):
    generated_at: datetime
    catalog_version: str
    method: str
    detected_by: str
    machine: FitMachine
    models: list[FitModel]


FIT_METHOD = (
    "Each model's 4-bit size from the curated catalog is scaled to every common GGUF "
    "quantization (Q8_0 down to Q2_K). A model needs its file size plus 10% and 0.5 GB for the "
    "context cache and runtime. The best quantization that fits comfortably is chosen. "
    "GPU memory budget is 85% of a discrete GPU's memory (75% of RAM on Apple silicon); "
    "otherwise system RAM minus 6 GB. Speeds are estimates (about memory bandwidth divided by "
    "model size), refined by your own benchmark runs when you have them, and are not benchmark "
    "results."
)


def _gb(value: int) -> float:
    return round(value / GIB, 1)


def _accelerator_kind(accelerator: AcceleratorInfo) -> AcceleratorKind:
    vendor = (accelerator.vendor or "").casefold()
    if vendor == "apple" or accelerator.name.startswith("Apple "):
        return "unified"
    if accelerator.integrated is True:
        return "integrated"
    if accelerator.integrated is False and accelerator.memory_kind == "dedicated":
        return "discrete"
    return "unknown"


def _fit_accelerator(accelerator: AcceleratorInfo) -> FitAccelerator:
    return FitAccelerator(
        name=friendly_cpu(accelerator.name) or accelerator.name,
        vendor=accelerator.vendor,
        memory_gb=_gb(accelerator.memory_bytes) if accelerator.memory_bytes else None,
        kind=_accelerator_kind(accelerator),
        runtime=accelerator.runtime,
        driver_version=accelerator.driver_version,
    )


class MemoryPools(BaseModel):
    compute_path: ComputePath
    gpu_budget_gib: float | None
    ram_budget_gib: float | None


def memory_pools(hardware: HardwareSnapshot) -> MemoryPools:
    accelerators = hardware.accelerators or []
    ram = hardware.memory_total_bytes
    ram_budget = max(0.0, (ram - SYSTEM_MEMORY_HEADROOM_BYTES) / GIB) if ram is not None else None
    discrete = [
        item.memory_bytes
        for item in accelerators
        if item.memory_bytes
        and _accelerator_kind(item) == "discrete"
        and (item.vendor or "").casefold() in {"nvidia", "amd", "intel"}
    ]
    if discrete:
        return MemoryPools(
            compute_path="discrete_gpu",
            gpu_budget_gib=max(discrete) * GPU_BUDGET_FRACTION / GIB,
            ram_budget_gib=ram_budget,
        )
    if ram is not None and any(_accelerator_kind(item) == "unified" for item in accelerators):
        return MemoryPools(
            compute_path="unified_memory",
            gpu_budget_gib=ram * UNIFIED_BUDGET_FRACTION / GIB,
            ram_budget_gib=None,
        )
    return MemoryPools(compute_path="cpu_only", gpu_budget_gib=None, ram_budget_gib=ram_budget)


COMPUTE_PATH_LABELS: dict[ComputePath, str] = {
    "discrete_gpu": "Discrete GPU: models run from GPU memory, with system RAM as overflow",
    "unified_memory": "Apple silicon: the CPU and GPU share one pool of unified memory",
    "cpu_only": "CPU and system RAM (no discrete GPU detected)",
}


def describe_machine(
    hardware: HardwareSnapshot, pools: MemoryPools, summary: str | None = None
) -> FitMachine:
    accelerators = [_fit_accelerator(item) for item in hardware.accelerators or []]
    notes: list[str] = []
    if hardware.accelerators is None:
        notes.append("GPUs could not be detected, so models are graded for the CPU and RAM only.")
    if pools.compute_path == "cpu_only" and any(item.kind == "integrated" for item in accelerators):
        notes.append(
            "An integrated GPU shares system RAM, so it does not add memory. Engines with "
            "Vulkan or OpenVINO support can use it to speed up reading long prompts."
        )
    if hardware.memory_total_bytes is None:
        notes.append("System memory was not detected, so fit cannot be graded.")
    if sum(1 for item in accelerators if item.kind == "discrete") > 1:
        notes.append("Several GPUs were found; fit uses the single largest one.")
    os_text = " ".join(part for part in (hardware.os_name, hardware.os_version) if part) or None
    return FitMachine(
        summary=summary or machine_summary(hardware),
        os=os_text,
        cpu_model=friendly_cpu(hardware.cpu_model),
        cpu_logical_cores=hardware.cpu_logical_cores,
        cpu_physical_cores=hardware.cpu_physical_cores,
        memory_total_gb=_gb(hardware.memory_total_bytes) if hardware.memory_total_bytes else None,
        accelerators=accelerators,
        accelerators_detected=hardware.accelerators is not None,
        compute_path=pools.compute_path,
        compute_path_label=COMPUTE_PATH_LABELS[pools.compute_path],
        gpu_budget_gb=round(pools.gpu_budget_gib, 1) if pools.gpu_budget_gib is not None else None,
        ram_budget_gb=round(pools.ram_budget_gib, 1) if pools.ram_budget_gib is not None else None,
        notes=notes,
    )


def _required_gib(size_gib: float) -> float:
    return size_gib * (1 + KV_OVERHEAD_FRACTION) + KV_OVERHEAD_GIB


def _place(required: float, pools: MemoryPools) -> tuple[RunMode, FitTier | None]:
    """Where a model of this memory need would run, and the grade memory alone allows."""

    gpu, ram = pools.gpu_budget_gib, pools.ram_budget_gib
    if pools.compute_path == "cpu_only":
        if ram is None:
            return "none", None
        if required <= ram * COMFORTABLE_FRACTION:
            return "cpu", "good"
        return ("cpu", "marginal") if required <= ram else ("none", "too_tight")
    assert gpu is not None
    if required <= gpu * COMFORTABLE_FRACTION:
        return "gpu", "perfect"
    if required <= gpu:
        return "gpu", "good"
    if ram is not None and required <= gpu + ram:
        return "gpu_and_ram", "marginal"
    return "none", "too_tight"


def _speed_machine(machine: SuggestionMachine, mode: RunMode) -> SuggestionMachine:
    # Published GPU bandwidth only applies when the whole model sits in GPU memory.
    return machine if mode == "gpu" else replace(machine, gpu_bandwidth_gb_s=None, gpu_name=None)


def _apply_speed(tier: FitTier, tps: float | None, read_gib: float) -> FitTier:
    if tier == "too_tight":
        return tier
    rate = tps if tps is not None else ASSUMED_READ_GIB_PER_SECOND / read_gib
    if rate < MARGINAL_TOKENS_PER_SECOND:
        return "marginal"
    if tier == "perfect" and rate < PERFECT_TOKENS_PER_SECOND:
        return "good"
    return tier


def _explain(
    mode: RunMode,
    tier: FitTier,
    required: float,
    pools: MemoryPools,
    tps: float | None,
    lower_quality: bool,
) -> str:
    gpu, ram = pools.gpu_budget_gib, pools.ram_budget_gib
    if mode == "gpu":
        text = f"Runs from GPU memory: needs about {required:.1f} of {gpu:.1f} GB available."
    elif mode == "gpu_and_ram":
        text = (
            f"Needs about {required:.1f} GB but the GPU has {gpu:.1f} GB, so part of the model "
            "would sit in system RAM; that is much slower than a full GPU load."
        )
    elif mode == "cpu":
        text = f"Runs on the CPU from system RAM: needs about {required:.1f} of {ram:.1f} GB."
    else:
        available = (gpu or 0) + (ram or 0) if pools.compute_path == "discrete_gpu" else ram
        text = (
            f"Needs about {required:.1f} GB even at the smallest quantization; "
            f"about {(available or 0):.1f} GB is available."
        )
    if tps is not None and mode != "none":
        text += f" Estimated {tps:.0f} tokens/s." if tps >= 3 else f" Estimated {tps:.1f} tokens/s."
    if tps is None and mode != "none":
        text += (
            " Speed is unknown until you benchmark a model here; the grade assumes typical "
            "memory speed."
        )
    if lower_quality:
        text += " A smaller quantization is needed, which reduces answer quality somewhat."
    return text


class _Candidate(BaseModel):
    name: str
    bits: float
    size_gib: float
    required: float
    mode: RunMode
    tier: FitTier
    tps: float | None
    basis: SpeedBasis
    label: str


def _best_candidate(
    model: CatalogModel, pools: MemoryPools, machine: SuggestionMachine
) -> _Candidate:
    """The highest-quality quantization with the best grade; the smallest one if none fits."""

    fitting: list[_Candidate] = []
    for name, bits in QUANTIZATIONS:
        size_gib = model.size_gib * bits / BASE_QUANT_BITS
        required = _required_gib(size_gib)
        mode, memory_tier = _place(required, pools)
        if mode == "none" or memory_tier is None:
            continue
        scaled = model.model_copy(update={"q4_size_gb": size_gib * GIB / 1e9})
        speed = estimate_speed(scaled, _speed_machine(machine, mode), "fits")
        fitting.append(
            _Candidate(
                name=name,
                bits=bits,
                size_gib=size_gib,
                required=required,
                mode=mode,
                tier=_apply_speed(
                    memory_tier, speed.tokens_per_second, size_gib * model.read_share
                ),
                tps=speed.tokens_per_second,
                basis=speed.basis,
                label=speed.label,
            )
        )
    # Quantizations below Q4 lose noticeable quality: use them only when nothing better fits.
    usable = [item for item in fitting if item.bits >= LOWER_QUALITY_BELOW_BITS] or fitting
    if usable:
        best = min(TIER_ORDER[item.tier] for item in usable)
        graded = [item for item in usable if TIER_ORDER[item.tier] == best]
        # Among equally graded quantizations take the best quality, except when the model only
        # just runs: then the smallest usable one is the one worth downloading.
        return graded[-1] if best >= TIER_ORDER["marginal"] else graded[0]
    name, bits = QUANTIZATIONS[-1]
    size_gib = model.size_gib * bits / BASE_QUANT_BITS
    return _Candidate(
        name=name,
        bits=bits,
        size_gib=size_gib,
        required=_required_gib(size_gib),
        mode="none",
        tier="too_tight",
        tps=None,
        basis="unknown",
        label="not estimated: the model does not fit",
    )


def evaluate_model(
    model: CatalogModel,
    pools: MemoryPools,
    machine: SuggestionMachine,
    installed: InstalledModels,
) -> FitModel:
    best = _best_candidate(model, pools, machine)
    name, bits, size_gib, required = best.name, best.bits, best.size_gib, best.required
    mode, tier, tps, basis, label = best.mode, best.tier, best.tps, best.basis, best.label
    lower_quality = bits < LOWER_QUALITY_BELOW_BITS and mode != "none"
    quality_points = QUALITY_POINTS * min(1.0, bits / QUANTIZATIONS[0][1] / 0.75)
    speed_points = SPEED_POINTS * min(1.0, tps / 20.0) if tps is not None else SPEED_POINTS * 0.5
    score = round(TIER_POINTS[tier] + quality_points + _size_points(model) + speed_points)
    gguf = model.sources.gguf
    downloaded = bool(installed.locations_for(model))
    return FitModel(
        model_id=model.id,
        name=model.name,
        family=model.family,
        publisher=model.publisher,
        params_b=model.params_b,
        active_params_b=model.active_params_b,
        context_tokens=model.context_tokens,
        license=model.license,
        strengths=list(model.strengths),
        notes=model.notes,
        quantization=name,
        quantization_bits=bits,
        lower_quality=lower_quality,
        size_gb=round(size_gib * GIB / 1e9, 1),
        memory_required_gb=round(required * GIB / 1e9, 1),
        run_mode=mode,
        tier=tier,
        tokens_per_second=tps,
        speed_basis=basis,
        speed_label=label,
        score=max(0, min(100, score)),
        explanation=_explain(mode, tier, required, pools, tps, lower_quality),
        downloaded=downloaded,
        sources=ModelSourcesView(
            ollama_tag=model.sources.ollama,
            gguf_repo=gguf.repo if gguf else None,
            gguf_file=gguf.file if gguf else None,
            gguf_url=gguf_page_url(gguf) if gguf else None,
            openvino_repo=model.sources.openvino,
            onnx_repo=model.sources.onnx,
        ),
    )


def build_hardware_fit(
    hardware: HardwareSnapshot,
    catalog: ModelCatalog,
    *,
    detected_by: str,
    machine: SuggestionMachine,
    installed: InstalledModels | None = None,
    models: Iterable[CatalogModel] | None = None,
) -> HardwareFitResponse:
    pools = memory_pools(hardware)
    installed = installed or InstalledModels()
    fits = [
        evaluate_model(model, pools, machine, installed)
        for model in (catalog.models if models is None else models)
    ]
    fits.sort(key=lambda item: (TIER_ORDER[item.tier], -item.score, item.name))
    return HardwareFitResponse(
        generated_at=utc_now(),
        catalog_version=catalog.catalog_version,
        method=FIT_METHOD,
        detected_by=detected_by,
        machine=describe_machine(hardware, pools, machine.summary or None),
        models=fits,
    )
