# LOCALFORGE Inference Lab implementation plan

**Status:** Prompts 0 through 16 are implemented and verified; Prompt 17 is next.
**Reviewed:** 13 October 2026
**Product and measurement source of truth:** `ImplementationPlan/LOCALFORGE-Inference-Lab-Implementation-Spec-v1.0.md`  
**Implementation sequence:** `ImplementationPlan/LOCALFORGE-Inference-Lab-Codex-Implementation-Prompt-Pack-v1.0.md`

## Repository reconnaissance

At the start of Prompt 0, the workspace contained only `ImplementationPlan/` with the two source Markdown documents and eleven conceptual PNGs listed below. The current application foundation was added in Prompt 1. This workspace still has no Git metadata: `git status --short --branch` returned “fatal: not a git repository”; there is no current branch or tracked/untracked status to report. No pre-existing files were overwritten or discarded.

No `AGENTS.md` was found at `D:\AGENTS.md`, `D:\Local-Forge\AGENTS.md`, or `D:\Local-Forge\ImplementationPlan\AGENTS.md`.

### Exact project files inspected

- `ImplementationPlan/LOCALFORGE-Inference-Lab-Implementation-Spec-v1.0.md`
- `ImplementationPlan/LOCALFORGE-Inference-Lab-Codex-Implementation-Prompt-Pack-v1.0.md`
- `ImplementationPlan/Conceptual-Images/Completed run metrics dashboard.png`
- `ImplementationPlan/Conceptual-Images/KV Cache Learning Lab.png`
- `ImplementationPlan/Conceptual-Images/Local model inventory workspace.png`
- `ImplementationPlan/Conceptual-Images/LocalForge Add Engine Wizard.png`
- `ImplementationPlan/Conceptual-Images/Localforge benchmark workload setup screen.png`
- `ImplementationPlan/Conceptual-Images/LocalForge Compare experiments dashboard.png`
- `ImplementationPlan/Conceptual-Images/LOCALFORGE Engines Dashboard.png`
- `ImplementationPlan/Conceptual-Images/LocalForge inference benchmark in progress.png`
- `ImplementationPlan/Conceptual-Images/LocalForge Inference Lab Overview.png`
- `ImplementationPlan/Conceptual-Images/LocalForge Machine Profile Dashboard.png`
- `ImplementationPlan/Conceptual-Images/Privacy controls in Localforge Settings.png`

The conceptual images use sample values and multiple navigation variants. Treat them as visual references; the product specification governs behavior, terminology, and the requirement to label illustrative data.

### Environment and available commands

The current developer shell is Windows PowerShell 5.1. Available tools and observed versions:

| Tool | Observed version / status |
|---|---|
| Python | 3.12.10 |
| Node.js | 25.2.1 |
| npm | 11.6.2 |
| uv | 0.12.17 |
| Ruff | 0.16.6 |
| pytest | 9.1.1 |
| Docker | 29.7.2 |
| Git | Installed, but this workspace is not a Git repository |
| pnpm | Not installed |

Prompt 1 established the first validation commands and CI configuration below. Commands have been run on this workspace; Prompt 1 results are recorded at the end of this document.

## Architecture and mapping to the product specification

No application architecture existed at initial reconnaissance. Prompt 1 now implements the starter architecture described here: a local-first React/TypeScript frontend, Python FastAPI/Pydantic control plane, isolated benchmark worker, and SQLite persistence. Keep implementation small and typed; add engine-specific behavior behind adapters rather than separate UI screens.

| Area | Specification direction | Initial implementation mapping |
|---|---|---|
| Web UI | React and TypeScript, desktop-first, accessible charts/tables | `frontend/` with React, TypeScript, Vite, component tests, and Playwright E2E |
| Local control plane | FastAPI, Pydantic, versioned `/api/v1` | `backend/src/localforge/api/`; bind to `127.0.0.1` by default |
| Shared contracts | Typed, versioned API and adapter schemas | Pydantic models in `backend/src/localforge/core/models.py` are the backend source of truth; API request/response schemas will come directly from them, with frontend types generated or contract-checked from OpenAPI when domain routes are added |
| Adapter boundary | Capability-aware adapters for attached, managed, library, and composed targets | `backend/src/localforge/adapters/`; begin with a deterministic fake adapter and shared contract tests |
| Long-running work | Isolated worker process; run lifecycle, events, cancellation, and resource locks | `backend/src/localforge/worker/` invoked independently of an HTTP request; coordinator persists state and events |
| Persistence | SQLite WAL for metadata; raw samples retained before aggregation | `backend/src/localforge/storage/`, versioned forward-only migrations, temporary DB support; JSONL for high-volume raw samples initially |
| Privacy | Local by default; explicit remote egress; secrets and content redacted by default | Loopback API, secret references rather than plaintext config, remote-target disclosure before sending requests, opt-in content retention |
| Documentation | Accurate setup, support status, methods, and limitations | `README.md`, `docs/`, and a connector support matrix added as implementation work begins |

Target layout, with Prompt 1 foundations implemented and domain/adapter modules reserved for later phases:

```text
backend/
  .python-version
  pyproject.toml
  src/localforge/
    api/          # FastAPI routes and application startup
    core/         # typed domain models, settings, errors
    adapters/     # adapter manifest, capability model, fake adapter
    storage/      # SQLite connection, migrations, repositories
    worker/       # independent benchmark worker entry point
  tests/
frontend/
  package.json
  src/            # React app, API client, screens, components
  e2e/            # deterministic browser workflows
docs/
```

Use SQLite from the standard library or a thin async-compatible wrapper unless a concrete requirement justifies a heavier ORM. Keep request samples and event payloads free of prompt/output bodies by default. Do not add charting, telemetry, model-download, or cloud dependencies before a feature needs them.

## Ordered implementation phases

Follow the prompt pack in one conversation and the same workspace. Prompt 0 is complete. Do not skip prerequisite phases.

