# LOCALFORGE Inference Lab — Codex implementation prompt pack

**Version:** 1.0  
**Purpose:** A sequenced set of copy-ready prompts for implementing LOCALFORGE Inference Lab in Codex.  
**Source of truth:** LOCALFORGE-Inference-Lab-Implementation-Spec-v1.0.md

## How to use this pack

Use one Codex conversation and the same repository for the full sequence. Paste Prompt 0 once, then paste the prompts in order. Do not skip a phase whose work is a dependency for the next one. Each prompt asks Codex to inspect the current repository because the codebase may already contain work.

The product specification is available at:

/workspace/scratch/557b1bf34dd8/LOCALFORGE-Inference-Lab-Implementation-Spec-v1.0.md

If Codex cannot read that path, add the specification to the repository as docs/product-spec.md or attach it to the conversation. Treat the specification as the product and measurement authority. Treat the repository’s established conventions as the implementation authority when they do not conflict with the specification.

The default implementation described by the spec is a React and TypeScript UI, a Python FastAPI local control plane, an isolated benchmark worker, and SQLite persistence. If the repository is empty, use that stack unless a concrete environment constraint prevents it. If it already has a coherent stack, preserve it and document any mapping from the spec.

Prompts 15A–15C expand the engine catalog. Run them in order after the MVP adapters. Engine APIs and support change: before implementing each adapter, inspect that engine’s current official documentation and record the version tested. Do not promise working support for a connector that is only represented in the catalog.

---

## Persistent implementation contract

Prompt 0 establishes this contract for the rest of the Codex conversation. Keep it in force for every prompt.

- Work in the current repository. Read its README, project instructions, dependency manifests, CI configuration, and existing code before editing. Respect AGENTS.md and repository conventions.
- Implement the requested phase in working code. Do not stop after a plan, mockup, scaffold, or list of recommendations.
- Make the smallest coherent change that completes the phase. Preserve working behavior and avoid unrelated refactors.
- Make the app local-first. Bind to loopback by default. Remote endpoints must be opt-in and clearly disclosed before requests are sent.
- Prefer explicit, typed, versioned contracts. Do not infer engine capabilities, token counts, versions, hardware, or metric sources when they cannot be verified. Represent unknown as unknown.
- Keep raw measurements and their provenance. Use monotonic time for durations. Do not conflate TTFT, inter-token latency, TPOT, request latency, and throughput.
- Never put credentials, authorization headers, raw secrets, or secret query parameters in logs, exception responses, test snapshots, or exports. Do not retain prompt or output content by default.
- Never terminate an engine process that LOCALFORGE did not launch and record as owned. Do not run shell commands assembled from user input. Do not download models or datasets without an explicit user action.
- Avoid nonfunctional placeholder buttons, fake success states, fabricated metrics, and unimplemented controls that look complete. If something is planned but not supported, say so in the UI.
- Add meaningful tests with each behavior. Do not write tests that merely mirror implementation details. Do not skip, xfail, weaken, or delete a test to make a phase pass. Fix failures or explain a concrete environment blocker.
- Use deterministic fake-engine fixtures for normal CI and end-to-end tests. Live-engine tests must be explicitly opt-in and must not download weights or require a GPU in standard CI.
- After each phase, run the relevant formatters, linters, type checks, unit tests, integration tests, and end-to-end tests available in the repository. Add missing commands where needed. Report exact commands and results.
- Update user-facing documentation as the product changes. Keep setup and test instructions accurate.
- At the end of each phase, report: what changed, key files, definition-of-done checklist, exact tests/commands run, failures or limitations, and the next phase dependency. Do not claim a gate passed unless it did.

## Global definition of done

The first portfolio-quality release is done only when a user can:

1. Start the app locally and understand where its data stays and when a remote endpoint receives prompts.
2. Inspect detectable system information without overstating what could not be detected.
3. Register or attach to a supported engine and see its tested version and capability snapshot.
4. Build and validate a deterministic workload and benchmark plan.
5. Run a benchmark using a deterministic fake engine in CI and a supported real engine when configured.
6. See live run state, request outcomes, latency, throughput, available resource metrics, and metric-source labels.
7. Cancel a run safely; interrupted and failed requests remain visible in history.
8. Compare runs with a clear comparability explanation.
9. Export a reproducible report with configuration, methods, sample counts, warnings, and secrets removed.
10. Complete a browser end-to-end test from engine setup through export without a real model download.
11. Pass the repository’s formatting, lint, type, unit, integration, security-relevant, and E2E gates.
12. Have documentation that distinguishes shipped, attach-only, managed-launch, library-pipeline, and planned engine support.

