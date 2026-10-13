# Connector support matrix

**Reviewed:** 13 October 2026

Every implemented connector in this phase attaches to an endpoint the user has already started.
LOCALFORGE does not install an engine, download a model, start its server, or choose its accelerator.
The OS list in the catalog describes where the LOCALFORGE client can run; the serving endpoint can
run on a different host. All version entries below are synthetic version responses exercised by
local HTTP fixtures. Standard CI does not install or launch the upstream engines, so these entries
do not claim live compatibility with those binaries.

| Connector | Status and integration | LOCALFORGE host OS | Fixture version | Version, model, and metric behavior |
|---|---|---|---|---|
| TensorRT-LLM `trtllm-serve` | Implemented; attach-only OpenAI Chat Completions | Windows, Linux, macOS | `1.3` | Reads `/version`, `/health`, and `trtllm_*` Prometheus samples. |
| NVIDIA Triton + TensorRT-LLM backend | Planned; not callable | Linux | Not recorded | Triton uses model-specific `/v2/models/{name}/generate` routes. LOCALFORGE has no verified chat adapter for this API and does not report a working connector. Triton and backend versions remain unknown. |
| LMDeploy | Implemented; attach-only OpenAI-compatible API server | Windows, Linux, macOS | `0.18.0` | Version remains unknown unless a safe version route is explicitly configured. Reads `lmdeploy:*` Prometheus samples when exposed. |
| LightLLM | Implemented; attach-only OpenAI Chat Completions | Windows, Linux, macOS | `1.2.0` | Uses the documented chat route and health check. It does not claim version discovery or native metrics because no stable route is documented by the connector. |
| Hugging Face TGI | Implemented; attach-only OpenAI Messages API | Windows, Linux, macOS | `3.3.7` | Reads `/info`, `/health`, and `tgi_*` Prometheus samples. Catalog and UI label it maintenance mode; it is not recommended for a new managed installation. |
| LocalAI | Implemented; attach-only OpenAI-compatible API | Windows, Linux, macOS | `4.11.0` | Reads `/version` and `api_call*` metrics when authorized and enabled. A backend is stored as a separate layer only when the selected model configuration explicitly reports it. |
| KoboldCpp | Implemented; attach-only OpenAI Chat Completions | Windows, Linux, macOS | `1.122.1` | Reads `/api/extra/version`. It identifies KoboldCpp as the server and leaves the lower-level inference runtime unknown. Its JSON performance route is not misrepresented as Prometheus metrics. |

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
