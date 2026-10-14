"""Rank inference engines by how well they fit this machine.

The ranking is a documented rule table plus one small scoring function. It never claims a
speed: the only performance numbers it reports are the user's own completed benchmark runs.

Score (0-100) = hardware fit (0-65, dominant) + setup ease on this OS (0-15)
              + status bonus (running 15, installed 10) + measured bonus (5).
The fit tier uses only hardware + setup ("fit points"), so installing an engine changes its
rank but not whether it suits the hardware. Any unmet hard requirement makes an engine
"unsuitable" with score 0, ranked last.
"""

import asyncio
import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx2
from pydantic import BaseModel

from localforge.adapters.contracts import AdapterCategory, AdapterManifest
from localforge.core.models import (
    AcceleratorInfo,
    EngineProfile,
    HardwareSnapshot,
    MachineProfile,
    MachineProfileStatus,
    utc_now,
)
from localforge.core.urls import endpoint_is_remote
from localforge.discovery import (
    DEFAULT_DISCOVERY_PORTS,
    DiscoveryCandidate,
    SavedEndpoint,
    discover_engines,
)
from localforge.system.inspection import CpuFeatures, detect_cpu_features, inspect_local_machine

RECOMMENDATION_METHOD = (
    "Ranked by fit for this machine's hardware and what is already installed. This is not a "
    "speed measurement; measured results come from your own benchmark runs."
)

GIB = 1024**3
# Model-size guidance constants (4-bit quantized weights plus KV cache for a 4k context).
GPU_MEMORY_USABLE_FRACTION = 0.85
SYSTEM_MEMORY_HEADROOM_BYTES = 6 * GIB
Q4_GIB_PER_BILLION_PARAMS = 0.6
COMFORTABLE_MEMORY_FRACTION = 0.7
# Generation speed a reader can follow comfortably; used to size models on CPU-only machines.
READABLE_TOKENS_PER_SECOND = 10

# Scoring constants.
STATUS_BONUS: dict[str, int] = {"running": 15, "installed": 10, "not_installed": 0, "unknown": 0}
MEASURED_BONUS = 5
BEST_FIT_POINTS = 62
GOOD_FIT_POINTS = 50
NO_AVX2_PENALTY = 10
SMALL_GPU_PENALTY = 10
UNKNOWN_GPU_HARDWARE_POINTS = 20

OsFamily = Literal["windows", "linux", "macos"]
HardwareClass = Literal["cpu", "intel_cpu", "intel_gpu", "nvidia", "amd_gpu", "apple"]
Fit = Literal["best", "good", "possible", "unsuitable"]
EngineStatus = Literal["running", "installed", "not_installed", "unknown"]
SnapshotSource = Literal["saved_profile", "live_detection"]

_GPU_CLASSES: frozenset[HardwareClass] = frozenset({"intel_gpu", "nvidia", "amd_gpu", "apple"})
_CPU_CLASSES: frozenset[HardwareClass] = frozenset({"cpu", "intel_cpu"})


@dataclass(frozen=True)
class InstallInfo:
    summary: str
    url: str
    command: str | None = None


@dataclass(frozen=True)
class EngineRule:
    """One engine's hard requirements, hardware fit, setup ease and install hints.

    ``hardware`` maps each hardware class the engine can use to fit points (0-65); classes
    missing from it cannot run the engine. ``setup`` maps each host OS where the engine runs
    to ease points (0-15); a missing OS is a hard requirement. ``container_on`` lists OSes
    where the engine only runs inside WSL2 or Docker.
    """

    adapter_id: str
    name: str
    hardware: Mapping[HardwareClass, int]
    strengths: Mapping[HardwareClass, str]
    hardware_requirement: str
    setup: Mapping[OsFamily, int]
    os_requirement: str
    install: Mapping[OsFamily, InstallInfo]
    container_on: frozenset[OsFamily] = frozenset()
    cpu_requires_avx512: bool = False
    uses_cpu_simd: bool = False
    # Serving engines built for large GPUs lose points below this much NVIDIA memory.
    large_gpu_bytes: int | None = None
    cautions: tuple[str, ...] = ()
    setup_notes: Mapping[OsFamily, str] = field(default_factory=dict)
    executables: tuple[str, ...] = ()
    python_modules: tuple[str, ...] = ()
    install_paths: Mapping[OsFamily, tuple[str, ...]] = field(default_factory=dict)
    # Engines that cannot be reliably detected on disk report "unknown" instead of
    # "not_installed" when nothing is found.
    detectable: bool = True


_ALL_OS: tuple[OsFamily, ...] = ("windows", "linux", "macos")
_NVIDIA_REQUIREMENT = "Needs an NVIDIA GPU with CUDA"
_LINUX_REQUIREMENT = "Runs on Linux; on Windows only inside WSL2 or Docker"
_LINUX_ONLY = "Runs on Linux only"
_CONTAINER_CAUTION = "On Windows this runs only inside WSL2 or Docker with GPU passthrough."


def _same(value: InstallInfo) -> dict[OsFamily, InstallInfo]:
    return dict.fromkeys(_ALL_OS, value)