A connector is complete only when its declared capabilities are backed by deterministic adapter contract tests, its limitations and tested versions are documented, and no capability is reported as supported without evidence.

---

# Prompt 0 — Repository reconnaissance and implementation plan

You are implementing LOCALFORGE Inference Lab. First read the product specification at /workspace/scratch/557b1bf34dd8/LOCALFORGE-Inference-Lab-Implementation-Spec-v1.0.md. If unavailable, locate an attached or repository copy; if none exists, stop and report that the source specification is missing.

Inspect the repository instructions, current branch and working tree, README, manifests, application structure, tests, CI, and available developer commands. Do not overwrite or discard existing work.

Create or update docs/implementation-plan.md with:
- the current architecture and a map to the spec’s recommended architecture;
- the ordered phases in this prompt pack;
- the first usable vertical slice and what is deliberately deferred;
- test commands and expected CI gates;
- concrete constraints, assumptions, and risks found in this repository.

Do not implement product features in this prompt. If the repository is empty, propose the smallest maintainable initial layout using React/TypeScript, FastAPI/Python, SQLite, a separate worker boundary, pytest, frontend unit tests, and Playwright E2E. Do not add unnecessary dependencies.

Definition of done:
- The spec and repository guidance have been read.
- The implementation plan exists and accurately reflects the repository.
- No existing user changes were discarded.
- The report names the exact files inspected and any unresolved constraint.
- No code feature is claimed complete.

Return the plan summary and the exact commands that would be used to validate the repository.

---

# Prompt 1 — Project foundation and runnable shell

Implement the foundation phase from the specification, following docs/implementation-plan.md and preserving existing repository conventions.

Create a runnable local development setup with:
- a desktop-first React/TypeScript app shell and navigation for Overview, Engines, Workloads, Runs, Experiments, and Learning;
- a versioned FastAPI control-plane skeleton with GET /health;
- an explicit frontend-to-backend development configuration that works locally;
- SQLite connection and migration infrastructure, with schema versioning and safe startup behavior;
- a separate worker entry point or process boundary suitable for later long-running benchmark tasks;
- formatting, lint, type-check, unit-test, and E2E commands in the project scripts and CI;
- a deterministic fake-engine test fixture or test service;
- a short local setup guide.

Keep visual work focused on a usable dark technical shell: clear type, keyboard-visible focus, status conveyed by text, and no invented benchmark data. Bind the backend to 127.0.0.1 by default. Do not add remote telemetry or model downloads.

Definition of done:
- A developer can start the UI and API using documented commands.
- The UI reaches the health route and displays API unavailable/recovered states accessibly.
- A migration can initialize a fresh temporary database and report its schema version.
- The worker can be invoked independently without coupling it to an HTTP request lifecycle.
- CI runs at least one meaningful backend test, one frontend test, and one browser E2E smoke test.
- All checks added in this phase pass locally; docs list the exact commands.

Tests to add:
- API health success and unavailable/error handling at the frontend boundary.
- Fresh database migration and repeated startup/idempotency.
- E2E: open the app, verify navigation and API status, reload, and verify no fatal error page.
- Run the whole configured check suite and report results.

---

# Prompt 2 — Domain contracts, persistence, and migrations

Implement the shared domain model and persistence layer required for the first benchmark workflow. Read sections 7, 8, 10, 12, and 17 of the spec before editing.

Define versioned, typed schemas and storage/repository interfaces for at least:
- MachineProfile;
- EngineProfile and CapabilitySnapshot;
- ModelArtifact;
- Dataset and Workload;
- BenchmarkPlan and BenchmarkRun;
- RequestSample and MetricSample;
- ExperimentGroup and Report metadata.

Use stable IDs, created/updated timestamps, explicit status enums, units and metric-source fields, schema versions, and nullable/unknown values where information is unavailable. Preserve namespaced engine-specific extension data without allowing it to overwrite normalized fields.

Use SQLite WAL mode where supported, bounded busy handling, parameterized queries, forward-only migrations, and temporary-database support in tests. Persist raw samples before deriving aggregates. Store only references or redacted summaries for prompts/outputs unless retention has been explicitly selected.

Definition of done:
- Fresh installs create the current schema and existing databases upgrade through tested migrations.
- Invalid enum values, missing required fields, and unsupported schema versions fail with stable, understandable errors.
- Repository operations work against a temporary SQLite database and do not depend on global process state.
- Deleting a run and deleting retained content have distinct, tested semantics.
- The API and frontend types have a documented source of truth; avoid hand-maintained duplicate definitions where generation or contract tests are practical.

