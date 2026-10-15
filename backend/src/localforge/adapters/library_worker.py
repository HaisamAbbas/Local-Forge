"""Child-process entry point for OpenVINO GenAI and ONNX Runtime GenAI pipelines."""

import argparse
import importlib
import importlib.metadata
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_MAX_IPC_LINE_BYTES = 1_048_576
_MAX_OUTPUT_CHARS = 16_000_000
_MAX_CHUNK_CHARS = 32_768


def _protocol_output() -> Any:
    """Reserve an unpolluted protocol stream and send native stdout noise to the sink."""

    protocol_fd = os.dup(sys.stdout.fileno())
    sink_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(sink_fd, sys.stdout.fileno())
    os.close(sink_fd)
    return os.fdopen(protocol_fd, "w", encoding="utf-8", buffering=1)


def _send(output: Any, message: dict[str, object]) -> None:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_IPC_LINE_BYTES:
        output.write('{"type":"error","code":"ipc_validation_failed"}\n')
        output.flush()
        return
    output.write(encoded + "\n")
    output.flush()


def _read_input() -> dict[str, object] | None:
    line = sys.stdin.buffer.readline(_MAX_IPC_LINE_BYTES + 1)
    if not line:
        return None
    if len(line) > _MAX_IPC_LINE_BYTES or not line.endswith(b"\n"):
        return {"op": "invalid"}
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"op": "invalid"}
    return value if isinstance(value, dict) else {"op": "invalid"}


def _package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _int_parameter(parameters: dict[str, object], name: str) -> int:
    value = parameters.get(name)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError("invalid integer generation parameter")


def _float_parameter(parameters: dict[str, object], name: str) -> float:
    value = parameters.get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ValueError("invalid numeric generation parameter")


def _initialize(
    backend: str, path: str, device: str
) -> tuple[dict[str, object], Callable[..., object]]:
    model_path = Path(path)
    if not model_path.is_absolute() or not model_path.exists():
        raise ValueError("model path unavailable")
    # Folder names such as "qwen2.5-1.5b-..." contain dots, so only files have a suffix format.
    if model_path.is_dir():
        model_format = (
            "openvino_ir" if (model_path / "openvino_model.xml").is_file() else "model_directory"
        )
    else:
        model_format = model_path.suffix.casefold().lstrip(".") or "model_file"
    if backend == "openvino_genai":
        openvino_genai = importlib.import_module("openvino_genai")

        pipeline = openvino_genai.LLMPipeline(str(model_path), device)
        info: dict[str, object] = {
            "type": "ready",
            "runtime_name": "OpenVINO GenAI",
            "package_version": _package_version("openvino-genai"),
            "secondary_runtime_name": "OpenVINO Runtime",
            "secondary_runtime_version": _package_version("openvino"),
            "model_format": model_format,
            "reported_device": device,
        }

        def generate(
            prompt: str, parameters: dict[str, object], emit: Callable[[str], None]
        ) -> None:
            config = openvino_genai.GenerationConfig()
            config.max_new_tokens = _int_parameter(parameters, "max_tokens")
            temperature = _float_parameter(parameters, "temperature")
            config.temperature = temperature
            config.top_p = _float_parameter(parameters, "top_p")
            config.do_sample = temperature > 0

            def streamer(chunk: str) -> None:
                emit(chunk)
                return None

            pipeline.generate(prompt, config, streamer=streamer)

        return info, generate
    if backend == "onnxruntime_genai":
        runtime = importlib.import_module("onnxruntime_genai")

        model = runtime.Model(str(model_path))
        tokenizer = runtime.Tokenizer(model)
        info = {
            "type": "ready",
            "runtime_name": "ONNX Runtime GenAI",
            "package_version": _package_version("onnxruntime-genai"),
            "secondary_runtime_name": "ONNX Runtime",
            "secondary_runtime_version": _package_version("onnxruntime"),
            "model_format": "onnx_genai",
            "reported_device": str(model.device_type),
        }

        def generate(
            prompt: str, parameters: dict[str, object], emit: Callable[[str], None]
        ) -> None:
            tokens = tokenizer.encode(prompt)
            params = runtime.GeneratorParams(model)
            options: dict[str, object] = {
                "max_length": len(tokens[0]) + _int_parameter(parameters, "max_tokens"),
                "temperature": _float_parameter(parameters, "temperature"),
                "top_p": _float_parameter(parameters, "top_p"),
            }
            params.set_search_options(**options)
            generator = runtime.Generator(model, params)
            generator.append_tokens(tokens)
            stream = tokenizer.create_stream()
            output_chars = 0
            while not generator.is_done():
                generator.generate_next_token()
                token_chunk = stream.decode(int(generator.get_next_tokens()[0]))
                if token_chunk:
                    output_chars += len(token_chunk)
                    if output_chars > _MAX_OUTPUT_CHARS:
                        raise RuntimeError("output limit")
                    emit(token_chunk)

        return info, generate
    raise ValueError("unsupported backend")


