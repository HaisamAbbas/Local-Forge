# vLLM adapter

LOCALFORGE attaches to an already running vLLM OpenAI-compatible server. It does not install
vLLM, download model weights, launch or stop a server, or select a GPU. The default server port is
`8000`; use the server root URL such as `http://127.0.0.1:8000`. A URL already ending in `/v1` is
also accepted.

## Setup and use

1. Start vLLM yourself with its OpenAI-compatible API server and a model already available to it.
2. In **Engines**, add the **vLLM serving endpoint** and enter the server URL.
3. Probe the profile, verify the listed model, then select it in a workload and run the benchmark.

The connector checks `/health`, `/version`, `/v1/models`, and the Chat Completions route. It sends
chat requests to `/v1/chat/completions`. The default optional metrics route is `/metrics`; expose it
through the vLLM server's metrics support. A missing, disabled, unauthorized, malformed, or
timed-out metrics route does not prevent client-side benchmarking. Native metrics stay separate
from LocalForge client-observed latency and throughput.

The probe sends a separate one-token usage check in addition to checking streaming. The default
vLLM stream request asks for usage counts, so that check verifies whether the server returns token
counts for the configured model. A profile may set
`engine_extensions.vllm.include_usage` to `false` if a server rejects that option. The connector
also supports `timeout_seconds`, `streaming_mode`, `model_id`, and route overrides under
`engine_extensions.vllm`. Route overrides must be local paths without query strings, fragments,
hosts, or traversal segments.

If the server requires an API key, save it in a supported operating-system credential manager and
put only its keyring reference in `secret_refs.api_key`. Do not place the key in the endpoint URL or
profile values. vLLM's API key support does not necessarily protect non-API routes such as health,
version, or metrics; restrict those routes at the server or network boundary according to the
server's configuration.

## Runtime identity and metrics

The server version is read from `/version`. The fingerprint records the reported version, endpoint
host and path, configured model and connector options, listed model IDs, and a SHA-256 configuration
fingerprint. vLLM has no separate metadata route enabled by this connector, so device details and
other private launch arguments remain unknown. The connector never infers the selected GPU from the
model name or endpoint.

Prometheus samples are retained with their `vllm:` names, labels, type, component, and `HELP`
definition. Known units such as seconds, bytes, tokens, and requests are preserved; unknown units
are labeled `native`. Histogram buckets and counts use the `observations` unit. Histogram bucket,
sum, and count series are not collapsed into a client-side latency metric. Malformed, non-finite,
summary, untyped, and other-engine samples are skipped with diagnostics where appropriate.

## Version and platform coverage

The implementation follows the official [vLLM serving API](https://docs.vllm.ai/en/latest/serving/online_serving/),
[metrics reference](https://docs.vllm.ai/en/latest/usage/metrics/), and
[launch argument reference](https://docs.vllm.ai/en/stable/api/vllm/entrypoints/launchers/cli_args/)
reviewed on 2026-10-13. Fixture tests use `0.10.2` as a synthetic `/version` response; that value
does not mean a real vLLM release was installed or live-tested. No live vLLM versions or platforms
have been validated. The adapter is attach-only and can reach any endpoint permitted by the existing
LocalForge endpoint policy; server/GPU behavior depends on the deployment selected by the operator.

Default tests use a local fixture, require no vLLM package, GPU, network access, or downloaded
weights. The live smoke test first probes a vLLM server you started yourself, then sends one explicit
short completion to an already-loaded model. The default probe makes two one-token chat requests
(streaming and usage checks), so the test can generate up to three short completions total. It never
installs vLLM or fetches model artifacts.

```powershell
$env:LOCALFORGE_VLLM_INTEGRATION = "1"
$env:LOCALFORGE_VLLM_BASE_URL = "http://127.0.0.1:8000"
$env:LOCALFORGE_VLLM_MODEL_ID = "your-already-served-model"
$env:LOCALFORGE_VLLM_CREDENTIAL_REF = "keyring://service/account"
Set-Location backend
uv run pytest tests/test_vllm_integration.py
```

The credential reference is optional. It must point to an existing key in a supported OS keyring.