Tests to add:
- Schema validation for valid, invalid, unknown, and boundary values.
- Migration from every supported prior schema fixture, plus migration interruption/retry behavior.
- CRUD and transaction rollback for core repositories.
- Metric records preserve value, unit, source, timestamp, and labels.
- Redaction and content-retention defaults.
- Run all repository checks.

---

# Prompt 3 — Adapter contract, capabilities, and deterministic fake engine

Implement the engine-adapter contract and capability model from sections 5 and 10 of the spec. The adapter must work as a plugin-like boundary so adding an engine does not require engine-specific frontend screens.

Define a versioned adapter manifest and typed interfaces for describe, config validation, probe, start, stop, list_models, request preparation, streaming, request abort where supported, native metric reading, runtime fingerprint collection, and close. Separate attached endpoint, managed process, library pipeline, and composed serving-stack integration modes.

Capabilities must support supported, unsupported, unknown, and user-supplied states. Include streaming, cancellation, usage counts, health, model inventory, native metrics, TTFT/TPOT/ITL metrics, cache/KV-cache metrics, batching, multimodal features, process management, and platform support.

Implement a deterministic fake adapter that can produce normal streaming, delayed first token, multi-token chunks, errors, disconnects, timeouts, cancellation, missing usage, and optional native metrics. It must be controllable in tests without sleeps where possible.

Definition of done:
- The app can list the fake adapter and its manifest through the same catalog path intended for real adapters.
- The fake adapter cannot claim a capability not declared in its manifest.
- A missing or old adapter version fails safely with a readable diagnostic.
- Adapter calls are isolated so a slow probe or stream does not block unrelated API requests.
- Contract tests run against the fake adapter and can be reused by every engine adapter.
- No real engine is required by ordinary CI.

Tests to add:
- Manifest/schema validation and capability-state behavior.
- Adapter contract tests for probe success/failure, stream completion/error/early-close, cancellation, metrics unavailable, and close cleanup.
- Verify unknown remains unknown and is not coerced to unsupported or supported.
- Verify managed stop only targets a process recorded as owned by the adapter.
- Run all checks.

---

# Prompt 4 — Machine inspection, engine registry, and safe connection tests

Implement system inspection and engine registration. Review spec sections 8.1, 8.2, 12.1, and 13.

System inspection should return only information the app can actually detect: OS, CPU, memory, accelerators, and relevant runtime/driver versions. Keep source and confidence/provenance for each value. Provide a manual, explicitly unverified profile for remote hardware that cannot be inspected.

Implement engine catalog and profile APIs to create, edit, enable, disable, probe, start, stop, and remove profiles when the adapter supports that action. Probe reachability, health, version, model inventory, streaming, and optional metrics independently; one missing endpoint must not erase successful probe results.

Security requirements:
- Bind to loopback by default and protect mutations using the local security pattern selected for this architecture.
- Mask credentials in responses and never persist them in plaintext. Use the OS credential facility if available; otherwise use a documented local encryption/key mechanism.
- Validate URL schemes and hosts; treat remote access as opt-in.
- Never kill a process found on a port unless LOCALFORGE launched and owns that exact process.
- Do not log secret values or full authorization headers.

Definition of done:
- A user can create a fake or supported engine profile, probe it, review capability diagnostics, and edit/remove it.
- The profile UI shows integration mode, tested version, and capability state.
- System values include provenance; undetectable values are explicitly unknown.
- Failed endpoint, auth, TLS, occupied-port, and missing-executable cases produce safe actionable diagnostics.
- Test fixtures use loopback mocks; tests never call public endpoints.

Tests to add:
- Hardware detection under mocked available/unavailable platform probes.
- API tests for engine CRUD, probe partial success, auth failure, invalid endpoint, and secrets masking.
- Verify a port conflict never results in a process termination attempt.
- Verify remote target disclosure is shown before the first request is sent.
- Run all checks.

---

# Prompt 5 — Workload, dataset import, and benchmark preflight

Implement workload creation and benchmark-plan preflight from sections 8.4 and 8.5 of the spec.

Support built-in deterministic workloads for fixed prefill, fixed decode, mixed chat, concurrency sweep, target request rate, and long context. Allow JSONL/CSV import with configurable field mapping, row limits, deterministic shuffle seed, duplicate handling, prompt sizing method, output limits, warmup count, duration or request limit, closed-loop concurrency or open-loop arrival rate, sampling settings, and content-retention choices.

Validate datasets by streaming where possible. Report row/line-specific parse errors, enforce configured size/count limits, preview a small redacted sample, record content hash and tokenizer/count method, and never silently import partial malformed data. A saved plan must use an immutable dataset snapshot or detect a changed hash before execution.