ENGINE_RULES: tuple[EngineRule, ...] = (
    EngineRule(
        adapter_id="localforge.ollama",
        name="Ollama",
        hardware={"cpu": 50, "nvidia": 52, "amd_gpu": 48, "apple": 54},
        strengths={
            "cpu": "Runs 4-bit GGUF models well on the CPU",
            "nvidia": "Uses NVIDIA GPUs through CUDA automatically",
            "amd_gpu": "Supports AMD GPUs through ROCm",
            "apple": "Uses Apple silicon GPUs through Metal",
        },
        hardware_requirement="",
        setup={"windows": 15, "linux": 15, "macos": 15},
        setup_notes=dict.fromkeys(_ALL_OS, "One installer; pulls models by name"),
        os_requirement="",
        uses_cpu_simd=True,
        executables=("ollama",),
        install_paths={
            "windows": (r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",),
            "macos": ("/Applications/Ollama.app",),
        },
        install={
            "windows": InstallInfo(
                "Windows installer from ollama.com, or winget.",
                "https://ollama.com/download",
                "winget install Ollama.Ollama",
            ),
            "linux": InstallInfo(
                "Official install script.",
                "https://ollama.com/download",
                "curl -fsSL https://ollama.com/install.sh | sh",
            ),
            "macos": InstallInfo("Download the macOS app.", "https://ollama.com/download"),
        },
    ),
    EngineRule(
        adapter_id="localforge.llama_cpp",
        name="llama.cpp",
        hardware={"cpu": 52, "intel_gpu": 55, "nvidia": 54, "amd_gpu": 52, "apple": 56},
        strengths={
            "cpu": "Reference GGUF engine with optimized CPU kernels",
            "intel_gpu": "Vulkan and SYCL builds can offload to Intel GPUs",
            "nvidia": "CUDA builds offload layers to NVIDIA GPUs",
            "amd_gpu": "Vulkan and HIP builds can offload to AMD GPUs",
            "apple": "Metal backend is built in on Apple silicon",
        },
        hardware_requirement="",
        setup={"windows": 12, "linux": 12, "macos": 13},
        setup_notes=dict.fromkeys(
            _ALL_OS, "Prebuilt binaries; LocalForge can launch llama-server for you"
        ),
        os_requirement="",
        uses_cpu_simd=True,
        executables=("llama-server",),
        install={
            "windows": InstallInfo(
                "Prebuilt release zips (CPU, Vulkan, SYCL, CUDA), or winget.",
                "https://github.com/ggml-org/llama.cpp/releases",
                "winget install llama.cpp",
            ),
            "linux": InstallInfo(
                "Prebuilt release binaries, Homebrew, or build from source.",
                "https://github.com/ggml-org/llama.cpp/releases",
                "brew install llama.cpp",
            ),
            "macos": InstallInfo(
                "Homebrew or prebuilt release binaries.",
                "https://github.com/ggml-org/llama.cpp/releases",
                "brew install llama.cpp",
            ),
        },
    ),
    EngineRule(
        adapter_id="localforge.koboldcpp",
        name="KoboldCpp",
        hardware={"cpu": 50, "intel_gpu": 52, "nvidia": 50, "amd_gpu": 48, "apple": 50},
        strengths={
            "cpu": "llama.cpp-based single executable that runs GGUF models on the CPU",
            "intel_gpu": "Vulkan backend can offload to Intel GPUs",
            "nvidia": "CUDA build offloads to NVIDIA GPUs",
            "amd_gpu": "Vulkan backend can offload to AMD GPUs",
            "apple": "Metal backend on Apple silicon",
        },
        hardware_requirement="",
        setup={"windows": 13, "linux": 12, "macos": 11},
        setup_notes=dict.fromkeys(_ALL_OS, "Single download, no installer"),
        os_requirement="",
        uses_cpu_simd=True,
        executables=("koboldcpp", "koboldcpp-nocuda", "koboldcpp_nocuda", "koboldcpp_cu12"),
        install={
            "windows": InstallInfo(
                "Download koboldcpp.exe (koboldcpp-nocuda.exe without an NVIDIA GPU).",
                "https://github.com/LostRuins/koboldcpp/releases",
            ),
            "linux": InstallInfo(
                "Download the Linux binary from GitHub releases.",
                "https://github.com/LostRuins/koboldcpp/releases",
            ),
            "macos": InstallInfo(
                "Download the macOS ARM64 binary from GitHub releases.",
                "https://github.com/LostRuins/koboldcpp/releases",
            ),
        },
    ),
    EngineRule(
        adapter_id="localforge.openvino_genai",
        name="OpenVINO GenAI",
        hardware={"cpu": 38, "intel_cpu": 50, "intel_gpu": 58},
        strengths={
            "cpu": "Runs on x86 and ARM CPUs",
            "intel_cpu": "Intel's own runtime, optimized for Intel CPUs",
            "intel_gpu": "Intel's own runtime; targets Intel CPUs, integrated GPUs and NPUs",
        },
        hardware_requirement="",
        setup={"windows": 9, "linux": 9, "macos": 8},
        setup_notes=dict.fromkeys(
            _ALL_OS, "Python package; models must be exported to OpenVINO format"
        ),
        os_requirement="",
        python_modules=("openvino_genai",),
        cautions=(
            "Models usually need converting to OpenVINO IR first (Optimum Intel); GGUF "
            "support is a preview.",
        ),
        install=_same(
            InstallInfo(
                "pip package; export models with Optimum Intel.",
                "https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai.html",
                "pip install openvino-genai",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.onnxruntime_genai",
        name="ONNX Runtime GenAI",
        hardware={"cpu": 40, "intel_gpu": 44, "amd_gpu": 44, "nvidia": 50},
        strengths={
            "cpu": "CPU package works on any x86 or ARM machine",
            "intel_gpu": "DirectML package can use Intel GPUs on Windows",
            "amd_gpu": "DirectML package can use AMD GPUs on Windows",
            "nvidia": "CUDA package targets NVIDIA GPUs",
        },
        hardware_requirement="",
        setup={"windows": 9, "linux": 8, "macos": 8},
        setup_notes=dict.fromkeys(_ALL_OS, "Python package; needs ONNX GenAI model folders"),
        os_requirement="",
        python_modules=("onnxruntime_genai",),
        cautions=("Needs models exported to the ONNX GenAI format; the Python API is a preview.",),
        install={
            "windows": InstallInfo(
                "DirectML package for any DirectX 12 GPU; onnxruntime-genai for CPU, "
                "onnxruntime-genai-cuda for NVIDIA.",
                "https://onnxruntime.ai/docs/genai/howto/install.html",
                "pip install onnxruntime-genai-directml",
            ),
            "linux": InstallInfo(
                "CPU package; onnxruntime-genai-cuda for NVIDIA.",
                "https://onnxruntime.ai/docs/genai/howto/install.html",
                "pip install onnxruntime-genai",
            ),
            "macos": InstallInfo(
                "CPU package.",
                "https://onnxruntime.ai/docs/genai/howto/install.html",
                "pip install onnxruntime-genai",
            ),
        },
    ),
    EngineRule(
        adapter_id="localforge.mlc_llm",
        name="MLC LLM",
        hardware={"intel_gpu": 44, "nvidia": 46, "amd_gpu": 46, "apple": 48},
        strengths={
            "intel_gpu": "Vulkan backend can use Intel GPUs",
            "nvidia": "CUDA and Vulkan backends",
            "amd_gpu": "ROCm and Vulkan backends",
            "apple": "Metal backend on Apple silicon",
        },
        hardware_requirement="Needs a GPU with Vulkan, Metal, CUDA or ROCm",
        setup={"windows": 6, "linux": 7, "macos": 7},
        setup_notes=dict.fromkeys(_ALL_OS, "Prebuilt wheels; models are compiled per device"),
        os_requirement="",
        executables=("mlc_llm",),
        python_modules=("mlc_llm",),
        install=_same(
            InstallInfo(
                "Prebuilt Python wheels; compile or download MLC-format models.",
                "https://llm.mlc.ai/docs/install/mlc_llm.html",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.localai",
        name="LocalAI",
        hardware={"cpu": 44, "intel_gpu": 44, "nvidia": 46, "amd_gpu": 42, "apple": 44},
        strengths={
            "cpu": "Wraps llama.cpp and other backends behind one OpenAI-compatible API",
            "intel_gpu": "Has Intel GPU (SYCL) images",
            "nvidia": "Has CUDA images",
            "amd_gpu": "Has ROCm images",
            "apple": "Native macOS build with Metal",
        },
        hardware_requirement="",
        setup={"windows": 5, "linux": 10, "macos": 10},
        container_on=frozenset({"windows"}),
        setup_notes={
            "windows": "Runs in Docker or WSL2 on Windows",
            "linux": "Install script or Docker image",
            "macos": "macOS app or Docker image",
        },
        os_requirement="",
        uses_cpu_simd=True,
        executables=("local-ai",),
        install={
            "windows": InstallInfo(
                "Run the official Docker image (Docker Desktop or WSL2).",
                "https://localai.io/installation/",
            ),
            "linux": InstallInfo(
                "Official install script or Docker image.",
                "https://localai.io/installation/",
                "curl https://localai.io/install.sh | sh",
            ),
            "macos": InstallInfo("macOS app or Docker image.", "https://localai.io/installation/"),
        },
    ),
    EngineRule(
        adapter_id="localforge.mistral_rs",
        name="mistral.rs",
        hardware={"cpu": 42, "nvidia": 50, "apple": 50},
        strengths={
            "cpu": "Rust engine with CPU builds",
            "nvidia": "CUDA builds for NVIDIA GPUs",
            "apple": "Metal builds on Apple silicon",
        },
        hardware_requirement="",
        setup={"windows": 7, "linux": 8, "macos": 9},
        setup_notes=dict.fromkeys(_ALL_OS, "Prebuilt release or a Rust (cargo) build"),
        os_requirement="",
        uses_cpu_simd=True,
        executables=("mistralrs-server", "mistralrs"),
        install=_same(
            InstallInfo(
                "Prebuilt release or cargo build; see the quickstart.",
                "https://ericlbuehler.github.io/mistral.rs/quickstart/",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.openai_compatible",
        name="OpenAI-compatible server",
        hardware={"cpu": 36},
        strengths={"cpu": "Benchmarks any server with an OpenAI-compatible chat API"},
        hardware_requirement="",
        setup={"windows": 12, "linux": 10, "macos": 12},
        setup_notes=dict.fromkeys(_ALL_OS, "Works with LM Studio, Jan and similar desktop apps"),
        os_requirement="",
        executables=("lms",),
        install_paths={
            "windows": (
                r"%LOCALAPPDATA%\Programs\LM Studio\LM Studio.exe",
                r"~\.lmstudio\bin\lms.exe",
            ),
            "linux": ("~/.lmstudio/bin/lms",),
            "macos": ("/Applications/LM Studio.app", "~/.lmstudio/bin/lms"),
        },
        detectable=False,
        cautions=("LocalForge cannot see which engine runs behind a generic endpoint.",),
        install=_same(
            InstallInfo(
                "Any server exposing /v1/chat/completions, for example LM Studio.",
                "https://lmstudio.ai/",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.vllm",
        name="vLLM",
        hardware={"nvidia": 65, "amd_gpu": 50, "cpu": 30},
        strengths={
            "nvidia": "High-throughput batched serving on NVIDIA GPUs",
            "amd_gpu": "ROCm builds serve on AMD GPUs",
            "cpu": "CPU builds exist for AVX-512 processors",
        },
        hardware_requirement=f"{_NVIDIA_REQUIREMENT} (CPU builds need AVX-512)",
        setup={"linux": 10, "windows": 3},
        container_on=frozenset({"windows"}),
        setup_notes={"linux": "pip install on Linux", "windows": "Needs WSL2 or Docker"},
        os_requirement=_LINUX_REQUIREMENT,
        cpu_requires_avx512=True,
        large_gpu_bytes=16 * GIB,
        executables=("vllm",),
        python_modules=("vllm",),
        install={
            "linux": InstallInfo(
                "pip package for Linux with CUDA.",
                "https://docs.vllm.ai/en/stable/getting_started/installation/",
                "pip install vllm",
            ),
            "windows": InstallInfo(
                "Not native on Windows; install the Linux package inside WSL2 or use Docker.",
                "https://docs.vllm.ai/en/stable/getting_started/installation/",
            ),
            "macos": InstallInfo(
                "Not supported for GPU serving on macOS.",
                "https://docs.vllm.ai/en/stable/getting_started/installation/",
            ),
        },
    ),
    EngineRule(
        adapter_id="localforge.sglang",
        name="SGLang",
        hardware={"nvidia": 63, "amd_gpu": 48},
        strengths={
            "nvidia": "High-throughput serving with prefix caching on NVIDIA GPUs",
            "amd_gpu": "ROCm support for AMD Instinct GPUs",
        },
        hardware_requirement=_NVIDIA_REQUIREMENT,
        setup={"linux": 8, "windows": 3},
        container_on=frozenset({"windows"}),
        setup_notes={"linux": "pip package or Docker image", "windows": "Needs WSL2 or Docker"},
        os_requirement=_LINUX_REQUIREMENT,
        large_gpu_bytes=16 * GIB,
        python_modules=("sglang",),
        install=_same(
            InstallInfo(
                "pip package or the lmsysorg/sglang Docker image (Linux).",
                "https://docs.sglang.ai/get_started/install.html",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.tensorrt_llm",
        name="TensorRT-LLM",
        hardware={"nvidia": 62},
        strengths={"nvidia": "NVIDIA's optimized runtime for its own GPUs"},
        hardware_requirement=_NVIDIA_REQUIREMENT,
        setup={"linux": 6, "windows": 2},
        container_on=frozenset({"windows"}),
        setup_notes={
            "linux": "NVIDIA container or a CUDA-matched wheel",
            "windows": "Needs WSL2 or Docker",
        },
        os_requirement=_LINUX_REQUIREMENT,
        large_gpu_bytes=16 * GIB,
        executables=("trtllm-serve",),
        python_modules=("tensorrt_llm",),
        install=_same(
            InstallInfo(
                "NVIDIA container or pip wheel matched to your CUDA version (Linux).",
                "https://nvidia.github.io/TensorRT-LLM/installation/",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.lmdeploy",
        name="LMDeploy",
        hardware={"nvidia": 56},
        strengths={"nvidia": "TurboMind CUDA engine for NVIDIA GPUs"},
        hardware_requirement=_NVIDIA_REQUIREMENT,
        setup={"linux": 9, "windows": 7},
        setup_notes={"linux": "pip package", "windows": "pip package with CUDA"},
        os_requirement="Runs on Linux and Windows",
        large_gpu_bytes=16 * GIB,
        executables=("lmdeploy",),
        python_modules=("lmdeploy",),
        install=_same(
            InstallInfo(
                "pip package (CUDA).",
                "https://lmdeploy.readthedocs.io/en/latest/get_started/installation.html",
                "pip install lmdeploy",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.lightllm",
        name="LightLLM",
        hardware={"nvidia": 50},
        strengths={"nvidia": "Python serving framework for NVIDIA GPUs"},
        hardware_requirement=_NVIDIA_REQUIREMENT,
        setup={"linux": 5, "windows": 2},
        container_on=frozenset({"windows"}),
        setup_notes={"linux": "Docker image or source install", "windows": "Needs WSL2 or Docker"},
        os_requirement=_LINUX_REQUIREMENT,
        large_gpu_bytes=16 * GIB,
        python_modules=("lightllm",),
        install=_same(
            InstallInfo(
                "Official Docker image or source install (Linux).",
                "https://github.com/ModelTC/LightLLM",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.tgi",
        name="Hugging Face TGI",
        hardware={"nvidia": 44, "amd_gpu": 38},
        strengths={
            "nvidia": "Production serving container for NVIDIA GPUs",
            "amd_gpu": "ROCm container for AMD Instinct GPUs",
        },
        hardware_requirement=_NVIDIA_REQUIREMENT,
        setup={"linux": 6, "windows": 2},
        container_on=frozenset({"windows"}),
        setup_notes={"linux": "Official Docker image", "windows": "Needs WSL2 or Docker"},
        os_requirement=_LINUX_REQUIREMENT,
        large_gpu_bytes=16 * GIB,
        executables=("text-generation-launcher",),
        detectable=False,
        cautions=("TGI is in maintenance mode; prefer another engine for new setups.",),
        install=_same(
            InstallInfo(
                "Official Docker image (ghcr.io/huggingface/text-generation-inference).",
                "https://huggingface.co/docs/text-generation-inference/en/installation",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.nvidia_dynamo",
        name="NVIDIA Dynamo",
        hardware={"nvidia": 46},
        strengths={"nvidia": "Distributed serving across many NVIDIA GPUs"},
        hardware_requirement=_NVIDIA_REQUIREMENT,
        setup={"linux": 4},
        setup_notes={"linux": "Multi-component deployment (frontend, router, workers)"},
        os_requirement=_LINUX_ONLY,
        large_gpu_bytes=16 * GIB,
        detectable=False,
        cautions=("Built for multi-GPU, multi-node serving; heavy for a single machine.",),
        install=_same(
            InstallInfo(
                "pip package or containers on Linux; see the deployment guide.",
                "https://docs.nvidia.com/dynamo/",
            )
        ),
    ),
    EngineRule(
        adapter_id="localforge.mlx_lm",
        name="MLX-LM",
        hardware={"apple": 65},
        strengths={"apple": "Apple's MLX framework, built for Apple silicon unified memory"},
        hardware_requirement="Needs a Mac with Apple silicon",
        setup={"macos": 11},
        setup_notes={"macos": "pip package"},
        os_requirement="Needs a Mac with Apple silicon",
        executables=("mlx_lm.server",),
        python_modules=("mlx_lm",),
        install=_same(
            InstallInfo(
                "pip package (macOS on Apple silicon).",
                "https://github.com/ml-explore/mlx-lm",
                "pip install mlx-lm",
            )
        ),
    ),
)
RULES_BY_ADAPTER: dict[str, EngineRule] = {rule.adapter_id: rule for rule in ENGINE_RULES}


# --- Machine traits ---------------------------------------------------------------------


@dataclass(frozen=True)
class MachineTraits:
    """The facts the rule table needs, derived from a hardware snapshot."""

    os: OsFamily | None
    arch: str | None
    classes: frozenset[HardwareClass]
    avx2: bool | None
    avx512: bool | None
    gpu_detection_ran: bool = True
    nvidia_memory_bytes: int | None = None


def os_family(os_name: str | None) -> OsFamily | None:
    lowered = (os_name or "").casefold()
    if lowered.startswith("win"):
        return "windows"
    if lowered in {"darwin", "macos", "mac os x", "osx"}:
        return "macos"
    if lowered == "linux":
        return "linux"
    return None


def normalized_arch(architecture: str | None) -> str | None:
    lowered = (architecture or "").casefold()
    if lowered in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if lowered in {"arm64", "aarch64", "armv8", "arm64e"}:
        return "arm64"
    return architecture or None


def machine_traits(hardware: HardwareSnapshot | None, features: CpuFeatures) -> MachineTraits:
    hardware = hardware or HardwareSnapshot()
    family = os_family(hardware.os_name)
    arch = normalized_arch(hardware.architecture)
    classes: set[HardwareClass] = {"cpu"}
    if "intel" in (hardware.cpu_model or "").casefold():
        classes.add("intel_cpu")
    nvidia_memory: int | None = None
    for accelerator in hardware.accelerators or []:
        vendor = (accelerator.vendor or "").casefold()
        if vendor == "nvidia":
            classes.add("nvidia")
            if accelerator.memory_bytes is not None:
                nvidia_memory = max(nvidia_memory or 0, accelerator.memory_bytes)
        elif vendor == "intel":
            classes.add("intel_gpu")
        elif vendor == "amd" and accelerator.integrated is False:
            classes.add("amd_gpu")
        elif vendor == "apple":
            classes.add("apple")
    if family == "macos" and arch == "arm64":
        classes.add("apple")
    return MachineTraits(
        os=family,
        arch=arch,
        classes=frozenset(classes),
        avx2=features.avx2,
        avx512=features.avx512,
        gpu_detection_ran=hardware.accelerators is not None,
        nvidia_memory_bytes=nvidia_memory,
    )


# --- Scoring ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assessment:
    """Pure rule-table result for one engine on one machine (status not included)."""

    adapter_id: str
    suitable: bool
    hardware_points: int
    setup_points: int
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]

    @property
    def fit_points(self) -> int:
        return self.hardware_points + self.setup_points


def assess_engine(rule: EngineRule, traits: MachineTraits) -> Assessment:
    """Apply one rule to a machine: hard requirements first, then hardware and setup."""

    cautions: list[str] = []
    unmet: list[str] = []
    usable = {
        hardware_class: points
        for hardware_class, points in rule.hardware.items()
        if hardware_class in traits.classes
        and not (hardware_class == "cpu" and rule.cpu_requires_avx512 and traits.avx512 is not True)
    }
    hardware_points = 0
    best_class: HardwareClass | None = None
    if usable:
        chosen = max(usable, key=lambda key: usable[key])
        best_class, hardware_points = chosen, usable[chosen]
    elif not traits.gpu_detection_ran and any(key in _GPU_CLASSES for key in rule.hardware):
        hardware_points = UNKNOWN_GPU_HARDWARE_POINTS
        cautions.append(f"GPU detection did not complete. {rule.hardware_requirement}.")
    else:
        unmet.append(rule.hardware_requirement)

    setup_points = 0
    if traits.os is None:
        cautions.append("The operating system was not detected; setup ease is unknown.")
    elif traits.os in rule.setup:
        setup_points = rule.setup[traits.os]
        if traits.os in rule.container_on:
            cautions.append(_CONTAINER_CAUTION)
    else:
        unmet.append(rule.os_requirement)

    if unmet:
        unique = tuple(dict.fromkeys(unmet))
        return Assessment(rule.adapter_id, False, 0, 0, unique, tuple(cautions))

    reasons: list[str] = []
    if best_class is not None:
        reasons.append(rule.strengths.get(best_class, ""))
    if best_class in _CPU_CLASSES and rule.uses_cpu_simd and traits.avx2 is False:
        hardware_points -= NO_AVX2_PENALTY
        cautions.append(
            "This CPU lacks AVX2, so CPU inference is markedly slower and some prebuilt "
            "binaries will not start."
        )
    if (
        best_class == "nvidia"
        and rule.large_gpu_bytes is not None
        and traits.nvidia_memory_bytes is not None
        and traits.nvidia_memory_bytes < rule.large_gpu_bytes
    ):
        hardware_points -= SMALL_GPU_PENALTY
        cautions.append(
            f"Built for high-throughput serving on large GPUs; with "
            f"{format_gib(traits.nvidia_memory_bytes)} of GPU memory a GGUF engine such as "
            "llama.cpp usually fits larger models."
        )
    if best_class == "cpu" and rule.cpu_requires_avx512:
        cautions.append("Only the CPU build applies here; it is aimed at servers, not desktops.")
    if traits.os is not None and traits.os in rule.setup_notes:
        reasons.append(rule.setup_notes[traits.os])
    cautions.extend(rule.cautions)
    return Assessment(
        rule.adapter_id,
        True,
        max(hardware_points, 0),
        setup_points,
        tuple(reason for reason in reasons if reason),
        tuple(cautions),
    )


def fit_tier(assessment: Assessment) -> Fit:
    if not assessment.suitable:
        return "unsuitable"
    if assessment.fit_points >= BEST_FIT_POINTS:
        return "best"
    if assessment.fit_points >= GOOD_FIT_POINTS:
        return "good"
    return "possible"


def score_engine(assessment: Assessment, status: EngineStatus, measured_runs: int) -> int:
    """Combine fit points with status and measurement bonuses; unsuitable scores 0."""

    if not assessment.suitable:
        return 0
    total = assessment.fit_points + STATUS_BONUS[status]
    if measured_runs > 0:
        total += MEASURED_BONUS
    return max(0, min(100, total))


# --- Model size guidance ---------------------------------------------------------------


class ModelSizeGuidance(BaseModel):
    basis: Literal["gpu_memory", "system_memory"]
    memory_budget_bytes: int | None
    comfortable_params_billion_q4: float | None
    max_params_billion_q4: float | None
    # Speed, not memory, limits CPU-only machines; estimated from this machine's own runs.
    speed_basis: Literal["measured", "not_measured", "not_applicable"] = "not_applicable"
    measured_read_gib_per_second: float | None = None
    readable_params_billion_q4: float | None = None
    text: str


def format_gib(value: int) -> str:
    return f"{value / GIB:.1f} GB"


def _round_params(value: float) -> float:
    return round(value, 1) if value < 10 else float(round(value))


def _params_text(value: float) -> str:
    return f"{value:g}B"


def model_size_guidance(
    hardware: HardwareSnapshot | None, read_gib_per_second: float | None = None
) -> ModelSizeGuidance:
    """Estimate 4-bit model sizes from GPU memory (discrete GPUs) or system RAM.

    Without a discrete GPU, memory decides what loads but speed decides what is usable, so
    the RAM estimate is paired with a speed estimate from this machine's measured runs.
    """

    hardware = hardware or HardwareSnapshot()
    discrete = [
        accelerator.memory_bytes
        for accelerator in hardware.accelerators or []
        if accelerator.memory_bytes is not None
        and accelerator.integrated is not True
        and accelerator.memory_kind == "dedicated"
        and (accelerator.vendor or "").casefold() in {"nvidia", "amd"}
    ]
    if discrete:
        memory = max(discrete)
        budget = int(memory * GPU_MEMORY_USABLE_FRACTION)
        basis: Literal["gpu_memory", "system_memory"] = "gpu_memory"
        source = f"{format_gib(memory)} of GPU memory"
    elif hardware.memory_total_bytes is not None:
        memory = hardware.memory_total_bytes
        budget = max(0, memory - SYSTEM_MEMORY_HEADROOM_BYTES)
        basis = "system_memory"
        source = f"{format_gib(memory)} of RAM"
    else:
        return ModelSizeGuidance(
            basis="system_memory",
            memory_budget_bytes=None,
            comfortable_params_billion_q4=None,
            max_params_billion_q4=None,
            text="Memory size was not detected, so no model-size estimate is available.",
        )
    budget_gib = budget / GIB
    comfortable = _round_params(
        budget_gib * COMFORTABLE_MEMORY_FRACTION / Q4_GIB_PER_BILLION_PARAMS
    )
    maximum = _round_params(budget_gib / Q4_GIB_PER_BILLION_PARAMS)
    speed_basis: Literal["measured", "not_measured", "not_applicable"] = "not_applicable"
    readable: float | None = None
    if maximum < 1:
        text = f"With {source}, even small 4-bit models will be tight."
    elif basis == "gpu_memory":
        text = (
            f"With {source}, 4-bit models up to about {_params_text(comfortable)} parameters "
            f"fit comfortably; about {_params_text(maximum)} is the practical limit."
        )
    else:
        text = (
            f"With {source}, 4-bit models up to about {_params_text(comfortable)} parameters "
            f"fit in memory (about {_params_text(maximum)} at most)."
        )
    if basis == "system_memory" and maximum >= 1:
        if read_gib_per_second is not None and read_gib_per_second > 0:
            speed_basis = "measured"
            readable = _round_params(
                read_gib_per_second / (READABLE_TOKENS_PER_SECOND * Q4_GIB_PER_BILLION_PARAMS)
            )
            at_7b = read_gib_per_second / (7 * Q4_GIB_PER_BILLION_PARAMS)
            text += (
                " Speed is the real limit without a discrete GPU: your runs streamed about "
                f"{read_gib_per_second:g} GB of model per second, so expect about "
                f"{READABLE_TOKENS_PER_SECOND} tokens/s up to roughly {_params_text(readable)} "
                f"and about {at_7b:.0f} tokens/s at 7B."
            )
        else:
            speed_basis = "not_measured"
            text += (
                " Without a discrete GPU, speed is the real limit: start with 1-3B models and "
                "benchmark them before trying larger ones."
            )
    return ModelSizeGuidance(
        basis=basis,
        memory_budget_bytes=budget,
        comfortable_params_billion_q4=comfortable,
        max_params_billion_q4=maximum,
        speed_basis=speed_basis,
        measured_read_gib_per_second=read_gib_per_second if speed_basis == "measured" else None,
        readable_params_billion_q4=readable,
        text=text,
    )


# --- Machine summary -------------------------------------------------------------------


class CpuFeaturesView(BaseModel):
    avx2: bool | None
    avx512: bool | None


class MachineView(BaseModel):
    summary: str
    os: str
    os_family: OsFamily | None
    arch: str
    cpu_model: str | None
    cpu_logical_cores: int | None
    cpu_physical_cores: int | None
    memory_total_bytes: int | None
    accelerators: list[AcceleratorInfo]
    cpu_features: CpuFeaturesView
    snapshot_source: SnapshotSource
    observed_at: datetime | None


def friendly_os(hardware: HardwareSnapshot) -> str:
    family = os_family(hardware.os_name)
    if family == "windows":
        match = re.match(r"10\.0\.(\d+)", hardware.os_version or "")
        if match:
            return "Windows 11" if int(match.group(1)) >= 22000 else "Windows 10"
        return "Windows"
    if family == "macos":
        return "macOS"
    return hardware.os_name or "Unknown OS"


def friendly_cpu(cpu_model: str | None) -> str | None:
    if not cpu_model:
        return None
    text = re.sub(r"\((r|tm|c)\)", "", cpu_model, flags=re.IGNORECASE)
    text = re.sub(r"\s+@\s*[\d.]+\s*[gm]hz$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(cpu|processor)\b", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()) or None


def _accelerator_label(accelerator: AcceleratorInfo) -> str:
    vendor = accelerator.vendor or ""
    if vendor == "Apple" or accelerator.name.startswith("Apple "):
        return f"{friendly_cpu(accelerator.name) or accelerator.name} GPU (unified memory)"
    if accelerator.integrated is True:
        prefix = vendor or (friendly_cpu(accelerator.name) or accelerator.name)
        return f"{prefix} integrated GPU (shared memory)"
    name = friendly_cpu(accelerator.name) or accelerator.name
    if accelerator.memory_bytes is not None:
        return f"{name} ({format_gib(accelerator.memory_bytes)})"
    return name


def machine_summary(hardware: HardwareSnapshot | None) -> str:
    hardware = hardware or HardwareSnapshot()
    parts = [friendly_os(hardware)]
    cpu = friendly_cpu(hardware.cpu_model)
    if cpu:
        parts.append(cpu)
    if hardware.memory_total_bytes is not None:
        parts.append(f"{format_gib(hardware.memory_total_bytes)} RAM")
    if hardware.accelerators is not None:
        labels = [_accelerator_label(item) for item in hardware.accelerators]
        parts.extend(labels or ["no GPU detected"])
    return " · ".join(parts)


# --- Status detection ------------------------------------------------------------------


@dataclass(frozen=True)
class InstallEvidence:
    status: EngineStatus
    detail: str | None


PathExists = Callable[[str], bool]
Which = Callable[[str], str | None]
ModuleFinder = Callable[[str], bool]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _expand(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def _profile_executables(profiles: Iterable[EngineProfile]) -> dict[str, str]:
    """Map adapter ids to the first saved, still-existing managed executable path."""

    found: dict[str, str] = {}
    for profile in profiles:
        if profile.adapter_id in found:
            continue
        for settings in profile.engine_extensions.values():
            executable = settings.get("executable_path") if isinstance(settings, dict) else None
            if isinstance(executable, str) and executable.strip():
                try:
                    if Path(executable).is_file():
                        found[profile.adapter_id] = executable
                        break
                except OSError:
                    continue
    return found


def detect_installed(
    rules: Iterable[EngineRule],
    os_name: OsFamily | None,
    saved_profiles: Sequence[EngineProfile] = (),
    *,
    which: Which = shutil.which,
    path_exists: PathExists = os.path.exists,
    module_available: ModuleFinder = _module_available,
) -> dict[str, InstallEvidence]:
    """Best-effort, execution-free check for installed engines (PATH, known dirs, packages)."""

    profile_executables = _profile_executables(saved_profiles)
    evidence: dict[str, InstallEvidence] = {}
    for rule in rules:
        found: str | None = None
        if rule.adapter_id in profile_executables:
            found = f"A saved LocalForge profile launches {profile_executables[rule.adapter_id]}"
        for executable in rule.executables:
            if found:
                break
            location = which(executable)
            if location:
                found = f"Found {executable} at {location}"
        for template in rule.install_paths.get(os_name, ()) if os_name else ():
            if found:
                break
            path = _expand(template)
            if path_exists(path):
                found = f"Found {path}"
        for module in rule.python_modules:
            if found:
                break
            if module_available(module):
                found = f"Python package {module} is importable in LocalForge's environment"
        if found:
            evidence[rule.adapter_id] = InstallEvidence("installed", found)
        elif rule.detectable:
            evidence[rule.adapter_id] = InstallEvidence(
                "not_installed", "Not found on PATH or in the usual install locations"
            )
        else:
            evidence[rule.adapter_id] = InstallEvidence(
                "unknown", "LocalForge cannot detect this engine unless it is running"
            )
    return evidence


# --- Measured runs ---------------------------------------------------------------------


class MeasuredView(BaseModel):
    completed_runs: int
    best_generation_tokens_per_second: float | None
    model_ref: str | None
    run_id: UUID | None


def _json(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class LocalRun:
    """A completed run measured on this machine, with its best generation rate if recorded."""

    run_id: str
    adapter_id: str
    model_ref: str | None
    model_size_bytes: int | None
    generation_tokens_per_second: float | None


def local_completed_runs(connection: sqlite3.Connection) -> list[LocalRun]:
    """Completed runs measured on this machine (not remote endpoints or manual machines)."""

    def payloads(entity_type: str) -> list[dict[str, object]]:
        rows = connection.execute(
            "SELECT payload_json FROM domain_records WHERE entity_type = ?", (entity_type,)
        ).fetchall()
        return [_json(row[0]) for row in rows]

    profiles = {str(item.get("id")): item for item in payloads("EngineProfile")}
    plans = {str(item.get("id")): item for item in payloads("BenchmarkPlan")}
    manual_machines = {
        str(item.get("id"))
        for item in payloads("MachineProfile")
        if item.get("status") == MachineProfileStatus.MANUAL_UNVERIFIED.value
    }
    best_rate: dict[str, float] = {}
    for run_id, payload in connection.execute(
        "SELECT run_id, payload_json FROM metric_samples WHERE metric_name = ?",
        ("generation_throughput_tokens_per_second",),
    ).fetchall():
        value = _json(payload).get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            best_rate[str(run_id)] = max(best_rate.get(str(run_id), 0.0), float(value))

    runs: list[LocalRun] = []
    for run in payloads("BenchmarkRun"):
        if run.get("status") != "completed":
            continue
        snapshot = _dict(run.get("profile_snapshot"))
        engine = _dict(snapshot.get("engine"))
        adapter_id = engine.get("adapter_id")
        if not isinstance(adapter_id, str):
            continue
        if str(snapshot.get("machine_profile_id")) in manual_machines:
            continue  # Measured on a manually described (other) machine.
        plan = plans.get(str(run.get("plan_id")), {})
        profile = profiles.get(str(plan.get("engine_profile_id")), {})
        endpoint = profile.get("endpoint_url")
        try:
            if isinstance(endpoint, str) and endpoint_is_remote(endpoint):
                continue  # A remote endpoint measures another machine.
        except ValueError:
            continue
        artifact = _dict(snapshot.get("model_artifact"))
        model = artifact.get("model_ref") or engine.get("selected_model_ref")
        size = artifact.get("size_bytes")
        run_id = str(run.get("id"))
        runs.append(
            LocalRun(
                run_id=run_id,
                adapter_id=adapter_id,
                model_ref=model if isinstance(model, str) else None,
                model_size_bytes=size if isinstance(size, int) and size > 0 else None,
                generation_tokens_per_second=best_rate.get(run_id),
            )
        )
    return runs


def measured_read_gib_per_second(runs: Sequence[LocalRun]) -> float | None:
    """Median model bytes read per second while generating, from runs with a known model size.

    Token generation reads roughly the whole model once per token, so rate x model size
    approximates how fast this machine can stream weights. It extrapolates speed to other sizes.
    """

    samples = sorted(
        (run.model_size_bytes / GIB) * run.generation_tokens_per_second
        for run in runs
        if run.model_size_bytes is not None and run.generation_tokens_per_second is not None
    )
    if not samples:
        return None
    middle = len(samples) // 2
    median = samples[middle] if len(samples) % 2 else (samples[middle - 1] + samples[middle]) / 2
    return round(median, 1)


def measured_runs_by_adapter(
    connection: sqlite3.Connection, runs: Sequence[LocalRun] | None = None
) -> dict[str, MeasuredView]:
    """Summarize completed local runs per adapter with the best generation rate."""

    summaries: dict[str, MeasuredView] = {}
    for local in runs if runs is not None else local_completed_runs(connection):
        adapter_id = local.adapter_id
        model = local.model_ref
        run_id = local.run_id
        rate = local.generation_tokens_per_second
        current = summaries.get(adapter_id)
        count = (current.completed_runs if current else 0) + 1
        current_best = current.best_generation_tokens_per_second if current else None
        if rate is not None and (current_best is None or rate > current_best):
            summaries[adapter_id] = MeasuredView(
                completed_runs=count,
                best_generation_tokens_per_second=round(rate, 2),
                model_ref=model if isinstance(model, str) else None,
                run_id=UUID(run_id),
            )
        elif current is not None:
            summaries[adapter_id] = current.model_copy(update={"completed_runs": count})
        else:
            summaries[adapter_id] = MeasuredView(
                completed_runs=count,
                best_generation_tokens_per_second=None,
                model_ref=None,
                run_id=None,
            )
    return summaries


# --- Assembling the response -----------------------------------------------------------


class InstallView(BaseModel):
    summary: str
    url: str
    command: str | None


class ScoreBreakdown(BaseModel):
    hardware: int
    setup: int
    status: int
    measured: int


class EngineRecommendation(BaseModel):
    rank: int
    adapter_id: str
    display_name: str
    connector_name: str
    fit: Fit
    score: int
    score_breakdown: ScoreBreakdown
    reasons: list[str]
    cautions: list[str]
    status: EngineStatus
    status_detail: str | None
    endpoint_url: str | None
    existing_profile_ids: list[UUID]
    install: InstallView
    measured: MeasuredView | None


class RecommendationResponse(BaseModel):
    generated_at: datetime
    method: str
    machine: MachineView
    model_size_guidance: ModelSizeGuidance
    recommendations: list[EngineRecommendation]


def eligible_manifests(manifests: Iterable[AdapterManifest]) -> list[AdapterManifest]:
    """Implemented, user-facing adapters that the rule table knows about."""

    return [
        manifest
        for manifest in manifests
        if manifest.implementation_status == "implemented"
        and manifest.category is not AdapterCategory.TEST_FIXTURE
        and manifest.adapter_id in RULES_BY_ADAPTER
    ]


def rank_engines(
    manifests: Sequence[AdapterManifest],
    traits: MachineTraits,
    *,
    installed: Mapping[str, InstallEvidence] | None = None,
    running: Sequence[DiscoveryCandidate] = (),
    saved_profiles: Sequence[EngineProfile] = (),
    measured: Mapping[str, MeasuredView] | None = None,
) -> list[EngineRecommendation]:
    """Score every eligible engine and return them ranked (unsuitable last)."""

    installed = installed or {}
    measured = measured or {}
    running_by_adapter: dict[str, list[DiscoveryCandidate]] = {}
    for candidate in running:
        running_by_adapter.setdefault(candidate.adapter_id, []).append(candidate)
    profile_ids: dict[str, list[UUID]] = {}
    for profile in saved_profiles:
        profile_ids.setdefault(profile.adapter_id, []).append(profile.id)

    entries: list[tuple[Assessment, EngineRecommendation]] = []
    for manifest in eligible_manifests(manifests):
        rule = RULES_BY_ADAPTER[manifest.adapter_id]
        assessment = assess_engine(rule, traits)
        candidates = running_by_adapter.get(rule.adapter_id, [])
        status: EngineStatus
        endpoint_url: str | None = None
        ids = list(profile_ids.get(rule.adapter_id, []))
        if candidates:
            first = candidates[0]
            status = "running"
            endpoint_url = first.endpoint_url
            version = f" (version {first.engine_version})" if first.engine_version else ""
            detail = f"Answering at {first.endpoint_url}{version}"
            if first.models:
                count = len(first.models)
                detail += f" with {count} model{'s' if count != 1 else ''}"
            if len(candidates) > 1:
                detail += f"; {len(candidates) - 1} more instance(s) found"
            status_detail: str | None = detail
            # Only profiles pointing at the live endpoint, so the first one can be benchmarked.
            ids = []
            for candidate in candidates:
                ids.extend(item for item in candidate.existing_profile_ids if item not in ids)
        else:
            evidence = installed.get(rule.adapter_id, InstallEvidence("unknown", None))
            status, status_detail = evidence.status, evidence.detail
            local_runs = measured.get(rule.adapter_id)
            if status == "not_installed" and local_runs is not None:
                # Completed runs prove it ran here, even if it lives outside PATH.
                status = "installed"
                status_detail = (
                    f"Not found on PATH or in the usual install locations, but you completed "
                    f"{local_runs.completed_runs} run(s) with it on this machine"
                )

        if not assessment.suitable and status == "running":
            # It demonstrably runs here (for example in WSL2 or a container); keep it usable.
            assessment = Assessment(
                assessment.adapter_id,
                True,
                0,
                0,
                ("Already running on this machine",),
                (
                    f"Running even though the usual requirement is not met "
                    f"({'; '.join(assessment.reasons)}). Measure before relying on it.",
                    *assessment.cautions,
                ),
            )

        runs = measured.get(rule.adapter_id)
        reasons = list(assessment.reasons)
        if runs is not None and runs.best_generation_tokens_per_second is not None:
            model = f" with {runs.model_ref}" if runs.model_ref else ""
            reasons.append(
                f"You measured up to {runs.best_generation_tokens_per_second:g} tokens/s "
                f"generation here{model} across {runs.completed_runs} completed run(s)"
            )
        elif runs is not None:
            reasons.append(f"You have {runs.completed_runs} completed run(s) with it here")
        install = rule.install.get(traits.os or "linux") or next(iter(rule.install.values()))
        score = score_engine(assessment, status, runs.completed_runs if runs else 0)
        entries.append(
            (
                assessment,
                EngineRecommendation(
                    rank=0,
                    adapter_id=rule.adapter_id,
                    display_name=rule.name,
                    connector_name=manifest.display_name,
                    fit=fit_tier(assessment),
                    score=score,
                    score_breakdown=ScoreBreakdown(
                        hardware=assessment.hardware_points,
                        setup=assessment.setup_points,
                        status=STATUS_BONUS[status] if assessment.suitable else 0,
                        measured=MEASURED_BONUS
                        if assessment.suitable and runs and runs.completed_runs
                        else 0,
                    ),
                    reasons=reasons,
                    cautions=list(assessment.cautions),
                    status=status,
                    status_detail=status_detail,
                    endpoint_url=endpoint_url,
                    existing_profile_ids=ids,
                    install=InstallView(
                        summary=install.summary, url=install.url, command=install.command
                    ),
                    measured=runs,
                ),
            )
        )

    entries.sort(
        key=lambda pair: (
            not pair[0].suitable,
            -pair[1].score,
            -pair[0].fit_points,
            pair[1].display_name.casefold(),
        )
    )
    return [
        recommendation.model_copy(update={"rank": index})
        for index, (_assessment, recommendation) in enumerate(entries, start=1)
    ]


def current_machine_profile(profiles: Iterable[MachineProfile]) -> MachineProfile | None:
    """The saved detected snapshot of this machine (as PUT /machine-profiles/current stores)."""

    return next(
        (
            profile
            for profile in profiles
            if profile.label == "This machine"
            and profile.status is not MachineProfileStatus.MANUAL_UNVERIFIED
            and profile.hardware is not None
        ),
        None,
    )


FAST_DISCOVERY_TIMEOUT = httpx2.Timeout(connect=0.5, read=1.5, write=0.5, pool=0.5)
FAST_DISCOVERY_BUDGET_SECONDS = 2.5


async def build_recommendations(
    manifests: Sequence[AdapterManifest],
    *,
    saved_machine: MachineProfile | None = None,
    saved_profiles: Sequence[EngineProfile] = (),
    measured: Mapping[str, MeasuredView] | None = None,
    read_gib_per_second: float | None = None,
    self_ports: Iterable[int] = (),
    discovery_ports: Sequence[int] = DEFAULT_DISCOVERY_PORTS,
    discovery_transport: httpx2.AsyncBaseTransport | None = None,
    discovery_budget_seconds: float = FAST_DISCOVERY_BUDGET_SECONDS,
    machine_inspector: Callable[[], MachineProfile] = inspect_local_machine,
    cpu_features_provider: Callable[[], CpuFeatures] = detect_cpu_features,
    install_detector: Callable[..., dict[str, InstallEvidence]] = detect_installed,
) -> RecommendationResponse:
    """Gather machine facts, running engines and installs concurrently, then rank."""

    eligible = eligible_manifests(manifests)
    implemented = {manifest.adapter_id: manifest.display_name for manifest in manifests}
    saved_endpoints = [
        SavedEndpoint(
            profile_id=profile.id,
            adapter_id=profile.adapter_id,
            endpoint_url=profile.endpoint_url,
        )
        for profile in saved_profiles
    ]

    async def machine_profile() -> tuple[MachineProfile, SnapshotSource]:
        if saved_machine is not None:
            return saved_machine, "saved_profile"
        return await asyncio.to_thread(machine_inspector), "live_detection"

    discovery_task = asyncio.create_task(
        discover_engines(
            implemented_adapters=implemented,
            saved_endpoints=saved_endpoints,
            ports=discovery_ports,
            self_ports=self_ports,
            transport=discovery_transport,
            timeout=FAST_DISCOVERY_TIMEOUT,
            port_budget_seconds=discovery_budget_seconds,
        )
    )
    features_task = asyncio.create_task(asyncio.to_thread(cpu_features_provider))
    (machine, source) = await machine_profile()
    hardware = machine.hardware or HardwareSnapshot()
    family = os_family(hardware.os_name)
    rules = [RULES_BY_ADAPTER[manifest.adapter_id] for manifest in eligible]
    installed = await asyncio.to_thread(install_detector, rules, family, saved_profiles)
    features = await features_task
    discovery = await discovery_task

    traits = machine_traits(hardware, features)
    recommendations = rank_engines(
        eligible,
        traits,
        installed=installed,
        running=discovery.candidates,
        saved_profiles=saved_profiles,
        measured=measured,
    )
    return RecommendationResponse(
        generated_at=utc_now(),
        method=RECOMMENDATION_METHOD,
        machine=MachineView(
            summary=machine_summary(hardware),
            os=friendly_os(hardware),
            os_family=family,
            arch=normalized_arch(hardware.architecture) or platform.machine() or "unknown",
            cpu_model=hardware.cpu_model,
            cpu_logical_cores=hardware.cpu_logical_cores,
            cpu_physical_cores=hardware.cpu_physical_cores,
            memory_total_bytes=hardware.memory_total_bytes,
            accelerators=list(hardware.accelerators or []),
            cpu_features=CpuFeaturesView(avx2=features.avx2, avx512=features.avx512),
            snapshot_source=source,
            observed_at=machine.updated_at if source == "saved_profile" else machine.created_at,
        ),
        model_size_guidance=model_size_guidance(hardware, read_gib_per_second),
        recommendations=recommendations,
    )


@dataclass(frozen=True)
class StoredContext:
    saved_machine: MachineProfile | None
    saved_profiles: list[EngineProfile]
    measured: dict[str, MeasuredView]
    read_gib_per_second: float | None = None


def load_stored_context(connection: sqlite3.Connection) -> StoredContext:
    """Read the saved machine snapshot, engine profiles and measured runs."""

    def records[T: BaseModel](entity_type: str, model: type[T]) -> list[T]:
        rows = connection.execute(
            "SELECT payload_json FROM domain_records WHERE entity_type = ? ORDER BY created_at, id",
            (entity_type,),
        ).fetchall()
        parsed: list[T] = []
        for row in rows:
            try:
                parsed.append(model.model_validate(json.loads(row[0])))
            except ValueError:
                continue
        return parsed

    runs = local_completed_runs(connection)
    return StoredContext(
        saved_machine=current_machine_profile(records("MachineProfile", MachineProfile)),
        saved_profiles=records("EngineProfile", EngineProfile),
        measured=measured_runs_by_adapter(connection, runs),
        read_gib_per_second=measured_read_gib_per_second(runs),
    )


def read_stored_context(database_path: Path) -> StoredContext:
    """Open an existing database read-only; a missing or unreadable one yields no context."""

    empty = StoredContext(None, [], {})
    if not database_path.is_file():
        return empty
    try:
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return empty
    try:
        return load_stored_context(connection)
    except sqlite3.Error:
        return empty
    finally:
        connection.close()


def recommend_for_data_directory(
    data_directory: Path,
    *,
    self_ports: Iterable[int] = (),
    discovery_budget_seconds: float = FAST_DISCOVERY_BUDGET_SECONDS,
) -> RecommendationResponse:
    """Synchronous entry point for the CLI; works without the API server running."""

    from localforge.adapters.catalog import default_adapter_catalog

    context = read_stored_context(data_directory / "localforge.sqlite3")

    async def run() -> RecommendationResponse:
        catalog = default_adapter_catalog()
        try:
            return await build_recommendations(
                catalog.list_manifests(),
                saved_machine=context.saved_machine,
                saved_profiles=context.saved_profiles,
                measured=context.measured,
                read_gib_per_second=context.read_gib_per_second,
                self_ports=self_ports,
                discovery_budget_seconds=discovery_budget_seconds,
            )
        finally:
            await catalog.close()

    return asyncio.run(run())


# --- Text rendering (CLI) --------------------------------------------------------------


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def render_text(response: RecommendationResponse, *, limit: int | None = None) -> list[str]:
    """Plain-text summary: machine, model sizes, ranked engines, then the method note."""

    lines = [
        "Engine recommendations for this machine",
        f"  Machine: {response.machine.summary}",
        f"  Models:  {response.model_size_guidance.text}",
        "",
    ]
    suitable = [item for item in response.recommendations if item.fit != "unsuitable"]
    unsuitable = [item for item in response.recommendations if item.fit == "unsuitable"]
    shown = suitable[:limit] if limit is not None else suitable
    name_width = max([len(item.display_name) for item in shown] + [6])
    name_width = min(name_width, 30)
    lines.append(f"  {'#':>2}  {'Engine':<{name_width}}  {'Fit':<8}  {'Status':<13}  Why")
    for item in shown:
        status = item.status.replace("_", " ")
        reason = item.reasons[0] if item.reasons else ""
        lines.append(
            f"  {item.rank:>2}  {_clip(item.display_name, name_width):<{name_width}}  "
            f"{item.fit:<8}  {status:<13}  {reason}"
        )
        if item.measured and item.measured.best_generation_tokens_per_second is not None:
            lines.append(
                f"  {'':>2}  {'':<{name_width}}  {'':<8}  {'':<13}  measured: up to "
                f"{item.measured.best_generation_tokens_per_second:g} tokens/s generation "
                f"({item.measured.completed_runs} run(s))"
            )
    if limit is None and unsuitable:
        lines.append("")
        lines.append("  Not suitable here:")
        for item in unsuitable:
            reason = "; ".join(item.reasons) or "requirements not met"
            lines.append(f"    {item.display_name}: {reason}")
    elif limit is not None and len(response.recommendations) > len(shown):
        lines.append("  Run `localforge recommend` for the full list.")
    lines.append("")
    lines.append(f"  {response.method}")
    return lines


def best_fit_line(response: RecommendationResponse) -> str | None:
    top = next(
        (item for item in response.recommendations if item.fit != "unsuitable"),
        None,
    )
    if top is None:
        return None
    status = top.status.replace("_", " ")
    return (
        f"Best fit for this machine: {top.display_name} ({status}). "
        "Run `localforge recommend` for the full list."
    )