| Prompt | Phase | Outcome |
|---|---|---|
| 1 | Project foundation and runnable shell | React shell, FastAPI health endpoint, SQLite migration startup, worker entry point, local setup, initial CI checks |
| 2 | Domain contracts, persistence, and migrations | Versioned schemas and repositories for machines, engines, models, workloads, plans, runs, samples, experiments, and reports |
| 3 | Adapter contract and deterministic fake engine | Capability states, adapter manifest, fake streaming/error/cancellation behaviors, reusable contract tests |
| 4 | Machine inspection and engine registry | Provenance-aware system profile; profile CRUD, safe probes, local security and credential handling |
| 5 | Workloads, dataset import, and preflight | Deterministic presets/import, validation, snapshots/hashes, warnings and blockers |
| 6 | Run coordinator, worker lifecycle, locks, and events | Separate worker execution, persisted state/events, reconnect/replay, cancellation and lock recovery |
| 7 | Metrics and measurement integrity | Raw request samples, formulas, source labels, native metrics, aggregation and uncertainty warnings |
| 8 | Overview, machines, and engines UI | Real API-backed machine and engine flows with accessible unknown/error states |
| 9 | Workload builder and plan review UI | Create, validate, review, save, and replay workload plans from the UI |
| 10 | Live runs, history, and run detail | Follow fake-engine runs, inspect persisted metrics/events, cancel safely, recover after reconnect |
| 11 | Generic OpenAI-compatible endpoint | Version-verified attached endpoint adapter with local mock fixtures and remote-egress disclosure |
| 12 | llama.cpp / llama-server adapter | Attach first; managed launch only where safely version-tested and process-owned |
| 13 | Ollama adapter | Attach, model inventory and streaming; never pull/delete models implicitly |
| 14 | Comparisons, experiments, and exports | Comparability grades, controlled variants, reproducible JSON/CSV/Markdown reports |
| 15A | vLLM and SGLang | Attach adapters first, optional version-tested managed Linux/CUDA profiles |
| 15B | Additional serving and wrapper connectors | TensorRT-LLM, Triton, LMDeploy, LightLLM, TGI, LocalAI, and KoboldCpp with truthful status |
| 15C | Platform, library, and distributed connectors | MLX-LM, MLC, OpenVINO GenAI, ONNX Runtime GenAI, Mistral.rs, ExLlamaV3, and Dynamo |
| 16 | Ecosystem catalog and adapter contribution workflow | Full catalog/support matrix and fixture-backed adapter author guidance |
| 17 | Security, privacy, recovery, and edge-case hardening | Code-driven hardening of the requirements in spec sections 12–14 and 17 |
| 18 | Full E2E, release gate, and handoff | Deterministic ten-step browser workflow, complete local release gate, and evidence-based handoff |

Prompts 15A–15C expand the ecosystem after the MVP adapters and are performed in that order. Before each real connector, check its current official documentation and record the tested version. A catalog entry alone is not a claim of working support.

Prompts 0 through 16 are complete. Prompt 17 is next.

## First usable vertical slice

Build and verify a deterministic fake-engine workflow before requiring any real inference engine:

1. Start the UI, local API, and independent worker using documented commands.
2. Inspect a machine profile and register/probe the fake engine.
3. Create a deterministic preset workload, review preflight, and save a plan.
4. Run it through the worker; persist run state, events, request outcomes, raw timings, and normalized metrics.
5. Follow progress in the UI, cancel a second run, reconnect, and inspect both terminal records without fabricated measurements.

Prompts 1–10 deliver this vertical slice. It proves the application path and CI behavior without model downloads, GPUs, or public endpoints. The first useful real-engine MVP then adds the generic OpenAI-compatible, llama.cpp, and Ollama paths in Prompts 11–13, followed by comparison and reproducible export in Prompt 14.

Keep the following out of the first vertical slice: production-serving breadth (15A–15C), distributed topology, library pipelines, low-level KV-cache/kernel learning labs, broad model discovery/download, quality evaluation, cloud hosting, and public leaderboards. Implement baseline privacy, loopback binding, secret redaction, and process ownership as their foundational phases require; Prompt 17 is a further hardening pass, not a reason to defer those basics.

## Validation commands and CI gates

Prompt 1 established the following commands and CI gates. They assume `backend/pyproject.toml` and `frontend/package.json` from the initial layout.

Backend, from `backend/`:

```powershell
uv sync --locked
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest
```

Frontend, from `frontend/`:

```powershell
npm ci
npm run format:check
npm run lint
npm run typecheck
npm run test
npm run build
npm run e2e
```

The E2E setup must use the deterministic fake adapter and a preinstalled Playwright browser in CI. Live-engine checks are opt-in; default checks must not download weights, require accelerators, call public endpoints, or depend on a real engine. CI should run backend formatting/lint/type/unit/integration/adapter checks, frontend formatting/lint/type/unit/build checks, and browser E2E from a clean checkout. Add security-relevant negative tests with the behavior they protect. There is no existing security tool to preserve or report.

Prompt 0 itself did not run project checks because no project code existed then. Prompt 1 ran these checks; see the completion record below.

## Constraints, assumptions, and risks