Preflight must show target, supported and unknown options, missing prerequisites, estimated run duration/volume when calculable, resource contention, and any remote data-egress disclosure. Block only when the requirement is known to be invalid; warn when the limit or capability is unknown.

Definition of done:
- A user can save and replay a deterministic workload without editing a config file.
- CSV and JSONL validation handles empty files, malformed rows, missing fields, duplicate rows, large files, and path traversal.
- Prompt and output retention are opt-in and reflected in storage/export.
- A changed source dataset cannot silently alter a saved plan.
- Preflight results identify blockers separately from warnings.
- Unit and API tests cover all supported workload shapes and invalid combinations.

E2E tests to add:
- Create workload from a preset, validate a small uploaded dataset, configure a plan, and see preflight success.
- Verify malformed data and an incompatible known context limit block launch with useful messages.
- Verify an unknown tokenization method is labeled rather than fabricated.
- Run all checks.

---

# Prompt 6 — Run coordinator, worker lifecycle, locks, and event stream

Implement asynchronous run orchestration from sections 8.6, 10.2, 10.5, and 10.6.

A run must be accepted quickly, assigned a stable ID, persisted, and executed in a separate worker boundary. Model the lifecycle explicitly: queued, preparing, warming, running, cancelling, completed, failed, cancelled, interrupted. Persist state transitions and event sequence numbers so restart/reconnect does not lose the final status.

Use a resource-lock manager for accelerator IDs, CPU resource groups, endpoint IDs, and serving-stack IDs. Prevent overlapping tests unless a user explicitly overrides the contention warning. Reconcile stale locks after worker death and mark orphaned runs interrupted on startup.

Stream sanitized, versioned events to the UI using SSE or the repository’s established equivalent. Normalize request_started, first_output, output_chunk metadata, engine_usage, request_completed, request_failed, request_cancelled, run_metric, system_sample, warning, and heartbeat. Do not include prompt/output bodies unless explicitly retained. Use bounded queues and batching so a slow browser cannot stall request generation.

Cancellation must stop dispatching new requests, request adapter cancellation where available, drain within a bound, and terminate only LOCALFORGE-owned worker processes if graceful cancellation fails.

Definition of done:
- Starting a run returns promptly with its run ID and queued/running status.
- Event reconnection can resume from the last event ID or retrieve persisted event history.
- Worker crash, app restart, cancellation, request timeout, and stale-lock recovery yield correct persisted states.
- A slow or disconnected UI does not block benchmark execution.
- A competing plan receives a useful lock conflict with an explicit override path.
- A managed engine process is never confused with the benchmark worker process.

Tests to add:
- State-machine transition tests including illegal transitions.
- Integration tests for queueing, cancellation, worker death, restart recovery, and lock expiry.
- SSE tests for ordered IDs, replay, heartbeat, disconnect, and sanitized payloads.
- Verify a fake engine cancellation completes within the configured bound.
- Run all checks.

---

# Prompt 7 — Metric capture, formulas, and measurement integrity

Implement raw sample capture and metric aggregation from sections 8.7, 9, 12.4, and 14 of the spec.

For every request, preserve monotonic start, first-output, and end durations where available, status, reliable input/output token counts, error class, and optional content under the retention policy. Keep wall-clock timestamps for display/correlation, never for duration calculations.

Keep client-side measurements distinct from engine-native metrics. Define and document units and formulas for request latency, TTFT, inter-token latency, TPOT, input/output throughput, request throughput, queue time, success/error/timeout counts, and percentiles. Do not calculate per-token timing from chunk count unless token count and timestamps make that valid. Do not derive exact token counts from character counts. Show estimates or unavailable values with a source label.

Preserve native raw metrics with engine/version/source/labels and detect invalid counters, reset counters, missing values, and unit mismatch. Report sample counts and warn when tails are statistically unstable. Include load-generator saturation and resource contention warnings.

Definition of done:
- Raw request records are persisted before aggregate metrics are calculated.
- Every displayed metric has a unit, definition, source, sample count, and method/version.
- TTFT appears only when observed from a stream or explicitly reported by the engine with provenance.
- TPOT and ITL remain separate metrics with defined formulas.
- Small or invalid samples cannot produce an authoritative-looking percentile.
- Exported raw data can reproduce the derived summary.

Tests to add:
- Formula tests with hand-calculated fixtures for success, timeout, cancellation, partial stream, zero tokens, and missing timestamps.
- Percentile and minimum-sample warning tests.
- Counter-reset, unit-mismatch, and missing-native-metric tests.
- Verify monotonic duration survives wall-clock adjustment in a simulated test.
- Run all checks.

---

# Prompt 8 — Core frontend flow: overview, machines, and engines

