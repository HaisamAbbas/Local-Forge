<p align="center"><img alt="LocalForge icon" height="96" src="frontend/public/icon-512.png" width="96" /></p>

# LOCALFORGE Inference Lab

LOCALFORGE is a local-first workbench for measuring and comparing LLM inference engines. The
dashboard and control API run on the evaluator's machine and bind to loopback during development.
Requests go from that machine to the selected inference engine. Remote endpoint probes require
explicit confirmation through the local API; remote inference requests use the configured endpoint.

The local UI, API, SQLite persistence, machine and engine profiles, workload builder, benchmark
runner, and run history are implemented. The adapter catalog covers llama.cpp, Ollama, vLLM,
SGLang, TensorRT-LLM, LMDeploy, LightLLM, TGI, LocalAI, KoboldCpp, MLX-LM, MLC LLM, mistral.rs,
OpenVINO GenAI, ONNX Runtime GenAI, NVIDIA Dynamo, and a generic OpenAI-compatible endpoint. Triton
with TensorRT-LLM and ExLlamaV3 are cataloged as planned and unavailable. The deterministic fake
engine is for workflow tests only: it does not load a model and its timings are not
model-performance measurements. Real endpoint requests are sent by the evaluator machine's local
API to the configured endpoint. Ordinary checks do not download models or contact public endpoints;
a model is downloaded only when the user confirms **Pull with Ollama** on the Models screen.

## Install and run (users)

Users do not need this repository, Node.js, or a Python setup. With
[uv](https://docs.astral.sh/uv/) installed and an engine such as Ollama running with a model:

```powershell
uvx localforge-inference-lab
```

That starts LocalForge on `http://127.0.0.1:8000` (or a free port) and opens it in the browser.
`uv tool install localforge-inference-lab` keeps a `localforge` command installed instead. Options:
`--port`, `--data-dir`, `--no-browser`, `--version`. Before the package is on PyPI, point uv at a
release wheel: `uvx --from <path-or-URL-to-wheel> localforge`.

## Benchmark from a script or CI

`localforge bench` runs a plan without the browser. It starts a private LocalForge on a free
loopback port, runs the plan, prints the measurements as JSON, and stops it again.

```json
{
  "label": "llama.cpp regression check",
  "engine": { "adapter": "localforge.llama_cpp", "endpoint": "http://127.0.0.1:8080" },
  "model": "*",
  "workload": { "preset": "mixed_chat", "requests": 20, "concurrency": 1, "max_output_tokens": 128 }
}
```

```powershell
localforge bench plan.json --out result.json                      # record a baseline
localforge bench plan.json --baseline result.json --tolerance 10  # fail on a >10% regression
```

Exit codes: `0` the run completed and stayed within tolerance, `1` it failed or regressed (a
metric the baseline had but the run lacks also fails), `2` the plan or arguments are invalid.
Latency metrics may not rise, and throughput may not fall, by more than `--tolerance` percent.
Shared or battery-powered machines are noisy; gate on a dedicated, idle machine.
`--api URL` uses an already running LocalForge instead of starting one.

## Tune a llama.cpp launch

`localforge tune` finds launch settings that suit this machine and model. Give it a plan for a
managed llama.cpp (`executable_path` and a `.gguf` `model_path`); it starts the server with the
engine's defaults, then changes one setting at a time (CPU threads, then GPU layers if a GPU is
present, then batch size), running the same short benchmark each time. A change is kept only if it
beats the current best by a margin (5% by default), so noise does not pick the winner.

```json
{
  "label": "tune my llama.cpp",
  "engine": {
    "adapter": "localforge.llama_cpp",
    "mode": "managed_process",
    "endpoint": "http://127.0.0.1:9931",
    "extensions": { "llama_cpp": { "executable_path": "D:/engines/llama-server.exe", "model_path": "D:/models/model.gguf" } }
  },
  "tune": { "objective": "throughput", "threads": [4, 8, 12], "min_gain_percent": 5 }
}
```

```powershell
localforge tune plan.json --out tuned.json
```