- **Execution model:** the evaluator installs and runs LOCALFORGE on their own machine. The dashboard and control plane stay local by default; the selected inference engine normally runs on that machine. A remote engine is an explicit user-configured target, with a prompt/data egress notice before requests are sent. The developer's machine is not a shared model host.
- **Developer environment:** implementation runs on Windows PowerShell with Python 3.12 and Node/npm. `backend/.python-version` selects Python 3.12 for repeatability. The product spec calls for Windows, Linux, and macOS support; adapter support remains platform-specific and must be labeled honestly.
- **Initial Git baseline:** this workspace began without Git metadata. A repository is being initialized on `main` for publication; the GitHub remote was empty at first inspection.
- **Source path in prompt pack:** the pack refers to `/workspace/scratch/...` for the specification. That path is absent here; the local source is under `ImplementationPlan/` and should be used as the authority.
- **Engine variability:** API behavior, metric definitions, launch flags, and supported versions change. Verify official docs and fixtures at each adapter phase; keep unknown capabilities unknown.
- **Measurement integrity:** ensure client and engine metrics remain distinct, use monotonic durations, preserve samples before aggregation, and do not imply causality from uncontrolled comparisons.
- **Security and privacy:** local-first defaults still require origin protection, credential handling, subprocess ownership, path validation, and deliberate remote egress. Build these into their owning phases.
- **Mockup consistency:** navigation labels differ among references. Use the spec's information architecture and prioritize accessible, working flows over exact pixel imitation.
- **Dependency and browser setup:** package manifests and lockfiles were introduced in Prompt 1. Keep dependencies modest and use npm; pnpm was unavailable during reconnaissance.

## Prompt 0 completion record

- Read both source documents and reviewed all eleven conceptual images.
- Inspected the workspace root, searched for project files, checked Git status, checked applicable `AGENTS.md` paths, and enumerated available development tools.
- Created the initial reconnaissance plan only; Prompt 1 implementation is recorded separately below. No existing user work was discarded.
- Unresolved constraint: the workspace has no Git repository, so branch and working-tree status cannot be established.

## Prompt 1 completion record

### Delivered

- Added the React/TypeScript/Vite dark workspace shell with working hash navigation for Overview, Engines, Workloads, Runs, Experiments, and Learning.
- Added the FastAPI app with `/health` and `/api/v1/health`, loopback development defaults, and a Vite same-origin proxy.
- Added SQLite WAL startup, bounded busy waits, `PRAGMA user_version` migration infrastructure, schema version 1, and a safe refusal to open a newer schema.
- Added an independently invokable `localforge-worker --check` process entry point and deterministic fake-engine test fixture.
- Added frontend API unavailable/recovery handling, accessible status updates, unit tests, Playwright smoke coverage, GitHub Actions CI, `.gitignore`, and local setup instructions.
- Kept the UI free of sample metrics and third-party network-loaded assets. Engine registration and benchmark execution remain for later phases.

### Checks run

Backend, from `backend/`:

- `uv sync --locked` — passed.
- `uv run --locked ruff format --check src tests` — passed.
- `uv run --locked ruff check src tests` — passed.
- `uv run --locked mypy src` — passed (8 source files).
- `uv run --locked pytest` — passed (6 tests).

Frontend, from `frontend/`:

- `npm run format:check` — passed.
- `npm ci` — passed from the committed frontend lockfile.
- `npm run lint` — passed.
- `npm run typecheck` — passed.
- `npm run test` — passed (2 tests).
- `npm run build` — passed.
- `npm run e2e` — passed (1 Chromium smoke test covering startup, navigation, reload, and API status).

### Limitations and next phase

- The worker is a tested process boundary only; job scheduling and benchmark execution arrive in Prompt 6.
- Engine and workload screens are honest empty states until their API-backed phases; no real engine is connected yet.
- The app was validated locally on Windows. GitHub Actions is configured on Ubuntu; broader OS and connector-specific coverage remains future work.
- The workspace still lacks Git metadata, so changes cannot be summarized as a branch diff.
- Prompt 2 now provides domain contracts, persistence repositories, and migrations; see the completion record below.

## Prompt 2 completion record

### Delivered

- Added versioned Pydantic contracts for machine and engine profiles, capability snapshots, model artifacts, datasets, workloads, benchmark plans and runs, request and metric samples, experiment groups, and report metadata.
- Added explicit lifecycle and capability enums, stable UUIDs, UTC timestamps, nullable unknown hardware/engine values, metric units and source labels, and a separate lowercase namespace for engine-owned extension data.
- Kept credentials in secret references instead of engine config; request/output bodies require both a retention marker and an explicit repository opt-in. Reference-only and redacted-summary samples remain supported.
- Added schema migration 2 with typed domain-record storage and dedicated raw request/metric sample tables. Sample writes require a persisted benchmark run; raw request samples are stored before any aggregate layer exists.
- Added typed SQLite CRUD repositories, caller-owned connections, bounded list sizes, parameterized SQL, atomic transactions, clear duplicate/not-found errors, explicit retained-content clearing, and a run deletion path that removes samples atomically. Generic run deletion is rejected to prevent orphaned samples.
- Documented Pydantic as the future API/OpenAPI contract source. Frontend domain types are not duplicated while domain routes do not yet exist.
- Added validation, CRUD, rollback, migration-upgrade/retry, raw metric preservation, retention, and run/content deletion tests.

### Checks run

Backend, from `backend/`:

- `uv run --locked ruff format --check src tests` — passed.
- `uv run --locked ruff check src tests` — passed.
- `uv run --locked mypy src` — passed (11 source files).
- `uv run --locked pytest` — passed (21 tests).

Frontend, from `frontend/`:

- `npm run format:check` — passed.
- `npm run lint` — passed.
- `npm run typecheck` — passed.
- `npm run test` — passed (2 tests).
- `npm run build` — passed.
- `npm run e2e` — passed (1 Chromium smoke test).

### Limitations and next phase

- These records are persistence contracts; user-facing CRUD APIs and dashboard workflows arrive in later prompts.
- Raw samples are kept in SQLite for the first workflow; high-volume JSONL/Parquet export and aggregate computation remain future work.
- Prompt 3 is now complete; see its completion record below.

## Prompt 3 completion record

### Delivered