Implement the frontend against real API contracts. Read sections 7, 11, and 15 of the spec. Do not build decorative screens with hard-coded benchmark numbers.

Build:
- Overview with actual machine status, latest-run summary, and a useful empty state.
- System detail with value provenance and unknown/unverified states.
- Engine catalog and engine-profile list.
- Add/edit engine flow for the fake adapter and MVP connection modes.
- Probe results showing health, models, version, streaming, metrics, capabilities, and diagnostics independently.
- Accessible loading, empty, offline, validation, and error states.

Use a typed API client, clear query/loading/error boundaries, keyboard-accessible controls, visible focus, and tabular equivalents for any chart or visual metric. Explain remote request data egress before testing or running a remote endpoint. Never display a secret after saving it.

Definition of done:
- Every visible action is wired to a functioning API or is clearly marked unavailable.
- A user can add, probe, edit, and remove the fake engine using only the UI.
- The screen exposes unknown capability values honestly.
- Layout remains usable at laptop and narrow window sizes.
- No mock measurements are presented as actual data.

Tests to add:
- Component tests for loading, empty, success, error, partial-probe, and secret-masking states.
- Keyboard navigation and accessible-name checks for the add/probe flow.
- E2E: register a fake engine, probe it, inspect capabilities, edit it, remove it.
- Run all checks.

---

# Prompt 9 — Workload builder and plan review UI

Implement the UI for workload creation, dataset validation/import, and benchmark preflight.

The workflow should make the user choose an engine, model, workload source, traffic mode, input/output sizing, warmup, run limit, sampling options, and retention policy. Distinguish closed-loop concurrency from open-loop target request rate. Show tokenizer/counting method, deterministic seed, dataset hash/snapshot, and unsupported or unknown engine features.

Before launch, provide a review page with blockers, warnings, expected request count/duration when calculable, resource-lock status, target details, and remote data-egress notice. Preserve entered values when server validation fails. Do not silently substitute unsupported settings.

Definition of done:
- A valid plan can be saved and replayed.
- Invalid plans show field-level accessible messages and do not start runs.
- Unknown token limits warn; known exceeded limits block.
- Dataset previews redact content unless retention was chosen.
- The plan screen is usable without a mouse and at narrow viewport widths.

Tests to add:
- Form and schema boundary tests for each workload mode.
- Component tests for validation, token-estimate source, warning/blocker differences, and retained form values.
- E2E: choose engine/model, create preset or import dataset, configure plan, review preflight, and launch a fake-engine run.
- Run all checks.

---

# Prompt 10 — Live run, run history, and run detail

Implement live execution views and run-history screens using persisted run state and the normalized event protocol.

The live view must show lifecycle state, elapsed time, request counts, successes/failures/timeouts/cancellations, available latency/throughput metrics, source labels, resource telemetry if available, event timeline, warnings, and a safe cancel action. Charts need units, axes, sample counts, an accessible table, and a no-data state. Do not smooth or interpolate absent samples as if measured.

Run detail must show plan/configuration fingerprint, engine/version/capabilities, machine profile, raw versus normalized metrics, incomplete samples, errors, and retention state. Reconnecting to a run must restore persisted state and resume events. History must make interrupted and failed runs discoverable.

Definition of done:
- A fake-engine run can be followed from queued through terminal status without refreshing.
- Refresh/disconnect/reconnect does not lose the terminal state or duplicate request samples.
- Cancel is disabled or explained when unsafe/unavailable, and user cancellation remains recorded.
- All metrics show their source and unavailable values remain distinct from zero.
- Errors are safe and actionable without secret leakage.

Tests to add:
- Frontend state tests for event ordering, duplicate event IDs, reconnect, stale event, failure, and cancellation.
- E2E: start fake run, observe live updates, cancel a second run, open both run details from history.
- Verify no prompt/output body is exposed by default in UI or event payload.
- Run all checks.

---

# Prompt 11 — Generic OpenAI-compatible endpoint adapter

Implement the generic attached endpoint adapter. Before coding, verify the current official API compatibility expectations relevant to the endpoints you support. Keep the connector generic and do not infer the underlying engine.

Support configurable base URL, model ID, optional secret reference, chat completion as the initial portable path, streaming where supported, safe timeout/retry settings, and feature probes for model inventory and optional endpoints. Make retries explicit and never retry a request after output has started unless the API contract makes replay safe.

