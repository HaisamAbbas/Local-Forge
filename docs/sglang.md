# SGLang adapter

LOCALFORGE attaches to an already running SGLang OpenAI-compatible server. It does not install
SGLang, download weights, launch or stop the service, or choose a GPU. The default server port is
`30000`; use the server root URL such as `http://127.0.0.1:30000`. A URL already ending in `/v1`
is also accepted.

## Setup and use

1. Start SGLang yourself with its OpenAI-compatible API and a model already available to it.
2. In **Engines**, add the **SGLang serving endpoint** and enter the server URL.
3. Probe the profile, verify the listed model, then select it in a workload and run the benchmark.

The connector checks `/ready`, `/v1/models`, and `/v1/chat/completions`. It reads allowlisted model
metadata from `/model_info` when that route is available. Aggregate native metrics are optional;
the default route is `/metrics`, which SGLang exposes when metrics are enabled by the server
operator. Missing, disabled, unauthorized, malformed, or timed-out metrics do not prevent
client-side benchmarking.

SGLang usage in streaming requests is opt-in because `stream_options.include_usage` support has
varied by server version. Set `engine_extensions.sglang.include_usage` to `true` only when the
selected server supports it. With the option enabled, the probe sends one additional one-token
request to check for returned counts; with it disabled, usage support remains unknown and no usage
check request is sent. Unsafe server-info paths are rejected for every configurable endpoint route.
The connector also accepts `timeout_seconds`, `streaming_mode`,
`model_id`, and route overrides under `engine_extensions.sglang`. Route overrides must be local
paths without query strings, fragments, hosts, or traversal segments.

If the server requires an API key, save it in a supported operating-system credential manager and
put only its keyring reference in `secret_refs.api_key`. Do not put the key in the URL or profile
values.

## Runtime identity and metrics

SGLang's `/model_info` response is filtered through an allowlist before model metadata or a runtime
fingerprint is returned. The fingerprint includes only safe model fields, listed model IDs,
endpoint host and path, connector options, and a SHA-256 configuration fingerprint. It excludes
model and tokenizer paths and unrecognized fields.

The connector deliberately does not call `/server_info` or `/get_server_info`: those routes can
return full launch arguments containing API keys or other credentials. Server version therefore
remains unknown by default. An operator may set `engine_extensions.sglang.version_route` to a
separate, reviewed local endpoint that returns a simple version value; only the parsed version is
stored. GPU/device metadata remains unknown unless a future safe endpoint provides verified
details.

Prometheus samples keep their `sglang:` names, labels, type, component, and `HELP` definition.
Recognized units such as seconds, bytes, tokens, and requests are preserved; unknown units remain
`native`. Histogram buckets and counts use `observations`. Histogram series are not flattened into
normalized client latency. Malformed, non-finite, summary, untyped, and other-engine samples are
skipped with diagnostics where appropriate. SGLang's server-side metrics must be enabled separately
from LocalForge's client-observed metrics.

## Version and platform coverage

The implementation follows the official [SGLang observability](https://docs.sglang.ai/advanced_features/observability.html),
[production metrics](https://docs.sglang.io/docs/references/production_metrics),
[OpenAI-compatible protocol](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/entrypoints/openai/protocol.py),
and [HTTP server routes](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/entrypoints/http_server.py)
reviewed on 2026-10-13. Fixture tests use `0.4.9` as a synthetic version response from an
operator-selected `/version` route; that value does not mean a real SGLang release was installed or
live-tested. No live SGLang versions or platforms have been validated. The adapter is attach-only;
the endpoint's operating system, accelerator, and server configuration are controlled by the
operator.

Default tests use a local fixture, require no SGLang package, GPU, network access, or downloaded
weights. The live smoke test first probes an SGLang server you started yourself, then sends one
explicit short completion to an already-loaded model. With the test's default usage setting, this
means two short chat requests total. The test never installs SGLang or fetches model artifacts.

```powershell
$env:LOCALFORGE_SGLANG_INTEGRATION = "1"
$env:LOCALFORGE_SGLANG_BASE_URL = "http://127.0.0.1:30000"
$env:LOCALFORGE_SGLANG_MODEL_ID = "your-already-served-model"
$env:LOCALFORGE_SGLANG_CREDENTIAL_REF = "keyring://service/account"
Set-Location backend
uv run pytest tests/test_sglang_integration.py
```

The credential reference is optional. It must point to an existing key in a supported OS keyring.