- Added a versioned adapter manifest and async protocol covering configuration validation, probe, start/stop, model inventory, request preparation and streaming, abort, native metrics, runtime fingerprint, and close.
- The shared catalog registers adapters by stable ID, lists manifests for the API, rejects incompatible protocol versions, checks capability and metric claims, and reports missing adapters with readable diagnostics.
- Kept attached endpoint, managed process, library pipeline, and composed serving-stack modes distinct in the shared contracts. Capability values retain supported, unsupported, unknown, and user-supplied states; the fake manifest declares its capability vocabulary.
- Added the deterministic fake adapter with gated first output and chunk delivery, multi-chunk streams, error/disconnect/timeout outcomes, cancellation, optional usage/native metrics, probe failures, and process ownership checks. It does not launch an operating-system process or load a model.
- Added an adapter call runner that schedules async probes and streams in tracked tasks, cleans up on early stream close, and cancels outstanding calls on shutdown.
- Exposed the registered manifests at `/api/v1/engines/catalog` and added a reusable adapter contract helper for future connector tests.
- Added tests for manifest validation, config diagnostics, capability claim rejection, missing/old adapters, stream outcomes, cancellation, early close, optional metrics, event-loop responsiveness, process ownership, and the API catalog.

### Checks run

Backend, from `backend/`:

- `uv run --locked ruff format --check src tests` — passed.
- `uv run --locked ruff check src tests` — passed.
- `uv run --locked mypy src` — passed (17 source files).
- `uv run --locked pytest` — passed (41 tests).

Frontend, from `frontend/`:

- `npm run format:check` — passed.
- `npm run lint` — passed.
- `npm run typecheck` — passed.
- `npm run test` — passed (2 tests).
- `npm run build` — passed.
- `npm run e2e` — passed (1 Chromium smoke test).

### Limitations and next phase

- The fake adapter exercises LOCALFORGE's connector boundary; it produces no real inference measurements. The dashboard remains an empty workspace until the API-backed UI phases.
- Adapters that perform blocking library inference must run in an isolated worker process when that execution phase is implemented; the current runner isolates async adapter calls from other API work.
- Prompt 4 machine inspection, profile API, and local security work is underway; see the current progress and limitations below.

## Prompt 4 progress record

### Delivered so far

- Added best-effort OS, CPU, memory, and NVIDIA metadata inspection with field provenance and explicit unknown values; added manual unverified machine profile CRUD.
- Added an idempotent endpoint to save the detected local-machine profile, so engine profiles can reference its stable ID.
- Added engine profile CRUD, model inventory persistence, capability snapshots, enable/disable, fake managed start/stop, and independent probe checks; configured health/model routes are passed to adapters.
- Added URL validation, remote-target confirmation before probing, loopback mutation checks, per-process session tokens, and response masking for credential references.
- Serialized same-profile mutation/probe/lifecycle operations, tightened secret-key validation to reject common token fields in profile config, kept model identity separate from the engine profile label, and allow a successful probe to recover a profile from `needs_attention`.
- Added API and domain tests for profile behavior, partial probes, remote disclosure, credential masking, and local-origin protection.

### Checks run after the latest changes

Backend, from `backend/`:

- `uv run --locked ruff format --check src tests` — passed.
- `uv run --locked ruff check src tests` — passed.
- `uv run --locked mypy src` — passed (25 source files).
- `uv run --locked pytest -q` — passed (58 tests).

### Remaining work before Prompt 4 is complete

- Profile and machine screens are still empty states; the user-facing flows are scheduled in Prompt 8.
- Credential references are validated and masked, but OS credential-store read/write integration is not implemented.
- Managed lifecycle currently exercises only the fake adapter and does not launch an operating-system process.
- No real inference endpoint adapter is available yet. Prompt 6 adds execution through the deterministic fake adapter only.

## Prompt 5 completion record

### Delivered

- Added deterministic workload and preset APIs, validated JSONL/CSV dataset ingestion, optional content-addressed prompt snapshots, and benchmark-plan preflight with blockers, warnings, and egress disclosure.
- Saved plan configuration includes the workload and dataset snapshot identifiers; runs can recheck the retained snapshot before consuming it.
- Added limits for upload bodies, parsed rows, request volume, concurrency, and dataset retention. Prompts remain unretained by default.
- The workload and preflight API behavior is covered in `backend/tests/test_workload_api.py` and dataset import tests.

## Prompt 6 completion record

### Delivered

- Added schema migrations 3 and 4 for ordered run events, process leases, resource locks, and persisted event contract versions.
- Added a legal run state machine, atomic resource acquisition with explicit contention override, heartbeat expiry reconciliation, startup interruption recovery, and terminal lock release.
- Added `POST/GET /api/v1/runs`, cancellation, samples, event history, and resumable Server-Sent Events. Events have stable per-run sequence IDs and monotonic offsets; prompt/output bodies and credential fields are rejected from event payloads.
- Starting a run persists it before launching a separately owned `localforge.worker` process. Worker shutdown and stale-worker cleanup target only that recorded child process; managed engine lifecycle handles remain separate.
- Implemented deterministic fake-adapter request execution, bounded concurrency, bounded cancellation drain, request outcomes/samples, sanitized normalized events, and a warning when an adapter cannot cancel an in-flight request.
- Added tests for illegal transitions, event ordering/replay/redaction, lease expiry, app restart recovery, fake-adapter cancellation, child-process execution/death, lock override, and SSE heartbeat/disconnect.

### Scope and limitations

- The worker currently loads the built-in fake adapter. It verifies coordinator behavior but does not contact a model or produce valid inference performance results. Adapter plugin loading and real-engine requests remain later phases.
- Host system sampling remains unavailable in the current `system_sample` event. Prompt 7 adds client request metrics, optional engine-native metrics, and load-generation warnings; it does not claim CPU, GPU, memory, or power telemetry.
- The worker caps concurrent request tasks at 128 and emits a warning when a plan requests more. This is a bounded execution guard, not a claim that the requested concurrency was achieved.
- The dashboard is not yet connected to run APIs; live run UI is scheduled for Prompt 10.

### Checks run

Backend, from `backend/`:

- `uv run --locked ruff format --check src tests` — passed.
- `uv run --locked ruff check src tests` — passed.
- `uv run --locked mypy src` — passed (31 source files).
- `uv run --locked pytest -q` — passed (94 tests).

Frontend, from `frontend/`:

- `npm run format:check` — passed.
- `npm run lint` — passed.
- `npm run typecheck` — passed.
- `npm run test` — passed (2 tests).
- `npm run build` — passed.
- `npm run e2e` — passed (1 Chromium smoke test).

## Prompt 7 completion record

### Delivered

- Persisted each request at dispatch and finalized it with measurement/warmup phase, requested/effective concurrency, request outcome, monotonic dispatch/first-output/completion offsets, latency, TTFT, queue delay, token counts and provenance, partial-output state, output event/character counts, and a sanitized error class.
- Warmup records remain available in raw samples but are excluded from run aggregates. Interrupted in-flight records retain observed stream timing and partial-output metadata without inventing an end-to-end duration.
- Added versioned, formula-bearing client summaries for latency, TTFT, queue time, chunk/event timing, token-timestamp ITL, exact-token TPOT, input/output/generation/request throughput, outcome counts, and error rate. Sweep summaries are kept separate by concurrency level. Every stored summary includes unit, source, method version, sample count, definition, formula, and quality flags.
- Added R-7 percentiles with explicit minimum counts (p50: 1, p90: 10, p95: 20, p99: 100); insufficient tails remain null with a warning flag.
- Preserved before/after native metric observations and provenance, checked counter resets, negative counters, missing snapshots, unit/kind changes, and stored valid counter deltas. Added `GET /api/v1/runs/{run_id}/metrics`.
- Added an open-loop late-dispatch warning, plus recovery of in-flight request samples during worker/app interruption. The fake adapter's optional request counter now advances with each generated request.
- Documented metric definitions and limitations in `docs/metrics.md`; the API-backed metrics view is scheduled for Prompt 10.

### Scope and limitations

- The worker currently uses only the deterministic fake adapter. Its measured request timings exercise the pipeline and are not model-performance results.
- Host CPU/GPU/memory/power sampling is still unavailable. ITL remains unavailable unless an adapter supplies token timestamps; TPOT is only derived from a successful stream with an exact token count and observed first-output/end offsets. No character-based token estimate is produced.
- Run metrics are persisted on finalization. Live metric presentation is a later frontend phase.

### Checks run

Backend, from `backend/`:

- `uv run --locked ruff format src tests` — passed (51 files formatted or unchanged).
- `uv run --locked ruff format --check src tests` — passed.
- `uv run --locked ruff check src tests` — passed.
- `uv run --locked mypy src` — passed (32 source files).
- `uv run --locked pytest -q` — passed (111 tests).

Frontend, from `frontend/` (no frontend files changed in this phase):

- `npm run format:check` — passed.
- `npm run lint` — passed.
- `npm run typecheck` — passed.
- `npm run test` — passed (2 tests).
- `npm run build` — passed.
- `npm run e2e` — passed (1 Chromium smoke test).

Repository diff check: `git diff --check` — passed. No external engine or model download was required.

## Prompt 10 completion record

### Delivered

- Added persistent run history and hash-addressable run details, including direct navigation from a newly launched workload.
- Added live lifecycle updates over resumable persisted events, automatic reconnection, immediate cancellation controls, and terminal-state refresh without manual reload.
- Added request counts, elapsed time, client-observed latency and throughput, source-labeled metric tables, resource-telemetry availability, warnings, and the latest 100 event rows. Chart values are not interpolated; an accessible table lists the latest 500 points and total observed sample count.
- Added plan configuration fingerprints and run-start snapshots for engine version/capabilities and machine hardware provenance. Secret references and engine configuration are not copied into the run snapshot.
- Kept request/output bodies out of the run UI and event payloads. Run details distinguish retained-content opt-in from the default no-body retention policy.
- Added frontend event/sample reconciliation tests, a fake-engine E2E workflow covering live completion, reload, history navigation, cancellation, and body redaction, plus a backend test for captured profile provenance.

### Scope and limitations

- Only the deterministic fake adapter is currently available for execution; its timings are pipeline checks, not model performance results.
- Host CPU/GPU/memory/power sampling is not implemented. The detail view reports whether the worker emitted resource telemetry and preserves unavailable values as unavailable.
- Run history displays the latest 100 records. Request samples and terminal event history restore through pages, so opening very large runs can take longer than opening small runs. The frontend retains only bounded sample and event detail while aggregating request counts as pages arrive.
- Terminal runs stop their live polling timer. The event timeline keeps its latest 100 rows while preserving the most recent system-telemetry event separately.

### Checks run

Backend, from `backend/`:

- `uv run ruff format --check src tests` — passed.
- `uv run ruff check src tests` — passed.
- `uv run mypy src` — passed (32 source files).
- `uv run pytest` — passed (117 tests).

Frontend, from `frontend/`:

- `npm run format:check` — passed.
- `npm run lint` — passed.
- `npm run typecheck` — passed.
- `npm run test` — passed (25 tests).
- `npm run build` — passed.
- `npm run e2e` — passed (4 Chromium tests); Prompt 10 cancellation scenario also passed in isolation.

Repository diff check: `git diff --check` — passed.

## Prompt 11 completion record

### Delivered