Definition of done:
- Attach-mode configuration works with the local deterministic mock endpoint.
- Probe distinguishes unreachable, unauthorized, healthy-but-no-model-list, streaming unsupported, and optional metrics unavailable.
- The endpoint’s engine identity stays “unknown/custom” unless explicitly user supplied.
- Remote endpoint use displays the egress disclosure before any request.
- Authorization secrets are masked and redacted from logs, exceptions, diagnostics, and exports.
- API-specific fields stay in a namespaced extension object.

Tests to add:
- Adapter contract suite against a local mock server for streaming and non-streaming responses, usage present/missing, malformed stream event, error response, disconnect, timeout, unauthorized, and cancellation.
- URL validation and secret-redaction tests.
- Opt-in integration test configuration that never runs in default CI.
- Run all checks.

---

# Prompt 12 — llama.cpp and llama-server adapter

Implement the MVP adapter for llama.cpp/llama-server using the current official server documentation and benchmark guidance. Record the exact version or revision used for implementation and tests. Support attach mode first and managed launch only where executable discovery and platform behavior are safely implemented.

Probe health, server version when available, model identity, streaming, usage/token details, and optional Prometheus metrics independently. Expose only settings the adapter validates for that version, such as model path, context, parallelism, batch settings, GPU layers, and bind/port settings where managed launch is supported. Explain GGUF and backend/platform requirements in the UI.

Definition of done:
- Attach mode works against a local llama-server test fixture.
- Managed mode starts only an explicitly selected executable with argument arrays and a controlled working directory, tracks its process identity, and stops only that owned process.
- Metrics are normalized only when the upstream definition and unit are known; all raw names and sources are retained.
- Unsupported OS/backend combinations are clearly labeled and do not look like launch failures.
- The adapter catalog and UI identify integration mode and tested version.

Tests to add:
- Reusable adapter contract suite plus llama.cpp-specific probe, model, stream, usage, metrics, bad-version, missing-executable, timeout, port-in-use, and process-ownership tests.
- Mock upstream responses in ordinary CI; optional live smoke test requires a preinstalled server and model path supplied by the user.
- Never download model weights in tests.
- Run all checks.

---

# Prompt 13 — Ollama adapter

Implement the MVP Ollama adapter using current official API documentation. Distinguish Ollama as a model management/server layer from the runner and underlying runtime when the runner can be detected; otherwise record the runner as unknown.

Support attach mode to an existing local server, health/model inventory, chat streaming, usage/latency fields where defined, version discovery where available, and capability probing. Implement managed server lifecycle only if it can be done without interfering with an existing Ollama service. Model pulling or deletion must require an explicit user action and confirmation; never do it as a side effect of probing or benchmarking.

Definition of done:
- Probe works when the health endpoint succeeds but model inventory is unavailable.
- Streaming and non-streaming/error responses normalize into the shared event and sample model.
- Underlying runner details are stored only when verified.
- The UI explains whether the benchmark is using a model already present and never starts an implicit download.
- Stop never terminates an Ollama process the app does not own.

Tests to add:
- Adapter contract plus native chat, stream termination, usage, missing model, model-list failure, version unknown, timeout, and server-already-running tests.
- Verify probe and run do not invoke pull/delete behavior.
- Live smoke test is opt-in and uses a preinstalled local model.
- Run all checks.

---

# Prompt 14 — Comparisons, experiment groups, and reproducible exports

Implement multi-run comparison, experiment groups/sweeps, and report export from sections 8.9, 8.10, and 9 of the spec.

Comparison must calculate a transparent comparability grade from hardware, model artifact/revision/quantization, engine/runtime version, workload and traffic pattern, tokenization, measurement source, and contention. Show the exact mismatches and compare shared metrics only. Different tokenization or model artifacts must not receive a controlled-comparison badge. Do not present a performance difference as causal when more than one variable changed.

Experiment groups must record baseline, variants, declared variable, repetitions, order/seed, and failed trials. Reports must include app/schema version, engine and model fingerprints, workload plan, metric formulas and sources, sample counts, warnings, errors, comparability, and generated time. Export JSON, CSV, and Markdown with safe filenames and path-traversal protection. Strip credentials always; include prompt/output text only when explicitly retained and selected at export.

Definition of done:
- A user can compare runs and understand why the comparison is controlled, directional, or not comparable.
- Missing values are not treated as zero.
- A plan export can be re-imported and reproduces the same workload configuration.
- Export includes enough provenance for another engineer to reproduce the experiment.
- Export failure leaves original history intact.

Tests to add:
- Comparability matrix tests that change one dimension at a time.
- Formula and report snapshot tests based on deterministic fixture runs.
- JSON/CSV/Markdown round-trip, secret-stripping, retention-policy, invalid-path, and unwritable-target tests.
- E2E: run two variants, compare, inspect mismatch explanation, export report, and verify file contents.
- Run all checks.

