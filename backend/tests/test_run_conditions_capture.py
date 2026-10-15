"""Runtime settings capture per engine, from mocked info routes and process command lines."""

import asyncio
import json
from typing import Any

import httpx2

from localforge.run_conditions.runtime_settings import (
    KOBOLDCPP,
    LLAMA_CPP,
    OLLAMA,
    SGLANG,
    VLLM,
    capture_runtime_settings,
    compute_backends_from_libraries,
    merge_runtime_settings,
    parse_launch_flags,
    runtime_field_value,
)

# Shapes copied from live llama-server b11609, KoboldCpp 1.123, and Ollama 0.40.3 responses.
LLAMA_PROPS = {
    "default_generation_settings": {"params": {"temperature": 0.8}, "n_ctx": 4096},
    "total_slots": 4,
    "model_path": "D:\\models\\qwen2.5-coder-1.5b-q4_k_m.gguf",
    "model_ftype": "Q4_K - Medium",
    "build_info": "b11609-fcac19079",
}
LLAMA_SLOTS = [{"id": index, "n_ctx": 4096, "is_processing": False} for index in range(4)]
KOBOLD_VERSION = {"result": "KoboldCpp", "version": "1.123", "parallelrequests": 1}
KOBOLD_PROPS = {
    "total_slots": 1,
    "model_path": "koboldcpp/qwen2.5-coder-1.5b-q4_k_m",
    "n_ctx": 4096,
    "default_generation_settings": {"n_ctx": 4096},
}
OLLAMA_SHOW = {
    "modelfile": "# Modelfile\nFROM C:\\Users\\x\\.ollama\\models\\blobs\\sha256-"
    "29d8c98fa6b098e200069bfb88b9508dc3e85586d20cba59f8dda9a808165104\nTEMPLATE x",
    "parameters": 'num_ctx 8192\nstop "<|im_end|>"',
    "details": {"format": "gguf", "parameter_size": "1.5B", "quantization_level": "Q4_K_M"},
}
OLLAMA_PS = {
    "models": [
        {
            "name": "qwen2.5-coder:1.5b",
            "model": "qwen2.5-coder:1.5b",
            "size": 2000,
            "size_vram": 500,
            "context_length": 4096,
        }
    ]
}


def _transport(routes: dict[str, object], seen: list[str] | None = None) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(f"{request.method} {request.url.path}")
        body = routes.get(f"{request.method} {request.url.path}")
        if body is None:
            return httpx2.Response(404, json={"error": "not found"})
        return httpx2.Response(200, content=json.dumps(body).encode())

    return httpx2.MockTransport(handler)