- Added an attached generic OpenAI-compatible adapter for chat completions, bounded model inventory, optional health and metrics-route probes, non-streaming and server-sent-event streaming responses, usage counts when supplied, cancellation, and conservative connection-only retries.
- Kept connector settings under `engine_extensions.openai_compatible`; accepted OS-keyring references for optional credentials without returning references in profile API responses or exposing key material in adapter diagnostics/errors. The endpoint's vendor and engine version remain unknown.
- Added the endpoint profile form, model-ID fallback for servers without a model-list route, timeout, streaming and retry controls, optional usage request, metrics-route availability check, and credential reference entry. Remote probes continue to require the existing explicit egress confirmation; the disclosure describes the minimal probe request.
- Added loopback mock-server contract coverage for streaming/non-streaming, usage present/missing, malformed events, errors, disconnects, timeouts, unauthorized access, cancellation, safe retries, URL validation, and secret redaction. Added profile-form coverage for namespaced settings and credential references. No public endpoint or downloaded model is used by default checks.
- Verified compatibility against the official [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), [streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events), and [models list](https://developers.openai.com/api/reference/resources/models/methods/list) references.
- Added a live endpoint smoke test that is skipped unless `LOCALFORGE_OPENAI_COMPATIBLE_INTEGRATION=1`, a base URL, and a model ID are explicitly configured. Optional credentials are resolved by keyring reference; the test never accepts a plaintext API key.

### Scope and limitations

- The endpoint must already be running; this adapter does not install, download, or launch inference servers. It supports the portable chat-completions shape only, not vendor-specific APIs.
- The optional metrics route is probed for reachability only; `read_metrics` remains unavailable and arbitrary payloads are never interpreted. Engine identity and server version are not inferred.
- Credentials must already exist in a supported OS keyring backend and be referenced by the profile. This adapter does not create keyring entries.

### Checks run

Backend, from `backend/`:

- `uv run --locked ruff format --check src tests` - passed (55 files).
- `uv run --locked ruff check src tests` - passed.
- `uv run --locked mypy src` - passed (33 source files).
- `uv run --locked pytest -q` - passed (129 tests; 1 opt-in integration test skipped by default).

Frontend, from `frontend/`:

- `npm run format:check` - passed.
- `npm run lint` - passed.
- `npm run typecheck` - passed.
- `npm run test` - passed (26 tests).
- `npm run build` - passed.
- `npm run e2e` - passed (4 Chromium tests).

Repository diff check: `git diff --check` - passed.

## Prompt 13 completion record

### Delivered

- Added the attach-only `localforge.ollama` adapter for an existing Ollama service on loopback. It probes health with `HEAD /`, discovers server version from `/api/version`, and reads inventory from `/api/tags`; an unavailable inventory does not erase a successful health result.
- Added streaming NDJSON and non-streaming `/api/chat`, stable error/timeout/termination events, exact token usage when supplied, safe option mapping including `max_tokens` to `num_predict`, and raw per-request server timing observations in nanoseconds.
- Kept Ollama server version separate from the underlying runner. Runner metadata is stored as unknown unless verified. Remote-backed inventory entries are omitted, and request preparation checks that the selected model appears in the local inventory.
- Added an Ollama profile form and Workloads guidance that requires an existing local model and states that LOCALFORGE never pulls, deletes, starts, or stops Ollama. The adapter has no managed-process or server-side cancellation capability.
- Added fixture tests for the shared adapter contract, streaming and non-streaming responses, token/timing normalization, missing models, unavailable inventory, unknown version, timeouts, incomplete streams, safe errors, model provenance, and request metric persistence. Added an opt-in live smoke test requiring a preinstalled local model.
- Reviewed the official Ollama [API introduction](https://docs.ollama.com/api/introduction), [chat](https://docs.ollama.com/api/chat), [tags](https://docs.ollama.com/api/tags), [usage](https://docs.ollama.com/api/usage), [version](https://docs.ollama.com/api/version), and [Go client health check](https://github.com/ollama/ollama/blob/main/api/client.go) documentation on 2026-10-13.

### Scope and limitations

- The adapter only attaches to local Ollama. It does not manage the service or invoke model pull/delete APIs.
- Ollama's documented response includes per-request timing fields, not an aggregate metrics endpoint for this adapter. Those timings remain raw engine-native measurements, separate from client-observed request latency.
- The live smoke test was not run because no Ollama endpoint and preinstalled model were explicitly configured. It is skipped by default.
- E2E now starts an isolated API database and uses dedicated ports 18114/15174, so it cannot reuse or mutate an already-running development server.

### Checks run

Backend, from `backend/`:

- `uv run --locked ruff format --check src tests` - passed (61 files).
- `uv run --locked ruff check src tests` - passed.
- `uv run --locked mypy src` - passed (35 source files).
- `uv run --locked pytest -q` - passed (154 tests; 3 opt-in integration tests skipped).

Frontend, from `frontend/`:

- `npm run format:check` - passed.
- `npm run lint` - passed.
- `npm run typecheck` - passed.
- `npm run test` - passed (29 tests).
- `npm run build` - passed.
- `npm run e2e` - passed (4 Chromium E2E tests on isolated API/UI ports).

Repository diff check: `git diff --check` - passed.

## Prompt 14 completion record

### Delivered

- Added evidence-backed run comparison grades for hardware, model artifact and tokenizer identity, runtime version, workload settings, traffic pattern, metric definitions and sources, and possible contention. Comparisons list exact differences, include only matching metric definitions, preserve unavailable values, and block causal badges when evidence does not support them.
- Added experiment groups with preflighted baseline and variant plans, one-change sweeps, repetitions, deterministic randomized order, runtime confirmation, sequential execution, resumable runs, and preserved failed-trial records.
- Added local JSON, CSV, and Markdown reports with app/schema versions, setup fingerprints, workload snapshots, metric formulas and sources, counts, warnings, errors, comparability, and generated time. Credentials are stripped; retained request text is included only after explicit export selection. Unsafe paths are rejected and failed writes preserve run history.
- Added workload plan JSON export/import with configuration snapshots and dataset hash checks, plus an Experiments workspace for comparing, sweeping, resuming, and exporting from the UI.
- Added an Edge E2E that exercises a two-variant sweep, opens the change explanation, downloads and checks report contents, and checks the workspace's seven distinct hero images load successfully. Full page artwork and responsive visual QA are recorded in `design-qa.md`.
- Fixed repeated-metric summary identity so definitions with the same metric name and labels are not conflated or assigned duplicate React keys.

### Scope and limitations

- A sweep requires a ready, preflighted workload plan. Trials execute sequentially; uncertain or long estimated runtimes require an explicit confirmation.
- Default verification uses deterministic fixtures and the fake engine. Live-engine behavior still depends on the configured local adapter and server.
- Reports stay under the local data directory. Prompt/output content remains omitted unless already retained and explicitly selected for export.

### Checks run

Backend, from `backend/`:

- `uv run --locked ruff format --check src tests` - passed.
- `uv run --locked ruff check src tests` - passed.
- `uv run --locked mypy src` - passed (37 source files).
- `uv run --locked pytest -q` - passed (173 tests; 3 opt-in integration tests skipped).

Frontend, from `frontend/`:

- `npm run format:check` - passed.
- `npm run lint` - passed.
- `npm run typecheck` - passed.
- `npm run test` - passed (29 tests).
- `npm run build` - passed.
- `npm run e2e` - passed (6 Microsoft Edge tests on isolated API/UI ports).

Repository diff check: `git diff --check` - passed.

## Prompt 15A completion record

### Delivered

- Added distinct attach-only `localforge.vllm` and `localforge.sglang` adapters using the shared
  OpenAI-compatible chat transport. Both expose health, model inventory, streaming, usage, optional
  Prometheus metrics, and runtime fingerprints without installing or launching an engine.
- Scoped observed capabilities to the server-reported version when it is available. vLLM reads
  `/version`; SGLang does not call `/server_info` or `/get_server_info` because they can expose
  launch secrets. An operator-selected SGLang version route is validated, and unsafe SGLang routes
  are rejected across all route settings, including percent-encoded spellings.
- Added a bounded Prometheus text parser that preserves each engine's raw metric name, labels,
  definition, type, and component. It retains known units, leaves unknown units native, does not
  flatten histogram/summary structures into client metrics, and diagnoses missing definitions,
  malformed samples, unsupported types, other-engine samples, and truncation after its sample cap.
- Added an explicit usage-count capability probe when the profile requests streaming usage.
  SGLang leaves the probe opt-in; vLLM requests usage by default. Remote probe egress disclosure
  describes the additional short check.
- Added privacy-filtered runtime/model metadata and configuration fingerprints. Device details stay
  unknown unless a safe route reports them. Added local fixture tests for the shared contract,
  endpoint errors, model inventory, streaming and usage, version discovery, auth, timeouts, metrics,
  secret filtering, SGLang unsafe-route rejection, and malformed/missing/renamed metrics.
- Added opt-in vLLM and SGLang live smoke tests. They require a URL and an already-served model,
  probe the server before sending the test completion, and never install engines or fetch model
  artifacts. The vLLM probe sends two short checks by default; SGLang sends one unless usage is
  opted in.
- Documented setup, routes, credentials, native metrics, version coverage, and platform limits in
  `docs/vllm.md` and `docs/sglang.md`; linked the guides from `README.md`.
- Reviewed the official vLLM serving, metrics, and launch references, and SGLang observability,
  production-metrics, protocol, and HTTP-server references on 2026-10-13.

### Scope and limitations

- Both adapters are attach-only. They do not own processes, install serving engines, download
  weights, or manage CUDA/GPU configuration.
- Fixture version strings `0.10.2` (vLLM) and `0.4.9` (SGLang) are synthetic test values, not
  installed or live-validated releases. No real server version, accelerator, or operating system
  has been live-validated. The opt-in live tests were skipped because no engine endpoint/model was
  explicitly configured.
- SGLang's version is unknown by default. The connector uses only allowlisted `/model_info` data;
  device and full launch configuration are not reported.
- Native Prometheus samples remain engine-native observations with their original names and
  definitions. Missing or disabled optional metrics do not block client-side benchmarks.

### Checks run

Backend, from `backend/`:

- `uv run ruff format --check src tests` - passed (72 files).
- `uv run ruff check src tests` - passed.
- `uv run mypy src` - passed (41 source files).
- `uv run pytest -q` - passed (200 tests; 5 opt-in live tests skipped).
- `git diff --check` - passed.

No frontend files were changed for this prompt.

## Prompt 15B completion record

### Delivered

- Added attach-only connectors for TensorRT-LLM `trtllm-serve`, LMDeploy, LightLLM, Hugging Face
  TGI, LocalAI, and KoboldCpp using the shared guarded adapter path. The catalog also lists NVIDIA
  Triton with its TensorRT-LLM backend as planned because its model-specific generate API is not a
  verified Chat Completions integration.
- Extended adapter manifests with implementation status, fixture-version coverage, maintenance
  status, recommended installation status, operating-system notes, and documentation links. TGI's
  maintenance status appears in the catalog UI; the planned Triton entry cannot be added as an
  engine profile.
- Kept version, metrics, and model metadata unknown when an adapter cannot verify them. TGI and
  TensorRT-LLM expose their documented version/metrics routes; LightLLM and KoboldCpp do not claim
  Prometheus support. KoboldCpp is not reported as a lower-level llama.cpp backend.
- Added serving-stack fingerprints and persist them in each run snapshot before warmups. Collection
  has a 15-second ceiling and failure is recorded without failing the benchmark. LocalAI stores its
  backend layer only when its selected model configuration explicitly reports a validated backend
  name; missing, malformed, or unauthorized configuration remains unknown. Run snapshots omit raw
  endpoint paths, which may contain credentials.
- Added per-adapter local HTTP fixture coverage for request/stream behavior, version and model
  inventory, errors, timeouts, native metric provenance or unsupported status, and fingerprint
  composition. No default test downloads a model or contacts an upstream serving endpoint.
- Added the evidence-backed connector matrix in `docs/connector-support-matrix.md` with official
  references and an explicit distinction between fixture versions and live-tested releases.

### Scope and limitations

- All newly implemented connectors attach to an already running endpoint. LOCALFORGE does not
  install engines, download models, launch their servers, or detect accelerators in this prompt.
- Version strings are synthetic local fixture values. No upstream binary/server or accelerator was
  live-tested. Triton with TensorRT-LLM remains catalog-only and has no callable adapter.
- LocalAI backend discovery may require LocalAI administrator authorization. Without an explicitly
  reported backend value, the stack fingerprint leaves that layer unknown.

### Checks run

Backend, from `backend/`:

- `uv run pytest -q` - passed (217 tests; 5 opt-in live tests skipped).
- `uv run ruff format --check src tests` - passed (79 files).
- `uv run ruff check src tests` - passed.
- `uv run mypy` - passed (48 source files).

Frontend, from `frontend/`:

- `npm run test` - passed (31 tests).
- `npm run lint` - passed.
- `npm run typecheck` - passed.
- `npm run build` - passed.
- `npm run format:check` - passed.

Repository diff check: `git diff --check` - passed.

## Prompt 15C completion record

### Delivered

- Added attach-only connectors for MLX-LM, MLC LLM, and mistral.rs. Their server-side platform,
  route, authentication, version, and native-metric limits are recorded in the connector matrix.
  MLX-LM can be attached to from any supported LOCALFORGE host; the MLX-LM endpoint itself must
  run on Apple silicon macOS.
- Added isolated library-pipeline adapters for OpenVINO GenAI and ONNX Runtime GenAI. Model code
  runs in a separate Python process with bounded JSON Lines IPC, startup/request timeouts, output
  limits, crash cleanup, and restart recovery. Users select an existing Python executable and local
  model path; LOCALFORGE does not install packages or fetch models. Successful local probes report
  installed runtime package versions when package metadata is available, and ONNX Runtime GenAI
  reports its model-selected device.
- Added NVIDIA Dynamo as an attached composed stack. Frontend metrics remain native and unmerged;
  worker identities appear only when the frontend exports validated worker-state inventory data.
  Worker/component and backend metrics remain outside the frontend scrape.
- Listed ExLlamaV3 as planned because its upstream project is a CUDA-oriented library and its
  documented HTTP option is a separate project. No callable LOCALFORGE ExLlamaV3 adapter is claimed.
- Added fixture-backed adapter, IPC failure/recovery, platform, catalog, metrics-topology, and
  remote-resource-lock tests. Updated the connector matrix with current official documentation,
  runtime and platform constraints, and the distinction between fixtures and live-tested engines.

### Scope and limitations

- Library-pipeline tests use fixture child processes. No OpenVINO or ONNX model, package build,
  accelerator, or upstream server was installed or live-tested in standard CI.
- MLX-LM, MLC LLM, mistral.rs, and Dynamo have no verified server-version route in these adapters,
  so their server versions remain unknown. Fixture responses are not upstream compatibility claims.
- ExLlamaV3 remains catalog-only and planned. Dynamo captures frontend metrics and its verified
  router worker inventory; it does not scrape worker/backend endpoints or infer unreported stacks.

### Checks run

Backend, from `backend/`:

- `uv run ruff format --check src tests` - passed (89 files).
- `uv run ruff check src tests` - passed.
- `uv run mypy src/localforge` - passed (57 source files).
- `uv run pytest` - passed (242 tests; 5 opt-in live tests skipped).
- `git diff --check` - passed.

## Prompt 16 completion record

### Delivered

- Audited all 19 intended ecosystem entries against spec section 5 and included all 20 registered
  manifests, including the deterministic fake fixture, in `docs/connector-support-matrix.md`.
- Added exact adapter IDs, implementation states, integration modes, LOCALFORGE host OS values,
  `version_tested` evidence, official documentation links, and scope limits to the matrix. Added an
  automated backend test that compares each field with the live manifest catalog and pins the
  intended adapter ID set.
- Added `docs/adapter-authoring.md` with manifest and capability semantics, contract/fixture test
  patterns, version evidence requirements, documentation and release status guidance, and privacy
  expectations. Linked the guide and complete matrix from `README.md`.
- Changed the catalog UI to distinguish implemented, planned, attach, managed, library, and
  composed modes; label local fixture contract validation separately from upstream release
  evidence; show LOCALFORGE host OS and each adapter's documentation link. Engine profiles now
  identify a configured endpoint as local or remote.
- Added a schema-driven profile form for generic adapter extension settings and local library
  pipelines, including existing model path, optional Python executable, device, and bounded
  timeout inputs. Library settings persist in the adapter namespace and reach the isolated worker;
  timeout values are read from that namespace as well.
- Recorded fixture evidence for llama.cpp and Ollama and declared the missing host OS values for
  Ollama and the generic OpenAI-compatible endpoint. Planned Triton and ExLlamaV3 remain
  non-callable catalog entries.

### Scope and limitations

- Fixture versions are synthetic local responses and do not claim upstream release compatibility.
  No new live engine, model, package build, or accelerator was tested.
- Ollama remains attach-only. The llama.cpp adapter supports attached and explicitly managed local
  process modes. All other ecosystem modes and the planned status remain as declared in the matrix.
- The generic schema form supports manifest fields of string, number, integer, boolean, or enum
  types. Adapter settings remain in extension storage; benchmark storage was not changed.

### Checks run

Backend, from `backend/`:

- `uv run ruff format --check src tests` - passed (89 files).
- `uv run ruff check src tests` - passed.
- `uv run mypy src` - passed (57 source files).
- `uv run pytest -q` - passed (244 tests; 5 opt-in live tests skipped).

Frontend, from `frontend/`:

- `npm run format:check` - passed.
- `npm run lint` - passed.
- `npm run typecheck` - passed.
- `npm run test -- --reporter=dot` - passed (34 tests).
- `npm run build` - passed.
- `npm run e2e` - passed (6 Chromium tests using isolated local API and UI ports).

- `git diff --check` - passed.