def run(backend: str) -> int:
    output = _protocol_output()
    initial = _read_input()
    if not isinstance(initial, dict) or initial.get("op") != "initialize":
        _send(output, {"type": "error", "code": "ipc_validation_failed"})
        return 2
    path = initial.get("model_path")
    device = initial.get("device")
    if not isinstance(path, str) or not isinstance(device, str):
        _send(output, {"type": "error", "code": "ipc_validation_failed"})
        return 2
    try:
        ready, generate = _initialize(backend, path, device)
    except ImportError:
        _send(output, {"type": "error", "code": "library_dependency_missing"})
        return 3
    except Exception as error:
        message = str(error).casefold()
        code = (
            "device_unavailable"
            if "device" in message or "plugin" in message
            else "model_load_failed"
        )
        _send(output, {"type": "error", "code": code})
        return 4
    _send(output, ready)

    while True:
        request = _read_input()
        if request is None:
            return 0
        operation = request.get("op")
        if operation == "close":
            return 0
        prompt = request.get("prompt")
        parameters = request.get("parameters")
        if (
            operation != "generate"
            or not isinstance(prompt, str)
            or len(prompt) > 800_000
            or not isinstance(parameters, dict)
            or set(parameters) != {"max_tokens", "temperature", "top_p"}
            or isinstance(parameters.get("max_tokens"), bool)
            or not isinstance(parameters.get("max_tokens"), int)
            or not 1 <= int(parameters["max_tokens"]) <= 8192
            or any(
                isinstance(parameters.get(name), bool)
                or not isinstance(parameters.get(name), (int, float))
                for name in ("temperature", "top_p")
            )
            or not 0 <= float(parameters["temperature"]) <= 2
            or not 0 <= float(parameters["top_p"]) <= 1
        ):
            _send(output, {"type": "error", "code": "ipc_validation_failed"})
            continue
        output_chars = 0

        def emit(chunk: str) -> None:
            nonlocal output_chars
            if not isinstance(chunk, str):
                raise TypeError("invalid token chunk")
            output_chars += len(chunk)
            if output_chars > _MAX_OUTPUT_CHARS:
                raise RuntimeError("output limit")
            for start in range(0, len(chunk), _MAX_CHUNK_CHARS):
                _send(output, {"type": "chunk", "text": chunk[start : start + _MAX_CHUNK_CHARS]})

        try:
            generate(prompt, parameters, emit)
        except Exception:
            _send(output, {"type": "error", "code": "library_generation_failed"})
            continue
        _send(output, {"type": "complete"})


def _drop_own_directory_from_path() -> None:
    """Stop this script's folder from shadowing the libraries it loads.

    Run as a script, Python puts this file's folder first on sys.path. That folder holds
    LocalForge's adapter modules named ``openvino_genai.py`` and ``onnxruntime_genai.py``, so
    importing the real runtime packages would load those adapter files instead.
    """

    own_directory = Path(__file__).resolve().parent
    sys.path[:] = [
        entry for entry in sys.path if not entry or Path(entry).resolve() != own_directory
    ]


def main() -> None:
    _drop_own_directory_from_path()
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("openvino_genai", "onnxruntime_genai"), required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.backend))


if __name__ == "__main__":
    main()