---

# Prompt 15A — Production serving adapters: vLLM and SGLang

Implement attach adapters for vLLM and SGLang first, then add managed Linux/CUDA launch profiles only when version-tested and safely configurable. Re-read their current official API, metrics, benchmark, and launch documentation before coding. Keep serving-engine metrics separate from client metrics.

For each engine, probe health, version, model inventory, streaming, usage, and optional Prometheus/native metrics. Record the model/runtime configuration fingerprint, server version, GPU/device metadata if the server exposes it, and the definitions and units of native metrics. Capability support must be version-specific. Managed launch must validate device selection, command arguments, working directory, environment, port, logs, timeout, and owned-process cleanup.

Definition of done:
- Each adapter passes the shared contract suite and engine-specific fixture tests.
- Optional metrics being disabled does not block client-side benchmarking.
- vLLM and SGLang appear as distinct connectors with distinct versioned capabilities and diagnostics.
- Default CI uses local fixtures and does not require CUDA, install either engine, or download weights.
- Opt-in live tests clearly document the required server URL/model and never fetch artifacts.

Tests to add:
- Streaming, errors, version probe, model list, native metric units, missing/renamed metrics, malformed metrics, auth/timeout, and process ownership where managed.
- Verify a metric from one engine is never silently mapped to a differently defined normalized metric.
- Run all checks and document tested versions and platform limits.

---

# Prompt 15B — Additional serving and wrapper connectors

Extend the adapter catalog using current official documentation and version-specific tests for:
- TensorRT-LLM;
- NVIDIA Triton with the TensorRT-LLM backend;
- LMDeploy;
- LightLLM;
- Hugging Face TGI, labeled maintenance mode in the catalog and UI;
- LocalAI, recording its selected backend when detectable;
- KoboldCpp.

Represent a wrapper and its selected backend as separate stack layers when the backend can be verified. Do not label a wrapper as the inference runtime unless that is accurate. Prefer attach mode and common API/metrics integration first. Add managed launch only when configuration and process ownership are tested.

Definition of done:
- Each connector has a tested catalog entry, adapter manifest, declared integration mode, supported-platform notes, official documentation link, and version-tested field.
- TGI’s maintenance status is visible and it is not presented as a recommended new managed installation.
- Wrapper/backend fingerprints are separately stored; unknown layers remain unknown.
- A connector without verified API support is labeled planned/unsupported, not implemented.
- No connector requires a model download or accelerator in default CI.

Tests to add:
- Shared adapter contract suite for every implemented adapter.
- Engine-specific local fixtures for API compatibility, version/capability differences, streaming, error handling, metrics provenance, and timeout.
- Stack composition tests for wrapper plus backend identity.
- Verify TGI maintenance label and unknown-backend handling.
- Run all checks and update support matrix documentation.

---

# Prompt 15C — Platform, library, and distributed-stack connectors

Implement and document connectors for:
- MLX-LM;
- MLC LLM;
- OpenVINO GenAI;
- ONNX Runtime GenAI;
- Mistral.rs;
- ExLlamaV3;
- NVIDIA Dynamo with vLLM, SGLang, or TensorRT-LLM worker stacks.

Re-read current official documentation for each target. Separate attached server adapters from library pipelines and composed serving stacks. Library pipelines must run in isolated worker processes with bounded IPC, cleanup, and dependency/version fingerprints. Dynamo must represent router/orchestrator, workers, and aggregate metrics as separate layers; do not merge aggregate and per-worker counters without a documented formula.

Some targets are platform- or hardware-specific. Catalog them with truthful OS, accelerator, dependency, model-format, and maturity notes. Keep unsupported platforms usable for the rest of the app.

Definition of done:
- Every implemented connector passes the shared adapter contract or an explicitly extended contract for its integration category.
- Library exceptions/crashes cannot corrupt the main API process or database.
- Platform-incompatible adapters give an actionable unsupported status rather than a generic app error.
- Dynamo reports topology and worker identities only when verified and keeps aggregate metrics distinct.
- Standard CI uses fake/local fixtures; live and accelerator tests are opt-in.
- No automatic model download or external endpoint call occurs in ordinary tests.

Tests to add:
- Subprocess crash, timeout, IPC validation, cleanup, and restart-recovery tests for library pipelines.
- Per-platform capability tests with mocked system profiles.
- Dynamo topology and aggregation tests including missing worker metrics.
- Adapter contract and version/capability tests for every delivered connector.
- Run all checks and publish a support matrix with tested versions.

---

# Prompt 16 — Engine ecosystem catalog and adapter contribution workflow