`objective` is `throughput` (generation speed, the default), `latency` (time to finish a request)
or `ttft` (time to the first token). The result lists every trial and the recommended launch
settings. The same tuner is on each managed llama.cpp card in **Engines** ("Tune launch settings"): it runs on a private copy, shows progress, can be stopped, and only changes the profile when you click **Apply these settings**. Close other programs first: on a busy machine the trials are noisy. Exit codes: `0`
completed, `1` the baseline run failed, `2` the plan is invalid.

## Build a release

```powershell
python scripts/build_release.py
```

This builds the UI, bundles it into the Python package, and writes the wheel and source archive to
`backend/dist/`. Publish them with `uv publish` (PyPI token required) or attach the wheel to a
GitHub Release. Set `LOCALFORGE_WEB_DIR=frontend/dist` to serve a local UI build from a development
API without packaging.

## Requirements (development)

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
Delete a finished run with `DELETE /api/v1/runs/{run_id}` (`409 run_active` while it is still
active), or preview and delete finished runs in bulk with `POST /api/v1/runs/cleanup`
(`before`, `statuses`, `keep_latest`; `dry_run` defaults to `true`). Repeat a plan with optional
changes through `POST /api/v1/benchmark-plans/{plan_id}/clone`; workload changes copy the workload,
so the source plan and workload are never edited. `GET /api/v1/models` lists model artifacts across
engine profiles. Saved reports are listed at `GET /api/v1/reports`, downloaded at
`/api/v1/reports/{report_id}/download`, and removed with `DELETE /api/v1/reports/{report_id}`.
Use-case presets (`interactive_chat`, `coding_assistant`, `document_qa`, `batch_summarization`,
`long_context_stress`, `quality_smoke`) are listed with their descriptions and default targets at
`GET /api/v1/workload-presets/use-cases` and created through `/api/v1/workloads/presets`. A plan
saves optional `targets` (omitted: the use-case defaults; `null`: none) and they can be replaced with
`PUT /api/v1/benchmark-plans/{plan_id}/targets`. `GET /api/v1/runs/{run_id}/verdict` evaluates a run
against the targets captured when it started: each target is `pass`, `fail`, or `unknown` (missing
metric or too few samples, never treated as a pass or zero), with plain-language explanations that
quote the run's own numbers. `GET /api/v1/prefill-estimate?engine_profile_id=&model_artifact_id=&input_tokens=`
estimates first-token time from prompt-processing rates measured in earlier completed runs. Workloads
may opt in to `quality_checks` (exact match against dataset expected answers, valid JSON with optional
required keys, stopped before the token limit, maximum length, contains / does not contain); outputs
are checked in memory and only the outcome and reason codes are stored unless retention is enabled.
Metric formulas, provenance, percentile minimums, and quality flags are documented in
[`docs/metrics.md`](docs/metrics.md).
Local API protection, data retention, path handling, process ownership, and recovery limits are
documented in [`docs/security-and-recovery.md`](docs/security-and-recovery.md).

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
cannot verify as local. The adapter never calls Ollama's pull or delete operations, and it never
starts or stops an Ollama process.

### See which models fit this hardware

**Hardware fit** detects this machine (processor and cores, system memory, graphics and how much
memory models can use) and grades every catalog model for it. For each model it picks the best
quantization the machine can hold (Q8_0 down to Q2_K, using Q3 or Q2 only when nothing better
fits), says where it would run (GPU memory, GPU plus system RAM, or CPU and RAM), and rates it
*Perfect fit*, *Good fit*, *Marginal* or *Too large*. Filter by use, by what runs well, by what is
already downloaded, or search by name. **Detect again** rescans the hardware.
`GET /api/v1/hardware-fit` returns the same data (`?refresh=true` rescans). Speeds are estimates, not
benchmarks, and the grade assumes typical memory speed until you have benchmarked a model here. See
[`docs/hardware-fit.md`](docs/hardware-fit.md) for the rules.

### Choose a model

**Models** opens with "Suggested for this machine": a curated catalog of well-known open models
(Qwen, Llama, Gemma, Phi, Mistral, DeepSeek-R1 distills, SmolLM2 and coding models, 0.5B-70B),
ranked per use case (chat, coding, document Q&A, small and fast, reasoning) by memory fit, estimated
speed on this machine, and what is already downloaded. Speeds are estimates: generation speed is
roughly memory read speed divided by model size, measured from your completed runs when there are
any, otherwise a rough GPU-bandwidth figure or unknown. The catalog never claims quality scores.
`GET /api/v1/models/suggestions?use_case=chat` returns the same ranking, and `localforge recommend`
prints the top three for chat and coding.

