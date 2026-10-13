# Ollama adapter

LOCALFORGE connects to an Ollama server that is already running on the evaluator's machine. The
adapter is attach-only: it does not install Ollama, start or stop the service, pull models, or
delete models. The local API default is `http://127.0.0.1:11434`.

## Setup and use

1. Start Ollama yourself and install the model you want to evaluate using Ollama's own tooling.
2. In **Engines**, add **Ollama** with the attached endpoint at `http://127.0.0.1:11434` (or the
   local port you configured). LOCALFORGE only accepts loopback addresses for this adapter.
3. Probe the profile. The probe checks server health with `HEAD /`, reads the optional version from
   `GET /api/version`, and reads model inventory from `GET /api/tags`. It does not send a prompt.
4. In **Workloads**, select a model reported in the local inventory and run the benchmark.

Only model entries without remote-host metadata are exposed as locally runnable. Request preparation
rechecks the inventory and blocks an ID that is missing or remote-backed. If inventory cannot be
verified, the run is blocked and the probe reports that condition. The UI says that the model must
already be installed; there is no pull or delete control in LOCALFORGE.

## Request and measurements

Chat requests use `POST /api/chat` with one user message, workload sampling options, and the selected
model. Streaming uses Ollama's newline-delimited JSON response; disabling streaming requests one JSON
response. Both shapes normalize into the shared output, usage, completion, error, and timeout events.
Prompt/evaluation counts populate the run's token sample when Ollama returns both values.

The adapter stores `total_duration`, `load_duration`, `prompt_eval_duration`, and `eval_duration`
from the final chat response as separate raw `ENGINE_NATIVE` metrics with unit `ns`, the Ollama
server version, adapter identity, request ID, and workload phase. These values are server-reported
per-request observations; they are not client-observed latency and are not aggregated into a
Prometheus scrape. The regular run sample still records client-observed latency and time to first
token independently.

`GET /api/version` identifies the Ollama server layer. The runner is stored as `unknown` unless a
future API response provides verifiable runner identity. Model family, quantization, and file format
are preserved as reported metadata but never used to guess the runner. The adapter does not claim
server-side cancellation: LOCALFORGE can close its local request stream, but Ollama may continue
generation.

## Opt-in local smoke test

Default tests use only a loopback fixture. To send one short prompt to a preinstalled local model,
set the integration flag and exact model name in PowerShell. The test skips unless explicitly
enabled, refuses non-loopback endpoints, and never pulls or deletes a model.

```powershell
$env:LOCALFORGE_OLLAMA_INTEGRATION = "1"
$env:LOCALFORGE_OLLAMA_MODEL = "your-installed-model:tag"
$env:LOCALFORGE_OLLAMA_URL = "http://127.0.0.1:11434"
Set-Location backend
uv run pytest tests/test_ollama_integration.py
```

## API references

The adapter follows Ollama's official [API introduction](https://docs.ollama.com/api/introduction),
[chat endpoint](https://docs.ollama.com/api/chat), [model inventory](https://docs.ollama.com/api/tags),
[usage fields](https://docs.ollama.com/api/usage), and [version endpoint](https://docs.ollama.com/api/version).
Ollama documents local server health as `HEAD /` in its [official Go client](https://github.com/ollama/ollama/blob/main/api/client.go).
The local integration fixtures do not require Ollama, network access, or model files.
