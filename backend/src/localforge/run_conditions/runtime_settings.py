"""Engine runtime settings observed at run time, each with the place it was observed.

Sources, in priority order: what the engine's own info routes report, then the launch flags of
the local process listening on the endpoint port, then LOCALFORGE's own managed launch
arguments. A setting no source reported stays ``not_reported``; engine defaults are never
assumed because they differ between engines and releases.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlunsplit

import httpx2
import psutil

from localforge.core.models import JsonValue
from localforge.core.urls import endpoint_is_remote, parse_engine_endpoint

NOT_REPORTED = "not_reported"
RUNTIME_FIELDS: tuple[str, ...] = (
    "threads",
    "threads_batch",
    "gpu_layers",
    "gpu_offload_share",
    "context_size",
    "parallel_slots",
    "batch_size",
    "ubatch_size",
    "flash_attention",
    "kv_cache_type",
    "backend_devices",
    "compute_backends",
)
# Settings that change speed enough that a comparison must not ignore them.
KEY_RUNTIME_FIELDS: tuple[str, ...] = (
    "compute_backends",
    "threads",
    "gpu_layers",
    "context_size",
    "parallel_slots",
)
FIELD_LABELS: dict[str, str] = {
    "threads": "CPU threads",
    "threads_batch": "prompt-processing threads",
    "gpu_layers": "GPU layers",
    "gpu_offload_share": "share of the model on the GPU",
    "context_size": "context size",
    "parallel_slots": "parallel slots",
    "batch_size": "batch size",
    "ubatch_size": "micro-batch size",
    "flash_attention": "flash attention",
    "kv_cache_type": "KV cache type",
    "backend_devices": "backend devices",
    "compute_backends": "compute backend",
}

SOURCE_PROCESS = "process_command_line"
SOURCE_MANAGED = "managed_launch_arguments"
_MAX_INFO_BYTES = 4 * 1024 * 1024
_INFO_TIMEOUT_SECONDS = 3.0

LLAMA_CPP = "localforge.llama_cpp"
KOBOLDCPP = "localforge.koboldcpp"
OLLAMA = "localforge.ollama"
VLLM = "localforge.vllm"
SGLANG = "localforge.sglang"
# Library pipelines and the engine-extension namespace that holds their device setting.
LIBRARY_PIPELINES: dict[str, str] = {
    "localforge.openvino_genai": "openvino_genai",
    "localforge.onnxruntime_genai": "onnxruntime_genai",
}

# Value-taking launch flags. Only these are ever read from a command line, so arguments such as
# API keys or passwords are never captured.
_LLAMA_FLAGS: dict[str, str] = {
    "-t": "threads",
    "--threads": "threads",
    "-tb": "threads_batch",
    "--threads-batch": "threads_batch",
    "-ngl": "gpu_layers",
    "--gpu-layers": "gpu_layers",
    "--n-gpu-layers": "gpu_layers",
    "-c": "context_size",
    "--ctx-size": "context_size",
    "-np": "parallel_slots",
    "--parallel": "parallel_slots",
    "-b": "batch_size",
    "--batch-size": "batch_size",
    "-ub": "ubatch_size",
    "--ubatch-size": "ubatch_size",
    "-fa": "flash_attention",
    "--flash-attn": "flash_attention",
    "-ctk": "kv_cache_type_k",
    "--cache-type-k": "kv_cache_type_k",
    "-ctv": "kv_cache_type_v",
    "--cache-type-v": "kv_cache_type_v",
    "-dev": "backend_devices",
    "--device": "backend_devices",
    "-m": "model_path",
    "--model": "model_path",
}
_LLAMA_SWITCHES: dict[str, tuple[str, JsonValue]] = {
    "--no-flash-attn": ("flash_attention", False),
}
_KOBOLD_FLAGS: dict[str, str] = {
    "-t": "threads",
    "--threads": "threads",
    "--blasthreads": "threads_batch",
    "--batchthreads": "threads_batch",
    "--threadsbatch": "threads_batch",
    "--threads-batch": "threads_batch",
    "-ngl": "gpu_layers",
    "--gpulayers": "gpu_layers",
    "--gpu-layers": "gpu_layers",
    "--n-gpu-layers": "gpu_layers",
    "-c": "context_size",
    "--contextsize": "context_size",
    "--ctx-size": "context_size",
    "--parallelrequests": "parallel_slots",
    "--continuous-batching": "parallel_slots",
    "--contbatch": "parallel_slots",
    "-b": "batch_size",
    "--blasbatchsize": "batch_size",
    "--batchsize": "batch_size",
    "--batch-size": "batch_size",
    "-ub": "ubatch_size",
    "--ubatchsize": "ubatch_size",
    "--ubatch-size": "ubatch_size",
    "--quantkv": "kv_cache_type",
    "-m": "model_path",
    "--model": "model_path",
}
_KOBOLD_SWITCHES: dict[str, tuple[str, JsonValue]] = {
    "--flashattention": ("flash_attention", True),
    "--flash-attn": ("flash_attention", True),
    "-fa": ("flash_attention", True),
    "--noflashattention": ("flash_attention", False),
    "--usecpu": ("backend_devices", ["cpu"]),
    "--usecuda": ("backend_devices", ["cuda"]),
    "--usecublas": ("backend_devices", ["cuda"]),
    "--usehipblas": ("backend_devices", ["hip"]),
    "--usevulkan": ("backend_devices", ["vulkan"]),
    "--useclblast": ("backend_devices", ["clblast"]),
}
_INTEGER_FIELDS = {
    "threads",
    "threads_batch",
    "context_size",
    "parallel_slots",
    "batch_size",
    "ubatch_size",
}

ProcessFinder = Callable[[int], tuple[list[str], str] | None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _positive(value: object) -> int | None:
    number = _integer(value)
    return number if number is not None and number > 0 else None


class RuntimeSettingsBuilder:
    """Collect observations; the first source to report a field wins."""

    def __init__(self) -> None:
        self._fields: dict[str, dict[str, JsonValue]] = {}
        self.sources: list[str] = []
        self.notes: list[str] = []
        self.extra: dict[str, JsonValue] = {}

    def observe(self, field: str, value: JsonValue, source: str) -> None:
        if value is None or field in self._fields:
            return
        self._fields[field] = {"value": value, "source": source}
        if source not in self.sources:
            self.sources.append(source)

    def has(self, field: str) -> bool:
        return field in self._fields

    def build(self, adapter_id: str, *, stage: str = "run_start") -> dict[str, JsonValue]:
        fields: dict[str, JsonValue] = {}
        for name in RUNTIME_FIELDS:
            fields[name] = cast(
                JsonValue,
                self._fields.get(name, {"value": None, "source": None, "status": NOT_REPORTED}),
            )
        return {
            "schema_version": 1,
            "adapter_id": adapter_id,
            "captured_at": _now(),
            "stage": stage,
            "fields": fields,
            "sources": cast(JsonValue, list(self.sources)),
            "notes": cast(JsonValue, list(self.notes)),
            **self.extra,
        }


def parse_launch_flags(
    argv: Sequence[str],
    flags: Mapping[str, str],
    switches: Mapping[str, tuple[str, JsonValue]],
) -> dict[str, JsonValue]:
    """Read only allow-listed launch flags; later occurrences win, as in a CLI parser."""

    found: dict[str, JsonValue] = {}
    index = 1
    while index < len(argv):
        token = argv[index]
        index += 1
        if not token.startswith("-"):
            continue
        name, separator, inline = token.partition("=")
        if name in switches:
            field, value = switches[name]
            found[field] = value
            continue
        if name not in flags:
            continue
        field = flags[name]
        if separator:
            found[field] = inline
            continue
        following = argv[index] if index < len(argv) else None
        if following is not None and (
            not following.startswith("-") or _integer(following) is not None
        ):
            found[field] = following
            index += 1
        else:
            found[field] = True
    return found


def _normalize_flash(value: JsonValue) -> JsonValue:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"on", "1", "true", "enabled", "yes"}:
        return True
    if text in {"off", "0", "false", "disabled", "no"}:
        return False
    return text or None


def apply_launch_flags(
    builder: RuntimeSettingsBuilder, flags: Mapping[str, JsonValue], source: str
) -> str | None:
    """Record parsed flags and return the model path argument, when one was given."""

    for field in _INTEGER_FIELDS:
        if field in flags:
            builder.observe(field, _integer(flags[field]), source)
    if "gpu_layers" in flags:
        raw = flags["gpu_layers"]
        number = _integer(raw)
        builder.observe("gpu_layers", number if number is not None else str(raw), source)
    if "flash_attention" in flags:
        builder.observe("flash_attention", _normalize_flash(flags["flash_attention"]), source)
    key_type = flags.get("kv_cache_type_k")
    value_type = flags.get("kv_cache_type_v")
    if "kv_cache_type" in flags:
        builder.observe("kv_cache_type", str(flags["kv_cache_type"]), source)
    elif key_type is not None or value_type is not None:
        if key_type == value_type:
            builder.observe("kv_cache_type", str(key_type), source)
        else:
            builder.observe(
                "kv_cache_type",
                f"K={key_type if key_type is not None else 'not set'}, "
                f"V={value_type if value_type is not None else 'not set'}",
                source,
            )
    devices = flags.get("backend_devices")
    if isinstance(devices, list):
        builder.observe("backend_devices", devices, source)
    elif isinstance(devices, str):
        builder.observe(
            "backend_devices",
            cast(JsonValue, [item.strip() for item in devices.split(",") if item.strip()]),
            source,
        )
    model = flags.get("model_path")
    return model if isinstance(model, str) and model.strip() else None


# File-name fragments that mean an engine process has loaded a compute backend. Command-line
# flags cannot say: a CPU build and a Vulkan build of one llama.cpp release are started alike.
BACKEND_LIBRARY_MARKERS: tuple[tuple[str, str], ...] = (
    ("ggml-vulkan", "Vulkan"),
    ("koboldcpp_vulkan", "Vulkan"),
    ("vulkan-1", "Vulkan"),
    ("libvulkan", "Vulkan"),
    ("ggml-cuda", "CUDA"),
    ("koboldcpp_cublas", "CUDA"),
    ("koboldcpp_cuda", "CUDA"),
    ("nvcuda", "CUDA"),
    ("libcuda", "CUDA"),
    ("cublas", "CUDA"),
    ("ggml-metal", "Metal"),
    ("metal.framework", "Metal"),
    ("ggml-sycl", "SYCL"),
    ("libsycl", "SYCL"),
    ("ggml-hip", "ROCm"),
    ("amdhip64", "ROCm"),
    ("ggml-opencl", "OpenCL"),
    ("koboldcpp_clblast", "OpenCL"),
)


def compute_backends_from_libraries(paths: Iterable[str]) -> list[str]:
    """Name the compute backends implied by loaded library paths; CPU when none is loaded."""

    lowered = [path.casefold() for path in paths]
    found: list[str] = []
    for marker, label in BACKEND_LIBRARY_MARKERS:
        if label not in found and any(marker in path for path in lowered):
            found.append(label)
    return found or ["CPU"]


def find_loaded_backends(port: int) -> list[str] | None:
    """Compute backends loaded by the local process listening on a port, or None if unknown."""

    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return None
    for connection in connections:
        address = connection.laddr
        if (
            connection.status != psutil.CONN_LISTEN
            or not address
            or getattr(address, "port", None) != port
            or not connection.pid
        ):
            continue
        try:
            maps = psutil.Process(connection.pid).memory_maps(grouped=True)
        except (psutil.Error, OSError, NotImplementedError):
            return None
        return compute_backends_from_libraries(str(getattr(item, "path", "")) for item in maps)
    return None


def find_listening_process(port: int) -> tuple[list[str], str] | None:
    """Return the command line and executable name of the local process listening on a port."""

    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return None
    for connection in connections:
        address = connection.laddr
        if (
            connection.status != psutil.CONN_LISTEN
            or not address
            or getattr(address, "port", None) != port
            or not connection.pid
        ):
            continue
        try:
            process = psutil.Process(connection.pid)
            return list(process.cmdline()), process.name()
        except (psutil.Error, OSError):
            return None
    return None


def _root_url(endpoint_url: str, route: str) -> str:
    parsed = parse_engine_endpoint(endpoint_url)
    prefix = parsed.path.rstrip("/")
    if prefix.endswith("/v1"):
        prefix = prefix[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, f"{prefix}/{route.lstrip('/')}", "", ""))


async def fetch_json(
    client: httpx2.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    body: dict[str, JsonValue] | None = None,
) -> object | None:
    """Read a small JSON info route; any failure means the route reported nothing."""

    try:
        response = await client.request(method, url, json=body, timeout=_INFO_TIMEOUT_SECONDS)
    except (httpx2.HTTPError, OSError, ValueError):
        return None
    if response.status_code != 200 or len(response.content) > _MAX_INFO_BYTES:
        return None
    try:
        return cast(object, response.json())
    except ValueError:
        return None


def _parse_ollama_parameters(text: object) -> dict[str, str]:
    values: dict[str, str] = {}
    if not isinstance(text, str):
        return values
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            values.setdefault(parts[0].strip(), parts[1].strip().strip('"'))
    return values


async def _llama_cpp(
    builder: RuntimeSettingsBuilder,
    client: httpx2.AsyncClient,
    endpoint_url: str,
    observed: dict[str, JsonValue],
) -> None:
    props = await fetch_json(client, _root_url(endpoint_url, "/props"))
    if isinstance(props, dict):
        settings = props.get("default_generation_settings")
        if isinstance(settings, dict):
            builder.observe("context_size", _positive(settings.get("n_ctx")), "llama_cpp_props")
        builder.observe("parallel_slots", _positive(props.get("total_slots")), "llama_cpp_props")
        model_path = props.get("model_path")
        if isinstance(model_path, str) and model_path.strip():
            observed["model_path"] = model_path.strip()
        ftype = props.get("model_ftype")
        if isinstance(ftype, str) and ftype.strip():
            observed["model_quantization"] = ftype.strip()[:80]
    slots = await fetch_json(client, _root_url(endpoint_url, "/slots"))
    if isinstance(slots, list) and slots:
        builder.observe("parallel_slots", len(slots), "llama_cpp_slots")
        first = slots[0]
        if isinstance(first, dict):
            builder.observe("context_size", _positive(first.get("n_ctx")), "llama_cpp_slots")


async def _koboldcpp(
    builder: RuntimeSettingsBuilder,
    client: httpx2.AsyncClient,
    endpoint_url: str,
    observed: dict[str, JsonValue],
) -> None:
    version = await fetch_json(client, _root_url(endpoint_url, "/api/extra/version"))
    if isinstance(version, dict):
        builder.observe(
            "parallel_slots", _positive(version.get("parallelrequests")), "koboldcpp_version"
        )
    props = await fetch_json(client, _root_url(endpoint_url, "/props"))
    if isinstance(props, dict):
        builder.observe("parallel_slots", _positive(props.get("total_slots")), "koboldcpp_props")
        builder.observe("context_size", _positive(props.get("n_ctx")), "koboldcpp_props")
    context = await fetch_json(
        client, _root_url(endpoint_url, "/api/extra/true_max_context_length")
    )
    if isinstance(context, dict):
        builder.observe(
            "context_size", _positive(context.get("value")), "koboldcpp_true_max_context_length"
        )
    perf = await fetch_json(client, _root_url(endpoint_url, "/api/extra/perf"))
    if isinstance(perf, dict):
        # Only liveness and queue facts are kept; perf has no thread or offload settings.
        observed["koboldcpp_perf_reachable"] = True


async def _ollama(
    builder: RuntimeSettingsBuilder,
    client: httpx2.AsyncClient,
    endpoint_url: str,
    model_ref: str,
    observed: dict[str, JsonValue],
) -> None:
    show = await fetch_json(
        client,
        _root_url(endpoint_url, "/api/show"),
        method="POST",
        body={"model": model_ref},
    )
    if isinstance(show, dict):
        observed["ollama_show"] = cast(JsonValue, show)
        parameters = _parse_ollama_parameters(show.get("parameters"))
        builder.observe("context_size", _positive(parameters.get("num_ctx")), "ollama_api_show")
        builder.observe("threads", _positive(parameters.get("num_thread")), "ollama_api_show")
        builder.observe("batch_size", _positive(parameters.get("num_batch")), "ollama_api_show")
        num_gpu = _integer(parameters.get("num_gpu"))
        builder.observe("gpu_layers", num_gpu, "ollama_api_show")
    running = await fetch_json(client, _root_url(endpoint_url, "/api/ps"))
    if isinstance(running, dict) and isinstance(running.get("models"), list):
        for item in cast(list[object], running["models"]):
            if not isinstance(item, dict):
                continue
            if model_ref not in {item.get("name"), item.get("model")}:
                continue
            size = _integer(item.get("size"))
            size_vram = _integer(item.get("size_vram"))
            if size is not None and size > 0 and size_vram is not None and size_vram >= 0:
                builder.observe(
                    "gpu_offload_share", round(min(1.0, size_vram / size), 4), "ollama_api_ps"
                )
                builder.extra["ollama_loaded_size_bytes"] = size
                builder.extra["ollama_loaded_vram_bytes"] = size_vram
            builder.observe("context_size", _positive(item.get("context_length")), "ollama_api_ps")
            break
        else:
            builder.notes.append(
                "Ollama had not loaded this model when settings were read; "
                "GPU offload is read again after the run."
            )


async def _vllm(
    builder: RuntimeSettingsBuilder, client: httpx2.AsyncClient, endpoint_url: str
) -> None:
    models = await fetch_json(client, _root_url(endpoint_url, "/v1/models"))
    if isinstance(models, dict) and isinstance(models.get("data"), list):
        for item in cast(list[object], models["data"]):
            if isinstance(item, dict):
                builder.observe(
                    "context_size", _positive(item.get("max_model_len")), "vllm_v1_models"
                )
                break


async def _sglang(
    builder: RuntimeSettingsBuilder, client: httpx2.AsyncClient, endpoint_url: str
) -> None:
    info = await fetch_json(client, _root_url(endpoint_url, "/get_server_info"))
    if not isinstance(info, dict):
        return
    source = "sglang_get_server_info"
    builder.observe("context_size", _positive(info.get("context_length")), source)
    builder.observe("parallel_slots", _positive(info.get("max_running_requests")), source)
    kv_dtype = info.get("kv_cache_dtype")
    if isinstance(kv_dtype, str) and kv_dtype.strip():
        builder.observe("kv_cache_type", kv_dtype.strip(), source)
    device = info.get("device")
    if isinstance(device, str) and device.strip():
        builder.observe("backend_devices", [device.strip()], source)


def _managed_llama_arguments(
    builder: RuntimeSettingsBuilder, extension: Mapping[str, JsonValue]
) -> str | None:
    mapping = {
        "context_size": "context_size",
        "parallelism": "parallel_slots",
        "batch_size": "batch_size",
        "ubatch_size": "ubatch_size",
    }
    for key, field in mapping.items():
        builder.observe(field, _positive(extension.get(key)), SOURCE_MANAGED)
    gpu_layers = extension.get("gpu_layers")
    if isinstance(gpu_layers, (int, str)) and not isinstance(gpu_layers, bool):
        number = _integer(gpu_layers)
        builder.observe("gpu_layers", number if number is not None else gpu_layers, SOURCE_MANAGED)
    model_path = extension.get("model_path")
    return model_path if isinstance(model_path, str) and model_path.strip() else None


async def capture_runtime_settings(
    *,
    adapter_id: str,
    integration_mode: str,
    endpoint_url: str | None,
    engine_extensions: Mapping[str, Mapping[str, JsonValue]],
    model_ref: str,
    client: httpx2.AsyncClient,
    process_finder: ProcessFinder = find_listening_process,
    backend_finder: Callable[[int], list[str] | None] = find_loaded_backends,
    stage: str = "run_start",
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Return (runtime settings snapshot, raw observations used for model identity).

    Raw observations carry local model paths and Ollama's show payload for the identity step;
    they are not stored in the run record as-is.
    """

    builder = RuntimeSettingsBuilder()
    observed: dict[str, JsonValue] = {}
    if endpoint_url:
        try:
            parse_engine_endpoint(endpoint_url)
        except ValueError:
            endpoint_url = None
    if endpoint_url:
        if adapter_id == LLAMA_CPP:
            await _llama_cpp(builder, client, endpoint_url, observed)
        elif adapter_id == KOBOLDCPP:
            await _koboldcpp(builder, client, endpoint_url, observed)
        elif adapter_id == OLLAMA:
            await _ollama(builder, client, endpoint_url, model_ref, observed)
        elif adapter_id == VLLM:
            await _vllm(builder, client, endpoint_url)
        elif adapter_id == SGLANG:
            await _sglang(builder, client, endpoint_url)

    process_model: str | None = None
    if (
        endpoint_url
        and adapter_id in {LLAMA_CPP, KOBOLDCPP}
        and not endpoint_is_remote(endpoint_url)
    ):
        port = parse_engine_endpoint(endpoint_url).port
        found = process_finder(port) if port is not None else None
        if found is not None:
            argv, executable = found
            flags, switches = (
                (_LLAMA_FLAGS, _LLAMA_SWITCHES)
                if adapter_id == LLAMA_CPP
                else (_KOBOLD_FLAGS, _KOBOLD_SWITCHES)
            )
            process_model = apply_launch_flags(
                builder, parse_launch_flags(argv, flags, switches), SOURCE_PROCESS
            )
            backends = backend_finder(port) if port is not None else None
            if backends is not None:
                builder.observe(
                    "compute_backends", cast(JsonValue, backends), "process_loaded_libraries"
                )
            builder.extra["process"] = {"executable": Path(executable).name[:120]}
            if SOURCE_PROCESS not in builder.sources:
                builder.sources.append(SOURCE_PROCESS)
            builder.notes.append(
                "Settings not given on the engine's command line use the engine's own "
                "defaults, which LOCALFORGE does not assume."
            )
        else:
            builder.notes.append(
                "The local engine process could not be inspected, so launch flags are unknown."
            )

    if adapter_id in LIBRARY_PIPELINES:
        # A library pipeline runs on the device named in its profile, which is the setting.
        namespace = engine_extensions.get(LIBRARY_PIPELINES[adapter_id], {})
        device = namespace.get("device") if isinstance(namespace, Mapping) else None
        if isinstance(device, str) and device.strip():
            builder.observe("compute_backends", [device.strip().upper()], "engine_profile_device")

    managed_model: str | None = None
    if adapter_id == LLAMA_CPP and integration_mode == "managed_process":
        managed_model = _managed_llama_arguments(builder, engine_extensions.get("llama_cpp", {}))

    if process_model is not None:
        observed["process_model_path"] = process_model
    if managed_model is not None:
        observed["managed_model_path"] = managed_model
    return builder.build(adapter_id, stage=stage), observed


def merge_runtime_settings(
    start: dict[str, JsonValue], end: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Fill fields the start capture could not observe with end-of-run observations."""

    start_fields = start.get("fields")
    end_fields = end.get("fields")
    if not isinstance(start_fields, dict) or not isinstance(end_fields, dict):
        return start
    merged = dict(start_fields)
    filled: list[JsonValue] = []
    for name, entry in end_fields.items():
        current = merged.get(name)
        if (
            isinstance(entry, dict)
            and entry.get("value") is not None
            and (not isinstance(current, dict) or current.get("value") is None)
        ):
            merged[name] = {**entry, "observed": "after_run"}
            filled.append(name)
    result = dict(start)
    result["fields"] = merged
    if filled:
        result["filled_after_run"] = filled
        sources = start.get("sources")
        end_sources = end.get("sources")
        if isinstance(sources, list) and isinstance(end_sources, list):
            result["sources"] = list(dict.fromkeys([*sources, *end_sources]))
    return result


def runtime_field_value(snapshot: object, field: str) -> JsonValue:
    if not isinstance(snapshot, dict):
        return None
    fields = snapshot.get("fields")
    if not isinstance(fields, dict):
        return None
    entry = fields.get(field)
    return entry.get("value") if isinstance(entry, dict) else None
