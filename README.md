# LOCALFORGE Inference Lab

LOCALFORGE is a local-first workbench for measuring and comparing LLM inference engines. The
dashboard and control API run on the evaluator's machine and bind to loopback during development.
Requests will go to the selected inference engine. Remote endpoint probes require explicit
confirmation through the local API; real model requests and inference adapters are not implemented
yet.

The local UI shell, API, typed data contracts, SQLite persistence, and machine/engine profile APIs
are in place. The API supports deterministic workload definitions, bounded JSONL/CSV dataset
validation, optional hash-addressed local dataset retention, and benchmark-plan preflight. The
adapter catalog currently includes a deterministic fake engine for exercising the integration
boundary; it does not load a model or produce inference performance measurements. The dashboard is
not yet wired to these APIs. The workload screens, real engine adapters, and benchmark execution
arrive in subsequent implementation phases. Ordinary checks do not download models.

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

This foundation release does not yet schedule benchmark jobs in the worker.

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
