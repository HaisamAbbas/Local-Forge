# LOCALFORGE Inference Lab

LOCALFORGE is a local-first workbench for measuring and comparing LLM inference engines. The
dashboard and control API run on the evaluator's machine and bind to loopback during development.
Requests go from that machine to the selected inference engine. Remote endpoint probes require
explicit confirmation through the local API; remote inference requests use the configured endpoint.

The local UI, API, SQLite persistence, machine and engine profiles, workload builder, benchmark
runner, and run history are implemented. The adapter catalog includes a deterministic fake engine,
a generic attached OpenAI-compatible chat-completions adapter, an attached llama.cpp adapter, and
an attach-only Ollama adapter. The fake engine does not load a model and its timings are not
model-performance measurements. Real endpoint requests are sent by the evaluator machine's local
API to the configured endpoint. Ordinary checks do not download models or contact public endpoints.

## Requirements

- Python 3.12 or newer
- Node.js 22.12 or newer and npm
- `uv` for Python environment and lockfile management

The desktop browser UI is served at `http://127.0.0.1:5173`. Its development server proxies
`/health` and `/api/v1` requests to the FastAPI service at `http://127.0.0.1:8000`, so the browser
uses same-origin requests and the backend is not exposed on the LAN.

## Run locally

Install the Python dependencies once:

```powershell
Set-Location backend
uv sync
```

Start the API in one terminal:

```powershell
Set-Location backend
uv run uvicorn localforge.api.main:app --host 127.0.0.1 --port 8000
```

Install frontend dependencies and start the UI in another terminal:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The API health response is available at
`http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/api/v1/health`. The registered adapter
manifest catalog is available at `http://127.0.0.1:8000/api/v1/engines/catalog`.
The interactive API reference is available at `http://127.0.0.1:8000/docs`. Workloads can be saved
through `/api/v1/workloads` or `/api/v1/workloads/presets`; JSONL and CSV datasets are imported at
`/api/v1/datasets/import` using a multipart upload. Dataset prompt bodies are retained only when the
import explicitly opts in. Benchmark plans report blockers and warnings at
`/api/v1/benchmark-plans/preflight` and save the selected dataset snapshot hash with the plan.
Start a saved plan with `POST /api/v1/runs` and its `plan_id`; read run state at
`/api/v1/runs/{run_id}`, samples at `/api/v1/runs/{run_id}/samples`, metrics at
`/api/v1/runs/{run_id}/metrics`, and events at `/api/v1/runs/{run_id}/events`. Event history supports
sequence-cursor replay or Server-Sent Events at `/api/v1/runs/{run_id}/events/stream`. Cancel an active run with
`POST /api/v1/runs/{run_id}/cancel`. Resource collisions return `409`; retry with
`override_contention: true` only after reviewing the warning. Events never contain request bodies;
samples retain prompt and output text only when the saved plan explicitly enables retention.
Metric formulas, provenance, percentile minimums, and quality flags are documented in
[`docs/metrics.md`](docs/metrics.md).

### Connect an OpenAI-compatible endpoint

The endpoint must already be running and reachable from the machine running LocalForge. In
**Engines**, add **OpenAI-compatible endpoint**, set its API base URL (for example,
`http://127.0.0.1:1234/v1`), then probe it and choose a model reported by its models route. If the
endpoint has no compatible model-list route, enter its model ID in the profile. LocalForge sends
chat completions to the configured base URL's `chat/completions` route and uses server-sent events
when enabled and supported. Timeouts, retry count, streaming mode, and optional usage requests are
configurable in the profile.

An API key is optional. Store it in the operating system credential manager and enter a reference,
not the key itself: `keyring://<service>/<account>` looks up that service/account pair, while
`localforge-credential://<account>` looks up the `LocalForge` service. The adapter only resolves
credentials through a supported OS keyring backend. Localhost endpoints stay local; a non-local
endpoint requires explicit egress confirmation before probing, and the probe may send a minimal
one-token chat-completion check. Inference requests also go to the configured endpoint.

This connector treats the endpoint as generic and does not guess the server vendor, engine version,
or underlying model identity. An optional metrics route is checked for availability only; it is not
normalized into benchmark metrics. See the official [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events),
and [models list](https://developers.openai.com/api/reference/resources/models/methods/list) references
for the portable request and response shapes this adapter supports.

The real-endpoint smoke test is skipped by default. To opt in, set the following variables in
PowerShell and run only that test; it sends one short completion to the configured endpoint. The
credential reference is optional and should point to an existing OS keyring entry.

```powershell
$env:LOCALFORGE_OPENAI_COMPATIBLE_INTEGRATION = "1"
$env:LOCALFORGE_OPENAI_COMPATIBLE_BASE_URL = "http://127.0.0.1:1234/v1"
$env:LOCALFORGE_OPENAI_COMPATIBLE_MODEL_ID = "your-model-id"
$env:LOCALFORGE_OPENAI_COMPATIBLE_CREDENTIAL_REF = "keyring://service/account"
Set-Location backend
uv run pytest tests/test_openai_compatible_integration.py
```

### Connect llama.cpp / llama-server

In **Engines**, choose **llama.cpp / llama-server**. Attach mode connects to a server already
running on the local machine. Managed mode starts only an explicitly selected llama-server binary
with an existing GGUF file and working directory. LocalForge never downloads model weights.
Managed launch stays on 127.0.0.1, and GPU support depends on the backend compiled into the chosen
binary. See [the llama.cpp adapter guide](docs/llama-cpp.md) for supported probes and metrics, the
version-pinned upstream references, and an opt-in live smoke test.

### Connect local Ollama

Ollama must already be running on this machine, with the model already installed. In **Engines**,
choose **Ollama**, keep **Attached endpoint**, and use the local server URL (default
`http://127.0.0.1:11434`). Probe the server, then select one of the locally listed models in
**Workloads**. LOCALFORGE checks `/`, `/api/version`, and `/api/tags`; chat requests use
`/api/chat`. The adapter filters remote-backed entries from inventory and refuses to run a model it
cannot verify as local. It never calls Ollama's pull or delete operations, and it never starts or
stops an Ollama process.

The stored runtime version identifies the Ollama server. The underlying runner remains explicitly
unknown unless the server exposes verified runner details; model family or file format is not used
to infer it. Ollama's per-request duration fields are saved as raw engine-native nanosecond
observations. Aggregate Ollama metric scraping is unavailable. See the [Ollama adapter guide](docs/ollama.md)
for supported behavior and an opt-in smoke test.

The database is created in the platform's per-user LOCALFORGE application-data directory. To use a
different directory for local development, set `LOCALFORGE_DATA_DIR` before starting the API:

```powershell
$env:LOCALFORGE_DATA_DIR = Join-Path (Get-Location) ".localforge"
```

Dataset uploads are limited to 100 MiB by default. Set `LOCALFORGE_MAX_DATASET_UPLOAD_BYTES` to a
positive byte count to change the limit. Retained dataset snapshots are stored under the configured
LOCALFORGE data directory in `datasets/`.

The process boundary can be checked independently from the API:

```powershell
Set-Location backend
uv run localforge-worker --check
```

The worker can be checked independently and is also started automatically for each accepted run.

## Checks

From `backend/`:

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest
```

From `frontend/`:

```powershell
npm run format:check
npm run lint
npm run typecheck
npm run test
npm run build
npx playwright install chromium
npm run e2e
```

The browser smoke test starts the local API and Vite UI itself. It uses only local services and the
deterministic test fixture; no model, GPU, or public endpoint is required.