**Pull with Ollama** is the only way LocalForge starts a download, and only after you confirm the
download size in the page: `POST /api/v1/models/pull` (`engine_profile_id`, `ollama_tag`,
`confirm: true`) asks your local, enabled Ollama profile to pull the tag; the network download to
`registry.ollama.ai` is made by Ollama. Progress is at `GET /api/v1/models/pulls/{id}`, cancel with
`POST /api/v1/models/pulls/{id}/cancel`, and the engine is re-probed afterwards so the model appears
in its inventory. For llama.cpp and KoboldCpp, **Get the GGUF** links to the Hugging Face file. See
[`docs/model-catalog.md`](docs/model-catalog.md) for the catalog format and ranking rules.

The stored runtime version identifies the Ollama server. The underlying runner remains explicitly
unknown unless the server exposes verified runner details; model family or file format is not used
to infer it. Ollama's per-request duration fields are saved as raw engine-native nanosecond
observations. Aggregate Ollama metric scraping is unavailable. See the [Ollama adapter guide](docs/ollama.md)
for supported behavior and an opt-in smoke test.

### Connect vLLM or SGLang

Both serving-engine connectors attach to a server that is already running; LOCALFORGE does not
install the engine, download weights, launch or stop the process, or infer GPU selection. Add
**vLLM serving endpoint** or **SGLang serving endpoint** under **Engines**, enter its server root URL,
probe it, and select one of its reported models in a workload. The default ports are `8000` for
vLLM and `30000` for SGLang. Optional native Prometheus samples retain their engine names and
definitions and remain separate from client-measured benchmark results. Missing native metrics do
not block a client benchmark.

See the [vLLM adapter guide](docs/vllm.md) and [SGLang adapter guide](docs/sglang.md) for route,
credential, metric, version-discovery, and platform coverage details. Neither connector has been
live-tested against an installed serving engine or GPU; default checks use local fixtures only.

Prompt 15B adds attach-only TensorRT-LLM, LMDeploy, LightLLM, TGI, LocalAI, and KoboldCpp
connectors. NVIDIA Triton with the TensorRT-LLM backend remains planned because its documented
model-specific API is not the chat API currently supported by the adapter boundary. See the
[connector support matrix](docs/connector-support-matrix.md) for every catalog entry, connection
modes, host OS declarations, test evidence, maintenance status, and known limits. Adapter
implementors should use the [adapter authoring guide](docs/adapter-authoring.md) for manifest,
capability, fixture, version evidence, privacy, and release requirements.

The database is created in the platform's per-user LOCALFORGE application-data directory. To use a
different directory for local development, set `LOCALFORGE_DATA_DIR` before starting the API:

```powershell
$env:LOCALFORGE_DATA_DIR = Join-Path (Get-Location) ".localforge"
```

**Settings** (`GET`, `PUT`, or `PATCH /api/v1/settings`) stores workspace preferences in the local
database: the request timeout applied to newly saved benchmark plans, the warmup count and
prompt/output retention that prefill the workload builder, and whether test fixture engines are
shown. The deterministic fake engine is hidden from the engine catalog and the plan builder by
default, and plans or runs that use it are blocked until **Show test fixture engines** is enabled.
Existing runs that used it remain readable. Set `LOCALFORGE_SHOW_TEST_FIXTURE_ENGINES=1` to make
fixtures visible by default before the setting has been saved (the browser test suite does this).
The same response reports the data directory and its free and total disk space, with a warning
below 1 GiB free.

**System** detection lists NVIDIA GPUs through `nvidia-smi` and every display adapter through the
operating system (Win32_VideoController on Windows, `lspci` on Linux, `system_profiler` on macOS).
Integrated GPUs are labeled as using shared system memory and report no memory size; dedicated
memory is shown only when the driver reports a 64-bit value (Windows `HardwareInformation.qwMemorySize`)
or `nvidia-smi` reports it. The Windows processor name comes from the registry, and physical cores
are reported when the operating system exposes them.

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
