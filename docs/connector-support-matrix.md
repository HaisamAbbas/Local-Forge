# Connector support matrix

**Reviewed:** 13 October 2026

Serving connectors attach to an endpoint the user has already started. Library-pipeline connectors
run an already-installed inference package against an existing local model in an isolated process.
LOCALFORGE does not install engines/dependencies, compile or download models, start serving
endpoints, or choose an accelerator. The OS list in the catalog describes where the LOCALFORGE
client can run; an attached server can run on another host. Fixture version values below are
synthetic and do not claim live binary compatibility. Library package versions appear only when a
local runtime probe reads the installed package metadata.

| Connector | Status and integration | LOCALFORGE host OS | Fixture version | Version, model, and metric behavior |
|---|---|---|---|---|
| TensorRT-LLM `trtllm-serve` | Implemented; attach-only OpenAI Chat Completions | Windows, Linux, macOS | `1.3` | Reads `/version`, `/health`, and `trtllm_*` Prometheus samples. |
| NVIDIA Triton + TensorRT-LLM backend | Planned; not callable | Linux | Not recorded | Triton uses model-specific `/v2/models/{name}/generate` routes. LOCALFORGE has no verified chat adapter for this API and does not report a working connector. Triton and backend versions remain unknown. |
| LMDeploy | Implemented; attach-only OpenAI-compatible API server | Windows, Linux, macOS | `0.18.0` | Version remains unknown unless a safe version route is explicitly configured. Reads `lmdeploy:*` Prometheus samples when exposed. |
| LightLLM | Implemented; attach-only OpenAI Chat Completions | Windows, Linux, macOS | `1.2.0` | Uses the documented chat route and health check. It does not claim version discovery or native metrics because no stable route is documented by the connector. |
| Hugging Face TGI | Implemented; attach-only OpenAI Messages API | Windows, Linux, macOS | `3.3.7` | Reads `/info`, `/health`, and `tgi_*` Prometheus samples. Catalog and UI label it maintenance mode; it is not recommended for a new managed installation. |
| LocalAI | Implemented; attach-only OpenAI-compatible API | Windows, Linux, macOS | `4.11.0` | Reads `/version` and `api_call*` metrics when authorized and enabled. A backend is stored as a separate layer only when the selected model configuration explicitly reports it. |
| KoboldCpp | Implemented; attach-only OpenAI Chat Completions | Windows, Linux, macOS | `1.122.1` | Reads `/api/extra/version`. It identifies KoboldCpp as the server and leaves the lower-level inference runtime unknown. Its JSON performance route is not misrepresented as Prometheus metrics. |
| MLX-LM | Implemented; attach-only OpenAI-compatible server | Windows, Linux, macOS | Not recorded | LOCALFORGE can attach from any supported desktop host; the target server must already run on Apple silicon macOS. Uses the official chat and model routes. It does not claim health, version, or native metrics. The model must already be local to that server. |
| MLC LLM | Implemented; attach-only REST server | Windows, Linux, macOS | Not recorded | Uses the documented Chat Completions and model routes. The MLC model library must already be compiled and served. The connector does not claim health, version, or native metrics. |
| OpenVINO GenAI | Implemented; isolated library pipeline | Windows, Linux, macOS | Fixture IPC only | Runs a local OpenVINO GenAI pipeline in a child process. Uses a preconverted OpenVINO model or supported local GGUF input and a configured device such as CPU. The runtime package versions are recorded after a successful local probe; no upstream binary is installed or live-tested in CI. |
| ONNX Runtime GenAI | Implemented; isolated library pipeline | Windows, Linux, macOS | Fixture IPC only | Runs the ONNX Runtime GenAI Python library in a child process using an existing local model directory. The installed package/provider combination determines CPU or accelerator support. Its Python API is documented as preview and can change. The model-reported device and package versions are fingerprinted after a successful local probe. |
| mistral.rs | Implemented; attach-only OpenAI-compatible server | Windows, Linux, macOS | Not recorded | Reads `/health` and only the `mistralrs_*` Prometheus family. Does not claim a server version route, bearer authentication, or normalization of the server's `http_*` metrics. Bind locally or place an authenticating reverse proxy in front of the endpoint. |
| NVIDIA Dynamo | Implemented; attached composed serving stack | Windows, Linux, macOS | Not recorded | Uses the OpenAI-compatible frontend and reads only its `dynamo_frontend_*` metrics. Router worker IDs and reported state become fingerprint layers only when emitted by verified frontend inventory metrics; discovered workers are not necessarily available for routing. Worker/component and backend metrics are not collected. Aggregate and per-worker samples retain distinct names and labels; no sums are inferred. |
| ExLlamaV3 | Planned; isolated library pipeline | Windows, Linux | Not recorded | CUDA-oriented Python inference library with PyTorch/CUDA requirements and local quantized model formats. It has no project-owned OpenAI-compatible server; TabbyAPI is a separate project. LOCALFORGE does not claim a callable ExLlamaV3 adapter. |

