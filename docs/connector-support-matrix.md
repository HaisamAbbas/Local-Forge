# Connector support matrix

**Reviewed:** 13 October 2026

Serving connectors attach to an endpoint the user has already started. Library-pipeline connectors run
an already-installed inference package against an existing local model in an isolated process.
LOCALFORGE does not install engines or dependencies, compile or download models, start serving
endpoints except for the explicitly marked llama.cpp managed mode, or choose an accelerator. The
host OS column describes the machine running LOCALFORGE; an attached server can run on another
machine. `version_tested` values marked as local fixtures are synthetic test responses, not
upstream release or binary validation. No live upstream release validation is claimed by this
matrix. Library package versions are learned only when a local runtime probe reads installed
package metadata.

Modes and statuses use the exact manifest values so the automated catalog test can compare this
table with the API catalog. An empty mode means the entry is not callable. `localforge.fake` is a
test fixture outside the intended user-facing ecosystem, but is included to keep the complete API
catalog accounted for.

| Adapter ID | Connector | Implementation status | Integration modes | LOCALFORGE host OS | `version_tested` | Documentation | Scope and limits |
|---|---|---|---|---|---|---|---|
| `localforge.exllamav3` | ExLlamaV3 library pipeline | planned | none | windows, linux | none | [Docs](https://github.com/turboderp-org/exllamav3) | CUDA-oriented library; no callable LOCALFORGE pipeline. PyTorch, CUDA, compatible GPU, and local quantized model are prerequisites. TabbyAPI is a separate project. |
| `localforge.fake` | Deterministic Fake Engine (test fixture) | implemented | attached_endpoint, managed_process | windows, linux, macos | none | none | Deterministic test adapter only. It does not load a language model and its timings are not inference measurements. |
| `localforge.koboldcpp` | KoboldCpp serving endpoint | implemented | attached_endpoint | windows, linux, macos | `1.122.1 (local API fixture); 1.123 (live, Windows CPU)` | [Docs](https://github.com/LostRuins/koboldcpp/wiki) | Attach-only Chat Completions. Reports KoboldCpp identity; does not infer its lower-level runtime or claim native metrics. |
| `localforge.lightllm` | LightLLM serving endpoint | implemented | attached_endpoint | windows, linux, macos | `1.2.0 (local API fixture)` | [Docs](https://github.com/ModelTC/LightLLM) | Attach-only Chat Completions. Fixture compatibility only; server version and native metrics remain unclaimed. |
| `localforge.llama_cpp` | llama.cpp / llama-server | implemented | attached_endpoint, managed_process | windows, linux, macos | `b11580 (local API fixture); b11609 (live, Windows CPU, attached and managed)` | [Docs](https://github.com/ggml-org/llama.cpp/blob/4413a192410fad1f054e9216114087a3e01527a0/tools/server/README.md) | Attach to an existing server or launch an explicitly selected local binary and existing GGUF file. Managed mode is loopback-only; GPU support depends on the selected build. |
| `localforge.lmdeploy` | LMDeploy serving endpoint | implemented | attached_endpoint | windows, linux, macos | `0.18.0 (local API fixture)` | [Docs](https://lmdeploy.readthedocs.io/en/latest/llm/api_server.html) | Attach-only OpenAI-compatible server. Version remains unknown unless a safe version route is configured; native samples stay engine-native. |
| `localforge.localai` | LocalAI serving endpoint | implemented | attached_endpoint | windows, linux, macos | `4.11.0 (local API fixture)` | [Docs](https://localai.io/) | Attach-only OpenAI-compatible server. A backend fingerprint layer is saved only when validated model configuration explicitly reports it. |
| `localforge.mistral_rs` | mistral.rs serving endpoint | implemented | attached_endpoint | windows, linux, macos | none | [Docs](https://ericlbuehler.github.io/mistral.rs/guides/serve/openai-compatible-apis/) | Attach-only server. Collects only the `mistralrs_*` metric family; does not claim server version or bearer-auth validation. |
| `localforge.mlc_llm` | MLC LLM serving endpoint | implemented | attached_endpoint | windows, linux, macos | none | [Docs](https://llm.mlc.ai/docs/deploy/rest.html) | Attach-only REST server. The compiled model library must already be installed and served; no health, version, or native metrics claims. |
| `localforge.mlx_lm` | MLX-LM serving endpoint | implemented | attached_endpoint | windows, linux, macos | none | [Docs](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py) | Attach-only server; target must already run on Apple silicon macOS. LOCALFORGE host may use any supported OS. |
| `localforge.nvidia_dynamo` | NVIDIA Dynamo frontend and serving stack | implemented | composed_serving_stack | windows, linux, macos | none | [Docs](https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/frontend/overview) | Attach to a composed serving stack. Reads frontend metrics only; verified workers remain separate layers and aggregate/per-worker series are never summed. |
| `localforge.ollama` | Ollama | implemented | attached_endpoint | windows, linux, macos | `0.12.0 (local API fixture); 0.40.3 (live, Windows CPU)` | [Docs](https://docs.ollama.com/api) | Attach to an existing local service. Does not pull/delete models or start/stop Ollama; remote-backed inventory is excluded. |
| `localforge.onnxruntime_genai` | ONNX Runtime GenAI library pipeline | implemented | library_pipeline | windows, linux, macos | none | [Docs](https://onnxruntime.ai/docs/genai/) | Runs installed Python package in an isolated child using an existing local model directory. Provider support depends on installed package and model configuration. |
| `localforge.openai_compatible` | Custom OpenAI-compatible endpoint | implemented | attached_endpoint | windows, linux, macos | none | [Docs](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) | Generic attached chat endpoint for local or explicitly confirmed remote use. Does not infer vendor, engine version, or vendor metrics. |
| `localforge.openvino_genai` | OpenVINO GenAI library pipeline | implemented | library_pipeline | windows, linux, macos | none | [Docs](https://docs.openvino.ai/2026/openvino-workflow-generative/inference-with-genai.html) | Runs installed OpenVINO GenAI in an isolated child using an existing supported local model. Device and package versions are reported only after a successful local probe. |
| `localforge.sglang` | SGLang serving endpoint | implemented | attached_endpoint | windows, linux, macos | `0.4.9 (local API fixture)` | [Docs](https://docs.sglang.ai/basic_usage/openai_api.html) | Attach-only OpenAI-compatible endpoint. Version stays unknown by default; optional metrics preserve engine-native definitions. |
| `localforge.tensorrt_llm` | TensorRT-LLM `trtllm-serve` | implemented | attached_endpoint | windows, linux, macos | `1.3 (local API fixture)` | [Docs](https://nvidia.github.io/TensorRT-LLM/commands/trtllm-serve/trtllm-serve.html) | Attach-only Chat Completions endpoint. No managed launcher, engine install, or model download. |
| `localforge.tgi` | Hugging Face TGI serving endpoint | implemented | attached_endpoint | windows, linux, macos | `3.3.7 (local API fixture)` | [Docs](https://huggingface.co/docs/text-generation-inference/en/index) | Attach-only and marked maintenance mode; useful for existing deployments, not recommended for new managed installs. |
| `localforge.triton_tensorrt_llm` | NVIDIA Triton + TensorRT-LLM backend | planned | none | linux | none | [Docs](https://github.com/triton-inference-server/tensorrtllm_backend) | Not callable. Its model-specific generation route is not a verified Chat Completions integration; Triton/backend identity and versions remain unknown. |
| `localforge.vllm` | vLLM serving endpoint | implemented | attached_endpoint | windows, linux, macos | `0.10.2 (local API fixture)` | [Docs](https://docs.vllm.ai/en/stable/serving/online_serving/) | Attach-only OpenAI-compatible server. No engine install, managed process, or model download. |

The generic profile form renders supported adapter settings from each manifest's configuration
schema. Local library profiles use that same form for a model path, optional Python executable,
timeouts, and a runtime device where applicable. The settings are stored in the adapter's own
extension namespace and passed to the isolated library worker; they are not benchmark-storage
columns.

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