def _capture(
    adapter_id: str,
    routes: dict[str, object],
    *,
    endpoint: str = "http://127.0.0.1:18080",
    argv: list[str] | None = None,
    integration_mode: str = "attached_endpoint",
    extensions: dict[str, dict[str, Any]] | None = None,
    model_ref: str = "model",
    seen: list[str] | None = None,
    backends: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ports: list[int] = []

    def finder(port: int) -> tuple[list[str], str] | None:
        ports.append(port)
        return (argv, "engine.exe") if argv is not None else None

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        async with httpx2.AsyncClient(transport=_transport(routes, seen)) as client:
            return await capture_runtime_settings(
                adapter_id=adapter_id,
                integration_mode=integration_mode,
                endpoint_url=endpoint,
                engine_extensions=extensions or {},
                model_ref=model_ref,
                client=client,
                process_finder=finder,
                backend_finder=lambda port: backends,
            )

    return asyncio.run(run())


def test_compute_backends_come_from_loaded_libraries() -> None:
    assert compute_backends_from_libraries(
        [r"C:\engine\ggml-cpu-alderlake.dll", r"C:\engine\ggml-vulkan.dll"]
    ) == ["Vulkan"]
    assert compute_backends_from_libraries(["/usr/lib/libggml-cuda.so", "libcuda.so.1"]) == ["CUDA"]
    assert compute_backends_from_libraries([r"C:\engine\ggml-cpu.dll"]) == ["CPU"]


def test_llama_cpp_records_compute_backend_from_process() -> None:
    snapshot, _ = _capture(
        LLAMA_CPP,
        {"GET /props": LLAMA_PROPS, "GET /slots": LLAMA_SLOTS},
        argv=["llama-server.exe", "-ngl", "99"],
        backends=["Vulkan"],
    )
    assert _field(snapshot, "compute_backends") == {
        "value": ["Vulkan"],
        "source": "process_loaded_libraries",
    }


def test_openvino_records_device_as_compute_backend() -> None:
    snapshot, _ = _capture(
        "localforge.openvino_genai",
        {},
        integration_mode="library_pipeline",
        extensions={"openvino_genai": {"device": "gpu"}},
    )
    assert _field(snapshot, "compute_backends") == {
        "value": ["GPU"],
        "source": "engine_profile_device",
    }


def _field(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    return snapshot["fields"][name]


def test_llama_cpp_reads_props_slots_and_process_flags() -> None:
    snapshot, observed = _capture(
        LLAMA_CPP,
        {"GET /props": LLAMA_PROPS, "GET /slots": LLAMA_SLOTS},
        endpoint="http://127.0.0.1:18080/v1",
        argv=[
            "llama-server.exe",
            "--model",
            "D:\\models\\qwen2.5-coder-1.5b-q4_k_m.gguf",
            "--threads",
            "12",
            "-ngl",
            "0",
            "-fa",
            "off",
            "-ctk",
            "q8_0",
            "-ctv",
            "q8_0",
            "--api-key",
            "never-captured",
        ],
    )

    assert _field(snapshot, "context_size") == {"value": 4096, "source": "llama_cpp_props"}
    assert _field(snapshot, "parallel_slots") == {"value": 4, "source": "llama_cpp_props"}
    assert _field(snapshot, "threads") == {"value": 12, "source": "process_command_line"}
    assert _field(snapshot, "gpu_layers")["value"] == 0
    assert _field(snapshot, "flash_attention")["value"] is False
    assert _field(snapshot, "kv_cache_type")["value"] == "q8_0"
    # Not given anywhere: recorded as not reported, never assumed.
    assert _field(snapshot, "batch_size") == {
        "value": None,
        "source": None,
        "status": "not_reported",
    }
    assert observed["model_path"] == LLAMA_PROPS["model_path"]
    assert "never-captured" not in json.dumps(snapshot)


def test_koboldcpp_reads_version_props_context_and_process_flags() -> None:
    seen: list[str] = []
    snapshot, observed = _capture(
        KOBOLDCPP,
        {
            "GET /api/extra/version": KOBOLD_VERSION,
            "GET /props": KOBOLD_PROPS,
            "GET /api/extra/true_max_context_length": {"value": 4096},
            "GET /api/extra/perf": {"last_eval_speed": 0, "idle": 1},
        },
        endpoint="http://127.0.0.1:18081",
        argv=[
            "koboldcpp-nocuda.exe",
            "--model",
            "D:\\models\\qwen2.5-coder-1.5b-q4_k_m.gguf",
            "--threads",
            "6",
            "--contextsize",
            "4096",
            "--usecpu",
            "--quantkv",
            "f16",
            "--password",
            "never-captured",
        ],
        seen=seen,
    )

    assert _field(snapshot, "threads") == {"value": 6, "source": "process_command_line"}
    assert _field(snapshot, "parallel_slots") == {"value": 1, "source": "koboldcpp_version"}
    assert _field(snapshot, "context_size") == {"value": 4096, "source": "koboldcpp_props"}
    assert _field(snapshot, "backend_devices")["value"] == ["cpu"]
    assert _field(snapshot, "kv_cache_type")["value"] == "f16"
    assert _field(snapshot, "gpu_layers")["status"] == "not_reported"
    assert observed["process_model_path"].endswith("qwen2.5-coder-1.5b-q4_k_m.gguf")
    assert "GET /api/extra/perf" in seen
    assert "never-captured" not in json.dumps(snapshot)


def test_ollama_reads_show_and_ps_for_context_and_gpu_share() -> None:
    snapshot, observed = _capture(
        OLLAMA,
        {"POST /api/show": OLLAMA_SHOW, "GET /api/ps": OLLAMA_PS},
        endpoint="http://127.0.0.1:11434",
        model_ref="qwen2.5-coder:1.5b",
    )
    assert _field(snapshot, "context_size") == {"value": 8192, "source": "ollama_api_show"}
    assert _field(snapshot, "gpu_offload_share") == {"value": 0.25, "source": "ollama_api_ps"}
    assert snapshot["ollama_loaded_vram_bytes"] == 500
    assert observed["ollama_show"] == OLLAMA_SHOW


def test_ollama_model_not_loaded_is_filled_after_the_run() -> None:
    start, _ = _capture(
        OLLAMA,
        {"POST /api/show": {"details": {}}, "GET /api/ps": {"models": []}},
        model_ref="qwen2.5-coder:1.5b",
    )
    assert _field(start, "gpu_offload_share")["status"] == "not_reported"
    assert any("not loaded" in note for note in start["notes"])
    end, _ = _capture(
        OLLAMA,
        {"POST /api/show": {"details": {}}, "GET /api/ps": OLLAMA_PS},
        model_ref="qwen2.5-coder:1.5b",
    )
    merged = merge_runtime_settings(start, end)
    assert runtime_field_value(merged, "gpu_offload_share") == 0.25
    assert merged["fields"]["gpu_offload_share"]["observed"] == "after_run"
    assert merged["filled_after_run"] == ["gpu_offload_share", "context_size"]


def test_vllm_and_sglang_info_routes() -> None:
    vllm, _ = _capture(VLLM, {"GET /v1/models": {"data": [{"id": "m", "max_model_len": 32768}]}})
    assert _field(vllm, "context_size") == {"value": 32768, "source": "vllm_v1_models"}
    assert _field(vllm, "threads")["status"] == "not_reported"
    sglang, _ = _capture(
        SGLANG,
        {
            "GET /get_server_info": {
                "context_length": 8192,
                "max_running_requests": 16,
                "kv_cache_dtype": "fp8_e5m2",
                "device": "cuda",
            }
        },
    )
    assert runtime_field_value(sglang, "parallel_slots") == 16
    assert runtime_field_value(sglang, "kv_cache_type") == "fp8_e5m2"
    assert runtime_field_value(sglang, "backend_devices") == ["cuda"]


def test_managed_llama_cpp_uses_its_launch_arguments() -> None:
    snapshot, observed = _capture(
        LLAMA_CPP,
        {},
        argv=None,
        integration_mode="managed_process",
        extensions={
            "llama_cpp": {
                "model_path": "D:\\models\\m.gguf",
                "context_size": 2048,
                "parallelism": 2,
                "gpu_layers": 0,
                "batch_size": 512,
            }
        },
    )
    assert _field(snapshot, "context_size") == {
        "value": 2048,
        "source": "managed_launch_arguments",
    }
    assert runtime_field_value(snapshot, "parallel_slots") == 2
    assert runtime_field_value(snapshot, "gpu_layers") == 0
    assert observed["managed_model_path"] == "D:\\models\\m.gguf"
    assert any("could not be inspected" in note for note in snapshot["notes"])


def test_remote_endpoints_are_not_matched_to_local_processes() -> None:
    snapshot, _ = _capture(
        LLAMA_CPP,
        {"GET /props": LLAMA_PROPS},
        endpoint="https://gpu-box.example:8443",
        argv=["llama-server", "--threads", "64"],
    )
    assert runtime_field_value(snapshot, "threads") is None
    assert runtime_field_value(snapshot, "context_size") == 4096


def test_unreachable_engine_reports_nothing() -> None:
    snapshot, observed = _capture(LLAMA_CPP, {}, argv=None)
    assert all(entry["value"] is None for entry in snapshot["fields"].values())
    assert observed == {}


def test_launch_flag_parsing_handles_inline_values_switches_and_negatives() -> None:
    flags = parse_launch_flags(
        ["koboldcpp", "--threads=8", "--gpulayers", "-1", "--flashattention", "model.gguf"],
        {"--threads": "threads", "--gpulayers": "gpu_layers"},
        {"--flashattention": ("flash_attention", True)},
    )
    assert flags == {"threads": "8", "gpu_layers": "-1", "flash_attention": True}