### Prompt 15C runtime limits

- **MLX-LM:** The connected server runs on Apple silicon macOS. LOCALFORGE can run on any supported
  desktop host because this connector only attaches to the remote HTTP server. MLX-LM owns the
  model package and accelerator selection.
- **MLC LLM:** The server must already have MLC LLM installed and a compiled model library. Its
  server documentation lists `cuda`, `metal`, `vulkan`, `rocm`, `opencl`, and `auto` device values.
  The connector does not start model compilation or download weights.
- **OpenVINO GenAI:** The worker accepts CPU or a locally configured plugin device. The current
  official examples use CPU/GPU, while OpenVINO separately documents Intel GPU/NPU support. GGUF
  inference is preview and limited to documented topologies; converted OpenVINO model directories
  are the broader path.
- **ONNX Runtime GenAI:** The input is a local directory containing the GenAI model configuration
  and ONNX model data. CPU, CUDA, and DirectML use distinct package variants; other execution
  providers depend on the package build and model configuration. The child reports the model's
  device type rather than copying an app-side default into the fingerprint. The API reference
  labels its Python surface preview.
- **mistral.rs:** Current prebuilt platform options include Apple Silicon/Metal on macOS, CPU on
  Windows and CPU builds on Linux, and CPU/CUDA builds on supported Linux/NVIDIA combinations.
  Other hardware may require an upstream source build. LOCALFORGE does not choose the backend or
  install the binary.
- **NVIDIA Dynamo:** The LOCALFORGE client can attach from any supported desktop OS. The target
  serving deployment is normally Linux with NVIDIA hardware and a user-managed Dynamo frontend,
  router, and supported workers. The frontend's inventory metrics report discovered worker IDs and
  their reported state; they do not prove Kubernetes pod existence or identify every backend detail.
- **ExLlamaV3:** The upstream library needs a compatible PyTorch and CUDA installation (the current
  repository specifies CUDA 12.4 or newer) and local supported quantized model files. Windows/Linux
  release wheels do not make the connector implemented; the LOCALFORGE entry remains planned.

## Stack identity and privacy

Runtime fingerprints are collected before benchmark measurements and stored inside each run's
`profile_snapshot.runtime_fingerprint`. The `stack_layers` array records each verified layer as an
independent object with its role, name, version, and source. Missing or unauthorized layers are
omitted and remain unknown. Fingerprint collection is bounded; if it fails, the run stores a
sanitized unavailable status and continues.
Run snapshots omit raw endpoint paths because user-configured path segments may contain credentials.

LocalAI is represented as a wrapper. If the user supplies a selected model ID (or the run already
has one selected), LOCALFORGE may read that model's `/api/models/config-json/{name}` configuration.
The route can require LocalAI administrator authorization. The adapter keeps only the validated
`backend` name; it does not retain the full configuration, model paths, tokens, or other fields. A
missing, malformed, or unauthorized configuration leaves the backend layer unknown. KoboldCpp is
recorded as its own serving runtime; the connector does not infer llama.cpp as a separately selected
backend from project lineage or GGUF format.

Prompt 15C library adapters run model code in a separate process with bounded JSON Lines IPC. Each
model/device/runtime configuration has one child worker, so its requests are serialized; higher
configured benchmark concurrency queues through that worker. Users can select an existing Python
executable for a separate dependency environment. A child crash or request timeout discards the
process, and a later request starts a fresh one. The probe does not install dependencies, download
models, or contact a model hub. It fingerprints installed runtime package versions and a small
allowlist of settings; local filesystem paths are not included in the run fingerprint. Standard CI
fixtures exercise the child protocol without claiming live OpenVINO or ONNX runtime versions.

