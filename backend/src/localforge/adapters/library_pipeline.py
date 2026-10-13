"""Process-isolated inference adapters for optional Python model libraries."""

import asyncio
import hashlib
import json
import platform
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from localforge.adapters.contracts import (
    AbortResult,
    AdapterCapabilities,
    AdapterCategory,
    AdapterConfiguration,
    AdapterDiagnostic,
    AdapterManifest,
    AdapterModel,
    AdapterPlatform,
    AdapterProcessHandle,
    AdapterStreamEvent,
    ConfigValidationResult,
    DiagnosticSeverity,
    GenerationRequest,
    MetricsResult,
    PreparedRequest,
    ProbeResult,
    ProbeStatus,
    RuntimeFingerprint,
    RuntimeStackLayer,
    StreamEventType,
    capability,
)
from localforge.core.models import CapabilitySupport, EngineIntegrationMode, JsonValue

_MAX_IPC_LINE_BYTES = 1_048_576
_MAX_PROMPT_CHARS = 800_000
_MAX_OUTPUT_CHARS = 16_000_000
_PLATFORM_MAP = {
    "windows": AdapterPlatform.WINDOWS,
    "linux": AdapterPlatform.LINUX,
    "darwin": AdapterPlatform.MACOS,
}


class LibraryPipelineError(RuntimeError):
    """A sanitized failure returned by the isolated library worker."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _WorkerSession:
    key: str
    process: asyncio.subprocess.Process
    info: dict[str, object]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class IsolatedLibraryPipelineAdapter:
    """Common bounded-IPC contract for native inference libraries.

    The library is imported and invoked only in a child process. One worker is kept per
    configured model/device/runtime tuple for the benchmark run. Child output is validated before
    it crosses back into the API or persistence worker.
    """

    adapter_id: str
    adapter_version: str
    display_name: str
    description: str
    docs_url: str
    backend_name: str
    package_name: str
    runtime_name: str
    configure_device = True
    supported_platforms: tuple[AdapterPlatform, ...] = (
        AdapterPlatform.WINDOWS,
        AdapterPlatform.LINUX,
        AdapterPlatform.MACOS,
    )
    default_device = "CPU"
    fixture_protocol_version = 1

    def __init__(
        self,
        *,
        _platform_name: Callable[[], str] = platform.system,
        _worker_command: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        self._platform_name = _platform_name
        self._worker_command = _worker_command
        self._sessions: dict[str, _WorkerSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._probe_lock = asyncio.Lock()
        self._active_requests: dict[UUID, _WorkerSession] = {}

    def describe(self) -> AdapterManifest:
        properties: dict[str, JsonValue] = {
            "model_path": {"type": "string", "format": "local-path"},
            "python_executable": {"type": "string", "format": "local-executable-path"},
            "startup_timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 600,
                "default": 180,
            },
            "request_timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 3600,
                "default": 300,
            },
        }
        if self.configure_device:
            properties["device"] = {"type": "string", "default": self.default_device}
        return AdapterManifest(
            protocol_version=1,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            display_name=self.display_name,
            description=self.description,
            category=AdapterCategory.LIBRARY_PIPELINE,
            integration_modes=[EngineIntegrationMode.LIBRARY_PIPELINE],
            operating_systems=list(self.supported_platforms),
            version_tested=[],
            declared_capabilities={
                "chat_completion",
                "streaming",
                "cancellation",
                "model_inventory",
                "runtime_fingerprint",
            },
            supported_transports=["bounded_json_lines_ipc"],
            config_schema={
                "type": "object",
                "required": ["model_path"],
                "properties": properties,
            },
            known_limitations=[
                "The Python package must already be installed in the selected Python runtime; "
                "an optional environment executable keeps native dependencies separate. LOCALFORGE "
                "does not install packages or download models.",
                "The model path must point to a complete local model. Model formats and devices "
                "depend on the selected inference library and installed runtime providers.",
                "Each configured model/device/runtime pair uses one isolated worker and "
                "serializes its "
                "requests. Concurrent benchmark requests therefore queue in that worker.",
                "Only text generation is normalized. Native engine metrics and exact token usage "
                "are not claimed by this adapter.",
            ],
            documentation_url=self.docs_url,
        )

    def _platform(self) -> AdapterPlatform | None:
        return _PLATFORM_MAP.get(self._platform_name().casefold())

    def _configuration(self, config: AdapterConfiguration) -> tuple[str, str, str, float, float]:
        raw_path = config.values.get("model_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Set model_path to an existing local model file or directory.")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise ValueError("model_path must be an absolute local path.")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ValueError("model_path does not exist or cannot be accessed.") from error
        if not (resolved.is_file() or resolved.is_dir()):
            raise ValueError("model_path must point to a model file or directory.")
        if len(str(resolved)) > 4096:
            raise ValueError("model_path exceeds the supported path length.")
        raw_python = config.values.get("python_executable", sys.executable)
        if not isinstance(raw_python, str) or not raw_python.strip():
            raise ValueError(
                "python_executable must be an absolute path to an existing Python file."
            )
        python_path = Path(raw_python).expanduser()
        if not python_path.is_absolute():
            raise ValueError("python_executable must be an absolute path.")
        try:
            resolved_python = python_path.resolve(strict=True)
        except OSError as error:
            raise ValueError("python_executable does not exist or cannot be accessed.") from error
        if not resolved_python.is_file():
            raise ValueError("python_executable must point to an existing file.")
        if not self.configure_device and "device" in config.values:
            raise ValueError(
                "This runtime selects its execution provider from the local model configuration."
            )
        device_value = config.values.get("device", self.default_device)
        if not isinstance(device_value, str) or not device_value.strip() or len(device_value) > 120:
            raise ValueError("device must be a non-empty value no longer than 120 characters.")
        startup_timeout = _bounded_number(
            config.values.get("startup_timeout_seconds", 180), minimum=1, maximum=600
        )
        request_timeout = _bounded_number(
            config.values.get("request_timeout_seconds", 300), minimum=1, maximum=3600
        )
        return (
            str(resolved),
            device_value.strip(),
            str(resolved_python),
            startup_timeout,
            request_timeout,
        )

    async def validate_config(self, config: AdapterConfiguration) -> ConfigValidationResult:
        diagnostics: list[AdapterDiagnostic] = []
        if config.integration_mode is not EngineIntegrationMode.LIBRARY_PIPELINE:
            diagnostics.append(
                _diagnostic(
                    "integration_mode_unsupported",
                    f"{self.display_name} requires library_pipeline integration mode.",
                    DiagnosticSeverity.ERROR,
                )
            )
        if self._platform() not in self.supported_platforms:
            supported = ", ".join(item.value for item in self.supported_platforms)
            diagnostics.append(
                _diagnostic(
                    "platform_unsupported",
                    f"{self.display_name} cannot run on this LOCALFORGE host. Use a supported "
                    f"host OS: {supported}.",
                    DiagnosticSeverity.ERROR,
                )
            )
        try:
            self._configuration(config)
        except ValueError as error:
            diagnostics.append(
                _diagnostic("model_configuration_invalid", str(error), DiagnosticSeverity.ERROR)
            )
        if not isinstance(config.values.get("device", self.default_device), str):
            diagnostics.append(
                _diagnostic("device_invalid", "device must be a string.", DiagnosticSeverity.ERROR)
            )
        for name, default, minimum, maximum in (
            ("startup_timeout_seconds", 180, 1, 600),
            ("request_timeout_seconds", 300, 1, 3600),
        ):
            value = config.values.get(name, default)
            if _bounded_number_or_none(value, minimum=minimum, maximum=maximum) is None:
                diagnostics.append(
                    _diagnostic(
                        "timeout_invalid",
                        f"{name} must be a number between {minimum} and {maximum} seconds.",
                        DiagnosticSeverity.ERROR,
                    )
                )
        return ConfigValidationResult(
            valid=not any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics),
            diagnostics=diagnostics,
        )

    async def probe(self, config: AdapterConfiguration) -> ProbeResult:
        manifest = self.describe()
        validation = await self.validate_config(config)
        if not validation.valid:
            return self._probe_failure(validation.diagnostics)
        async with self._probe_lock:
            return await self._probe_local_model(config, manifest)

    async def _probe_local_model(
        self, config: AdapterConfiguration, manifest: AdapterManifest
    ) -> ProbeResult:
        session: _WorkerSession | None = None
        try:
            _path, _device, _python, startup_timeout, _request_timeout = self._configuration(config)
            # A probe must not borrow a run's cached worker: its cleanup would stop inference
            # if a benchmark prepared that worker while the probe was finishing.
            session = await self._start_worker(config, startup_timeout, key=f"probe:{uuid4().hex}")
            info = dict(session.info)
        except Exception:
            return self._probe_failure(
                [
                    _diagnostic(
                        "library_worker_unavailable",
                        f"The isolated {self.display_name} worker could not load the local model. "
                        "Check that the library dependency is installed and the model/device "
                        "combination is supported.",
                        DiagnosticSeverity.ERROR,
                        retryable=True,
                    )
                ]
            )
        finally:
            if session is not None:
                await self._discard_session(session)
        version = _safe_info_string(info, "package_version")
        caps = {
            name: capability(CapabilitySupport.SUPPORTED, source="isolated_library_worker")
            for name in manifest.declared_capabilities
        }
        return ProbeResult(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            status=ProbeStatus.SUCCEEDED,
            reachable=capability(CapabilitySupport.SUPPORTED, source="local_process"),
            engine_version=version,
            capabilities=AdapterCapabilities(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                capabilities=caps,
            ),
        )

    def _probe_failure(self, diagnostics: list[AdapterDiagnostic]) -> ProbeResult:
        return ProbeResult(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            status=ProbeStatus.FAILED,
            reachable=capability(CapabilitySupport.UNSUPPORTED, source="local_process"),
            capabilities=AdapterCapabilities(
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                capabilities={},
            ),
            diagnostics=diagnostics,
        )

    async def start(self, config: AdapterConfiguration) -> AdapterProcessHandle:
        del config
        raise NotImplementedError("library pipelines start on demand inside the run worker")

    async def stop(self, process: AdapterProcessHandle) -> None:
        del process
        raise NotImplementedError("library pipeline workers are managed by adapter close")

    async def list_models(self, config: AdapterConfiguration) -> list[AdapterModel]:
        try:
            path, device, python, _startup_timeout, _request_timeout = self._configuration(config)
        except ValueError:
            return []
        model_ref = "localforge-configured:model"
        display_name = self.display_name.removesuffix(" library pipeline")
        return [
            AdapterModel(
                model_ref=model_ref,
                display_name=f"Configured {display_name} model",
                metadata={"source": "verified_library_pipeline_configuration"},
            )
        ]

    async def prepare_request(
        self, config: AdapterConfiguration, request: GenerationRequest
    ) -> PreparedRequest:
        validation = await self.validate_config(config)
        if not validation.valid:
            first = validation.diagnostics[0]
            raise ValueError(first.message)
        if len(request.prompt) > _MAX_PROMPT_CHARS:
            raise ValueError("Prompt exceeds the isolated pipeline IPC limit.")
        parameters = _generation_parameters(request.parameters)
        path, device, python, startup_timeout, request_timeout = self._configuration(config)
        key = _session_key(path, device, python)
        await self._ensure_worker(config, startup_timeout)
        return PreparedRequest(
            model_ref=request.model_ref,
            prompt=request.prompt,
            transport="bounded_json_lines_ipc",
            request_metadata={
                "worker_key": key,
                "parameters": parameters,
                "request_timeout_seconds": request_timeout,
            },
        )

    def stream_request(self, request: PreparedRequest) -> AsyncIterator[AdapterStreamEvent]:
        async def events() -> AsyncIterator[AdapterStreamEvent]:
            request_id = request.request_id
            worker_key = request.request_metadata.get("worker_key")
            session = self._sessions.get(worker_key) if isinstance(worker_key, str) else None
            if session is None:
                yield AdapterStreamEvent(
                    request_id=request_id,
                    sequence=0,
                    event_type=StreamEventType.ERROR,
                    error_code="library_worker_restarted",
                    error_message="The isolated model worker restarted; prepare the request again.",
                )
                return
            timeout = _bounded_number_or_none(
                request.request_metadata.get("request_timeout_seconds", 300),
                minimum=1,
                maximum=3600,
            )
            if timeout is None:
                yield _stream_error(request_id, 0, "ipc_validation_failed", "Invalid timeout.")
                return
            parameters = request.request_metadata.get("parameters")
            if not isinstance(parameters, dict):
                yield _stream_error(
                    request_id, 0, "ipc_validation_failed", "Invalid generation parameters."
                )
                return
            payload: dict[str, object] = {
                "op": "generate",
                "prompt": request.prompt,
                "parameters": parameters,
            }
            try:
                encoded = _encode_ipc(payload)
            except ValueError:
                yield _stream_error(
                    request_id, 0, "ipc_validation_failed", "Generation request exceeds IPC limit."
                )
                return
            sequence = 0
            output_size = 0
            has_output = False
            await session.lock.acquire()
            self._active_requests[request_id] = session
            try:
                await _write_line(session, encoded)
                while True:
                    message = await asyncio.wait_for(_read_message(session), timeout=float(timeout))
                    kind = message.get("type")
                    if kind == "chunk":
                        text = message.get("text")
                        if (
                            set(message) != {"type", "text"}
                            or not isinstance(text, str)
                            or not text
                            or len(text) > 32_768
                        ):
                            raise LibraryPipelineError(
                                "ipc_validation_failed",
                                "The model worker returned an invalid chunk.",
                            )
                        output_size += len(text)
                        if output_size > _MAX_OUTPUT_CHARS:
                            raise LibraryPipelineError(
                                "output_limit_exceeded",
                                "Generated output exceeded the safety limit.",
                            )
                        yield AdapterStreamEvent(
                            request_id=request_id,
                            sequence=sequence,
                            event_type=(
                                StreamEventType.FIRST_OUTPUT
                                if not has_output
                                else StreamEventType.OUTPUT_CHUNK
                            ),
                            text=text,
                        )
                        has_output = True
                        sequence += 1
                    elif kind == "complete":
                        if set(message) != {"type"}:
                            raise LibraryPipelineError(
                                "ipc_validation_failed",
                                "The model worker returned an invalid completion.",
                            )
                        yield AdapterStreamEvent(
                            request_id=request_id,
                            sequence=sequence,
                            event_type=StreamEventType.COMPLETED,
                        )
                        return
                    elif kind == "error":
                        if set(message) - {"type", "code"} or not isinstance(
                            message.get("code"), str
                        ):
                            raise LibraryPipelineError(
                                "ipc_validation_failed",
                                "The model worker returned an invalid error.",
                            )
                        code = _safe_error_code(message.get("code"))
                        yield _stream_error(
                            request_id,
                            sequence,
                            code,
                            "The isolated inference library reported a generation failure.",
                        )
                        await self._discard_session(session)
                        return
                    else:
                        raise LibraryPipelineError(
                            "ipc_validation_failed", "The model worker returned an invalid message."
                        )
            except TimeoutError:
                await self._discard_session(session)
                yield _stream_error(
                    request_id,
                    sequence,
                    "library_request_timeout",
                    "The isolated model worker exceeded its request timeout and was stopped.",
                    event_type=StreamEventType.TIMEOUT,
                )
            except asyncio.CancelledError:
                await self._discard_session(session)
                raise
            except Exception as error:
                await self._discard_session(session)
                code = (
                    error.code
                    if isinstance(error, LibraryPipelineError)
                    else "library_worker_crashed"
                )
                yield _stream_error(
                    request_id,
                    sequence,
                    code,
                    "The isolated model worker stopped unexpectedly and was discarded.",
                )
            finally:
                self._active_requests.pop(request_id, None)
                if session.lock.locked():
                    session.lock.release()

        return events()

    async def abort_request(self, request_id: UUID) -> AbortResult:
        session = self._active_requests.get(request_id)
        if session is None:
            return AbortResult(supported=True, accepted=False, message="No active request found.")
        await self._discard_session(session)
        return AbortResult(supported=True, accepted=True, message="Isolated worker stopped.")

    async def read_metrics(self, config: AdapterConfiguration) -> MetricsResult:
        del config
        return MetricsResult(
            available=False,
            diagnostic=_diagnostic(
                "native_metrics_unsupported",
                "The library pipeline has no normalized native metrics connector.",
            ),
        )

    async def collect_fingerprint(self, config: AdapterConfiguration) -> RuntimeFingerprint:
        _path, device, _python, startup_timeout, _request_timeout = self._configuration(config)
        session = await self._ensure_worker(config, startup_timeout)
        package_version = _safe_info_string(session.info, "package_version")
        reported_device = _safe_info_string(session.info, "reported_device")
        if reported_device is not None:
            settings: dict[str, JsonValue] = {"device": reported_device}
        else:
            settings = {"device": device} if self.configure_device else {}
        model_format = _safe_info_string(session.info, "model_format")
        if model_format:
            settings["model_format"] = model_format
        layers = [
            RuntimeStackLayer(
                layer_type="runtime",
                name=self.runtime_name,
                version=package_version,
                source="engine_reported",
            )
        ]
        secondary_name = _safe_info_string(session.info, "secondary_runtime_name")
        secondary_version = _safe_info_string(session.info, "secondary_runtime_version")
        if secondary_name:
            layers.append(
                RuntimeStackLayer(
                    layer_type="runtime",
                    name=secondary_name,
                    version=secondary_version,
                    source="engine_reported",
                )
            )
        return RuntimeFingerprint(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            engine_version=package_version,
            runtime_name=self.runtime_name,
            runtime_version=package_version,
            stack_layers=layers,
            settings=settings,
        )

    async def close(self) -> None:
        sessions = tuple(self._sessions.values())
        self._sessions.clear()
        self._active_requests.clear()
        await asyncio.gather(*(self._stop_process(session.process) for session in sessions))

    async def _ensure_worker(
        self, config: AdapterConfiguration, startup_timeout: float
    ) -> _WorkerSession:
        path, device, python, _startup_timeout, _request_timeout = self._configuration(config)
        key = _session_key(path, device, python)
        existing = self._sessions.get(key)
        if existing is not None and existing.process.returncode is None:
            return existing
        async with self._sessions_lock:
            existing = self._sessions.get(key)
            if existing is not None and existing.process.returncode is None:
                return existing
            if existing is not None:
                await self._discard_session(existing)
            session = await self._start_worker(config, startup_timeout, key=key)
            self._sessions[key] = session
            return session

    async def _start_worker(
        self,
        config: AdapterConfiguration,
        startup_timeout: float,
        *,
        key: str,
    ) -> _WorkerSession:
        if self._platform() not in self.supported_platforms:
            raise LibraryPipelineError(
                "platform_unsupported",
                f"{self.display_name} is unavailable on this host OS. Use a supported host: "
                + ", ".join(item.value for item in self.supported_platforms),
            )
        path, device, python, _startup_timeout, _request_timeout = self._configuration(config)
        worker_command = (
            self._worker_command(self.backend_name)
            if self._worker_command is not None
            else (
                python,
                str(Path(__file__).with_name("library_worker.py")),
                "--backend",
                self.backend_name,
            )
        )
        process = await asyncio.create_subprocess_exec(
            *worker_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=_MAX_IPC_LINE_BYTES,
        )
        session = _WorkerSession(key=key, process=process, info={})
        try:
            await _write_line(
                session,
                _encode_ipc({"op": "initialize", "model_path": path, "device": device}),
            )
            info = await asyncio.wait_for(_read_message(session), timeout=startup_timeout)
            if info.get("type") != "ready" or not _valid_ready_message(info):
                raise LibraryPipelineError(
                    _safe_error_code(info.get("code")),
                    "The isolated model worker could not initialize this library/model pair.",
                )
            session.info = info
            return session
        except BaseException:
            await self._stop_process(process)
            raise

    async def _discard_session(self, session: _WorkerSession) -> None:
        if self._sessions.get(session.key) is session:
            self._sessions.pop(session.key, None)
        await self._stop_process(session.process)

    @staticmethod
    async def _stop_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()


def _diagnostic(
    code: str,
    message: str,
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO,
    *,
    retryable: bool = False,
) -> AdapterDiagnostic:
    return AdapterDiagnostic(
        code=code,
        message=message,
        severity=severity,
        retryable=retryable,
    )


def _bounded_number(value: object, *, minimum: float, maximum: float) -> float:
    result = _bounded_number_or_none(value, minimum=minimum, maximum=maximum)
    if result is None:
        raise ValueError("timeout setting is outside its supported range.")
    return result


def _bounded_number_or_none(value: object, *, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if minimum <= number <= maximum else None


def _generation_parameters(parameters: dict[str, JsonValue]) -> dict[str, JsonValue]:
    allowed = {"max_tokens", "temperature", "top_p"}
    if set(parameters) - allowed:
        raise ValueError(
            "The isolated library pipeline supports max_tokens, temperature, and top_p."
        )
    max_tokens = parameters.get("max_tokens", 256)
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 1 <= max_tokens <= 8192
    ):
        raise ValueError("max_tokens must be an integer between 1 and 8192.")
    result: dict[str, JsonValue] = {"max_tokens": max_tokens}
    for name, default, maximum in (("temperature", 1.0, 2.0), ("top_p", 1.0, 1.0)):
        value = parameters.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number.")
        number = float(value)
        if not 0 <= number <= maximum:
            raise ValueError(f"{name} must be between 0 and {maximum}.")
        result[name] = number
    return result


def _session_key(path: str, device: str, python: str) -> str:
    return hashlib.sha256(f"{path}\0{device}\0{python}".encode()).hexdigest()


def _encode_ipc(payload: Mapping[str, object]) -> bytes:
    try:
        encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise ValueError("IPC payload is not JSON serializable.") from error
    if len(encoded) > _MAX_IPC_LINE_BYTES:
        raise ValueError("IPC payload exceeds its configured bound.")
    return encoded + b"\n"


async def _write_line(session: _WorkerSession, encoded: bytes) -> None:
    writer = session.process.stdin
    if writer is None or session.process.returncode is not None:
        raise LibraryPipelineError("library_worker_crashed", "The model worker is not running.")
    writer.write(encoded)
    await writer.drain()


async def _read_message(session: _WorkerSession) -> dict[str, object]:
    reader = session.process.stdout
    if reader is None:
        raise LibraryPipelineError("library_worker_crashed", "The model worker has no IPC output.")
    try:
        line = await reader.readline()
    except (ValueError, asyncio.LimitOverrunError) as error:
        raise LibraryPipelineError(
            "ipc_validation_failed", "The model worker exceeded the IPC bound."
        ) from error
    if not line:
        raise LibraryPipelineError("library_worker_crashed", "The isolated model worker exited.")
    if len(line) > _MAX_IPC_LINE_BYTES or not line.endswith(b"\n"):
        raise LibraryPipelineError(
            "ipc_validation_failed", "The model worker sent an oversized IPC line."
        )
    try:
        message = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LibraryPipelineError(
            "ipc_validation_failed", "The model worker sent invalid JSON."
        ) from error
    if not isinstance(message, dict):
        raise LibraryPipelineError(
            "ipc_validation_failed", "The model worker sent a non-object message."
        )
    return cast(dict[str, object], message)


def _valid_ready_message(message: dict[str, object]) -> bool:
    if set(message) - {
        "type",
        "runtime_name",
        "package_version",
        "secondary_runtime_name",
        "secondary_runtime_version",
        "model_format",
        "reported_device",
    }:
        return False
    if not isinstance(message.get("package_version"), str):
        return False
    if not isinstance(message.get("runtime_name"), str):
        return False
    for name in (
        "secondary_runtime_name",
        "secondary_runtime_version",
        "model_format",
        "reported_device",
    ):
        value = message.get(name)
        if value is not None and not isinstance(value, str):
            return False
    return True


def _safe_info_string(message: dict[str, object], key: str) -> str | None:
    value = message.get(key)
    return value if isinstance(value, str) and value != "unknown" and len(value) <= 120 else None


def _safe_error_code(value: object) -> str:
    allowed = {
        "library_dependency_missing",
        "model_load_failed",
        "device_unavailable",
        "ipc_validation_failed",
        "library_generation_failed",
    }
    return value if isinstance(value, str) and value in allowed else "library_worker_failed"


def _stream_error(
    request_id: UUID,
    sequence: int,
    code: str,
    message: str,
    *,
    event_type: StreamEventType = StreamEventType.ERROR,
) -> AdapterStreamEvent:
    return AdapterStreamEvent(
        request_id=request_id,
        sequence=sequence,
        event_type=event_type,
        error_code=code,
        error_message=message,
    )