Audit the complete engine catalog against section 5 of the spec. Ensure the UI and docs distinguish:
- managed local process;
- attached local endpoint;
- attached remote endpoint;
- isolated library pipeline;
- composed serving stack.

The intended catalog includes llama.cpp/llama-server, Ollama, vLLM, SGLang, TensorRT-LLM, Triton with TensorRT-LLM, NVIDIA Dynamo, LMDeploy, LightLLM, Hugging Face TGI, MLX-LM, MLC LLM, OpenVINO GenAI, ONNX Runtime GenAI, Mistral.rs, ExLlamaV3, LocalAI, KoboldCpp, and a custom OpenAI-compatible endpoint. Optional future entries such as Candle-based servers and Aphrodite Engine may be recorded as planned without claiming support.

Add contributor documentation explaining the adapter manifest, capability states, contract tests, local fixture requirements, version-tested field, documentation links, privacy expectations, and release status. Keep planned, attach-only, managed-launch, library-pipeline, and fully tested statuses visibly distinct.

Definition of done:
- Every intended catalog entry has a truthful status and connection mode.
- No planned or untested connector is advertised as a working adapter.
- The adapter author can add a fixture-backed adapter without changing benchmark storage or building an engine-specific frontend screen.
- Catalog coverage is checked by an automated test against the support matrix.
- Run all checks.

---

# Prompt 17 — Security, privacy, recovery, and edge-case hardening

Perform a code-driven security and reliability pass against sections 12, 13, 14, and 17 of the spec. Fix issues in scope; do not only produce an audit report.

Check loopback binding, local mutation protection, CORS/CSRF, remote egress disclosure, credential storage and redaction, subprocess argument safety, process ownership, imported file size/path handling, export path traversal, prompt/output retention, database migration/recovery, worker orphan cleanup, run cancellation, stale locks, and bounded queues/timeouts. Review logs, error responses, browser console output, test snapshots, exports, and crash paths for secrets and prompt leakage.

Definition of done:
- Security-relevant defaults are tested, not only described.
- Secrets cannot be recovered from logs, diagnostics, UI state, test failure output, or exports.
- File handling cannot escape configured directories through traversal or symlink surprises.
- Only explicitly owned child processes can be stopped.
- Startup after worker/app interruption preserves completed samples and marks incomplete runs accurately.
- Any unavoidable limitation is documented beside the affected behavior.

Tests to add:
- Negative tests for each threat path above, using safe temporary resources and mocked processes.
- Tests for database busy/recovery behavior, worker crash, cancellation timeout, queue saturation, and cleanup.
- Check dependencies for known issues using the project’s existing security tooling; do not add a network-dependent audit step that makes ordinary development unreliable.
- Run all checks and include exact output summaries.

---

# Prompt 18 — Full vertical E2E, release gate, and handoff

Complete and verify the portfolio-ready first release described in section 20 of the spec. Add or repair end-to-end coverage using Playwright (or the repository’s established browser E2E tool) and the deterministic fake engine. The tests must run in normal CI without GPUs, public network access, or downloaded models.

The main E2E flow must:
1. Start the local app and show privacy/local-data behavior.
2. Inspect machine information, including unknown/unverified fields as applicable.
3. Register and probe a fake engine.
4. Create a workload or import a small local fixture dataset.
5. Review and pass preflight.
6. Run a benchmark and observe live status and metrics.
7. Cancel another run and verify its final status.
8. Open run details and inspect raw/normalized metric sources.
9. Compare two runs and see a comparability explanation.
10. Export a report and verify its provenance and secret removal.

Add separate tests for API restart/reconnect, worker interruption recovery, remote endpoint disclosure without actually sending data to a public host, and zero-retention behavior.

Before finishing, run the complete documented release gate: formatter, linter, type checker, backend unit/integration tests, frontend unit tests, adapter contract suite, E2E suite, and any security checks configured by the repository. Fix regressions rather than weakening tests. Review the repository diff and remove dead code, debug prints, placeholder data, and misleading UI.

Definition of done:
- The ten-step E2E workflow passes in CI on a clean checkout.
- The global definition of done at the start of this pack is checked item by item with evidence.
- All documented local setup and test commands work from a clean environment.
- README and support matrix accurately state shipped, attach-only, managed, library, planned, and platform-limited capabilities.
- No real engine, model download, GPU, or public internet is required for the default release gate.
- The final response names the release commit/diff scope if applicable, lists exact commands and results, lists known limitations, and makes no unsupported completion claim.

If a required item cannot pass because of a concrete environment blocker, finish all independent checks, document the blocker and reproducible evidence, and leave the smallest actionable next step. Do not call the project release-ready while a required acceptance gate is still failing.