The Dynamo adapter captures the HTTP frontend metrics endpoint only. It does not combine
`dynamo_frontend_*` values with `dynamo_component_*`, `dynamo_epp_*`, operator, NIXL, or backend
engine metrics. Verified router worker state samples supply worker identities; missing state samples
mean the topology is unknown, even when aggregate worker counts exist.

ExLlamaV3 remains planned because its official project is an inference library and points to
third-party TabbyAPI for an OpenAI-compatible server. A LOCALFORGE library worker needs its own
isolated generation adapter and fixture contract before this entry can be callable.

## Fixture contract

Each implemented connector passes the shared adapter contract against a local HTTP fixture. The
fixtures cover API routing, version response parsing where documented, model inventory, streaming,
sanitized errors, native metric provenance or an explicit unsupported result, and bounded timeout
behavior. LocalAI adds composition tests for a verified backend, an unauthorized/unknown backend,
and a model ID that must not be used as a route. TGI and planned Triton status are checked in both
the API manifest and the catalog UI. No default test contacts a public endpoint or downloads a
model. Live server checks still require a separately started server and are not claimed by this
matrix.

## Official references

- [TensorRT-LLM `trtllm-serve` API and endpoints](https://nvidia.github.io/TensorRT-LLM/commands/trtllm-serve/trtllm-serve.html)
- [NVIDIA Triton TensorRT-LLM backend API and Prometheus metrics](https://github.com/triton-inference-server/tensorrtllm_backend)
- [LMDeploy OpenAI-compatible API server](https://lmdeploy.readthedocs.io/en/latest/llm/api_server.html) and [production metrics](https://lmdeploy.readthedocs.io/en/latest/advance/metrics.html)
- [LightLLM server API](https://github.com/ModelTC/LightLLM/blob/main/lightllm/server/api_server.py) and [official releases](https://github.com/ModelTC/LightLLM/releases)
- [TGI API reference](https://huggingface.co/docs/text-generation-inference/reference/api_reference), [metrics](https://huggingface.co/docs/text-generation-inference/reference/metrics), and [maintenance notice](https://huggingface.co/docs/text-generation-inference/index)
- [LocalAI API and backend configuration](https://localai.io/docs/advanced/) and [Prometheus monitoring](https://localai.io/docs/operations/traffic/)
- [KoboldCpp API and OpenAI-compatible routes](https://github.com/LostRuins/koboldcpp/wiki)
- [MLX-LM server source](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py) and [official releases](https://github.com/ml-explore/mlx-lm/releases)
- [MLC LLM REST API](https://llm.mlc.ai/docs/deploy/rest.html)
- [OpenVINO GenAI inference](https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai.html) and [Python pipeline API](https://docs.openvino.ai/2026/api/genai_api/_autosummary/openvino_genai.LLMPipeline.html)
- [ONNX Runtime GenAI Python API](https://onnxruntime.ai/docs/genai/api/python.html), [install and execution-provider options](https://onnxruntime.ai/docs/genai/howto/install.html), and [official Python example](https://github.com/microsoft/onnxruntime-genai/blob/main/examples/python/model-qa.py)
- [mistral.rs OpenAI-compatible APIs](https://ericlbuehler.github.io/mistral.rs/guides/serve/openai-compatible-apis/) and [quickstart](https://ericlbuehler.github.io/mistral.rs/quickstart/)
- [ExLlamaV3 official repository](https://github.com/turboderp-org/exllamav3), [official releases](https://github.com/turboderp-org/exllamav3/releases), and [official generation example](https://github.com/turboderp-org/exllamav3/blob/master/examples/generation_loop.py)
- [NVIDIA Dynamo frontend API](https://docs.nvidia.com/dynamo/dev/reference/components/frontend-configuration), [metrics catalog](https://docs.nvidia.com/dynamo/dev/reference/observability/metrics-catalog), and [metric labels](https://docs.nvidia.com/dynamo/dev/reference/observability/metric-labels)
