# LOCALFORGE Inference Lab

## Comprehensive product and implementation specification

**Version:** 1.0  
**Status:** Product and technical specification  
**Research checked:** 12 October 2026  
**Product form:** Local-first web application opened in a desktop browser  
**Working name:** LOCALFORGE Inference Lab

---

## 1. Executive summary

LOCALFORGE Inference Lab is a local-first application for running controlled performance experiments against large language model inference engines. A user connects or launches an engine, chooses a model and workload, runs a benchmark, observes live system and request metrics, then compares the result with other configurations.

The application is both a useful inference workbench and a learning project. It helps a learner understand how model format, quantization, prompt length, generation length, batching, concurrency, caching, and hardware affect inference. It makes those effects measurable and repeatable.

The product supports a broad engine ecosystem through capability-aware adapters. It does not assume every engine has the same API, runtime metrics, launch process, hardware support, or model format. Common measurements are normalized where possible. Engine-specific features are shown separately, and unavailable measurements are clearly marked.

The first implementation should prioritize local llama.cpp and Ollama, plus a generic OpenAI-compatible endpoint. The roadmap adds vLLM and SGLang early, then the wider engine set documented in Section 5.

---

## 2. Product definition

### 2.1 Product statement

> A private workbench for measuring, comparing, and understanding LLM inference performance across local and self-managed serving engines.

### 2.2 Problem

Developers can start an inference server and generate text, but it is difficult to answer practical engineering questions consistently:

- Which engine and model configuration best fits a given machine?
- Does quantization improve throughput enough to justify any quality loss?
- Does longer context affect time to first token, decode speed, or memory first?
- At what load does latency rise sharply or the engine run out of KV-cache capacity?
- Are two reported tokens-per-second values measured in the same way?
- Did a configuration change help, or did a different prompt set, model revision, or warmup state cause the difference?

LOCALFORGE makes the model, engine, workload, machine, and measurement method part of every experiment record.

### 2.3 Intended users

1. **Inference engineering learners** who want to study serving systems through hands-on experiments.
2. **Developers** choosing a runtime and configuration for a local or self-managed model.
3. **ML and platform engineers** who need a repeatable way to inspect latency, throughput, memory, and load behavior.
4. **Engine researchers and maintainers** who want to replay workloads and compare engine or runtime changes.

### 2.4 User benefit

- Understand what limits a run: prefill, decode, queueing, memory, network, or load-generator capacity.
- Choose a model, quantization, and engine using evidence from the target machine.
- Repeat experiments with recorded settings and comparable workload inputs.
- See which conclusions are supported by client measurements and which depend on engine-specific metrics.
- Keep prompts, outputs, credentials, and results on the user's machine by default.

### 2.5 Product principles

- **Evidence before conclusions:** show measurements, configuration, and uncertainty together.
- **Local-first:** the control plane binds to loopback by default and stores work locally.
- **Capability-aware:** only expose settings and metrics that an adapter can verify.
- **Comparable where fair:** record the details needed to decide whether two runs can be compared.
- **No silent approximation:** label estimated token counts, missing telemetry, or incompatible settings.
- **One change per experiment:** guide users toward controlled comparisons.
- **Useful on CPU-only systems:** core workflows do not require a GPU.

---

## 3. Scope

### 3.1 In scope

- Discover local hardware and software runtime versions.
- Register, test, start, stop, and inspect supported local inference engines.
- Attach to engines already running on localhost or a configured remote host.
- Create, validate, and replay benchmark workloads.
- Measure request latency, generation speed, throughput, resource use, and failures.
- Display live run state and time-series metrics.
- Compare runs and experiment variants.
- Persist configuration fingerprints, results, notes, and reports.
- Export self-contained JSON, CSV, and Markdown reports.
- Provide engine-specific configuration panels backed by adapter capability schemas.
- Include educational explanations of metrics and common bottleneck patterns.
- Support an advanced local toy-runtime laboratory for learning selected inference internals.

### 3.2 First-release exclusions

- Training or fine-tuning model weights.
- Hosting a public inference endpoint.
- Cloud account provisioning, GPU rental, billing, or autoscaling.
- Guaranteeing identical model output across engines or quantization formats.
- Replacing the internal scheduler, kernel stack, or KV-cache implementation of production engines.
- A public leaderboard ranking engines without a controlled, published benchmark method.

### 3.3 Product boundary

LOCALFORGE is the experiment control plane and benchmark client. Inference execution is performed by the selected engine or serving stack, except for the educational toy-runtime module. The app owns workload creation, process coordination where supported, request generation, metric capture, comparison, and reporting.

---

## 4. Main product workflow

1. **Inspect this machine.** Record operating system, CPU, memory, available accelerator devices, and detectable driver/runtime versions.
2. **Add an engine.** Select a built-in engine adapter, choose managed-local or attach mode, and select a model or API endpoint.
3. **Test the connection.** Check health, available models, API compatibility, stream behavior, and optional metric endpoints.
4. **Choose or build a workload.** Select a preset or dataset and configure input/output lengths, traffic pattern, concurrency, warmup, and duration.
5. **Review the benchmark plan.** Display the target, expected resource use where known, unsupported options, and estimated run duration.
6. **Run the benchmark.** Show progress, live latency/throughput/resource graphs, request outcomes, and warnings. Allow safe cancellation.
7. **Inspect the result.** Report setup, measured metrics, engine metrics, errors, and suitability for comparison.
8. **Repeat one change.** Clone the plan, change one setting, and run again.
9. **Compare and export.** Show a comparison table and charts, summarize observed differences, and export a report.

### Example learning experiment

Question: “How does concurrency change output throughput and p95 latency for this model on my A100?”

- Select the same engine, model revision, quantization, and workload for every run.
- Sweep concurrency from 1 to 16, using a fixed request dataset and output-token budget.
- Separate warmup from the measurement interval.
- Observe total output throughput, TTFT, TPOT/inter-token latency, request latency, GPU memory, and KV-cache usage if the engine exposes it.
- Identify the point where throughput stops rising while p95 latency or memory pressure continues to rise.
- Save the plan and repeat it after changing one engine option.

---

## 5. Inference-engine ecosystem and integration plan

The ecosystem contains runtimes, local model managers, API servers, and distributed serving platforms. These are represented as distinct layers. A wrapper can contain or launch an engine; an orchestration layer can coordinate one or more engines. The interface records the complete stack when known.

### 5.1 Integration modes

| Mode | Meaning | Example |
|---|---|---|
| Managed local process | LOCALFORGE launches and supervises an installed runtime using a validated command profile. | llama-server, Ollama server, vLLM |
| Attached local endpoint | The user starts the server separately; LOCALFORGE probes and sends requests to it. | vLLM, SGLang, LMDeploy, custom OpenAI-compatible server |
| Attached remote endpoint | The user supplies a reachable endpoint and optional credentials. Remote use is opt-in and clearly marked. | A self-managed GPU server |
| Library pipeline | An adapter calls a library API in a worker process rather than an HTTP server. | OpenVINO GenAI, ONNX Runtime GenAI, MLX-LM |
| Serving-stack profile | The registered target includes an orchestrator/router plus one or more worker engines. | NVIDIA Dynamo with vLLM, SGLang, or TensorRT-LLM |

### 5.2 Target engine and platform matrix

All rows below are part of the intended product ecosystem. Delivery is phased because platforms, dependencies, model formats, and runtime APIs differ.

| Engine or stack | Category and intended use | Integration approach | Target phase |
|---|---|---|---|
| **llama.cpp / llama-server** | Portable C/C++ runtime for CPU and accelerator inference, GGUF models, local and server use. | Managed process and attach adapter; OpenAI-style API; optional Prometheus metrics; CPU/GPU settings schema. | MVP |
| **Ollama** | Local model acquisition/management and HTTP serving interface, with runtime behavior dependent on its runner and model package. | Managed server and attach adapter; native API first, optional OpenAI-compatible API; capture Ollama and runner version when detectable. | MVP |
| **vLLM** | High-throughput serving engine, especially for GPU and production-style workloads. | Attach adapter first; managed Linux/CUDA launcher profile; OpenAI-compatible API and Prometheus metrics where enabled. | Phase 2 |
| **SGLang** | Production serving for language and multimodal models; caching and structured-program workloads. | Attach adapter first; managed launcher profile where platform supports it; API plus available engine metrics. | Phase 2 |
| **TensorRT-LLM** | NVIDIA-focused optimized runtime and serving integrations. | Attach to its supported API/Triton deployment; managed profile later because build and model-engine preparation are version-sensitive. | Phase 2 |
| **NVIDIA Triton + TensorRT-LLM backend** | Serving host and backend stack for NVIDIA inference deployments. | Stack adapter records Triton and backend versions, model repository, and serving configuration. | Phase 2 |
| **NVIDIA Dynamo** | Distributed inference framework and orchestration layer that can work with vLLM, SGLang, and TensorRT-LLM. | Serving-stack adapter; captures topology, router/worker roles, and aggregate metrics separately from worker-engine metrics. | Phase 3 |
| **LMDeploy** | Compression, deployment, and serving toolkit with API server and multi-GPU support. | Attach adapter; managed launcher profile and version-specific options later. | Phase 2 |
| **LightLLM** | Python-based LLM inference and serving framework with scheduling and KV-cache work. | Attach API adapter first, then managed Linux/GPU launcher and supported metrics. | Phase 2 |
| **Hugging Face TGI** | Existing Hugging Face serving system. Official documentation states TGI is in maintenance mode; it remains useful for existing deployments and historical comparisons. | External endpoint compatibility adapter and status label “maintenance”; avoid presenting it as a recommended new managed install. | Phase 2 |
| **MLX-LM** | Apple Silicon-oriented model loading, generation, and serving. | macOS library/server adapter; detect Apple Silicon and supported quantized model formats. | Phase 2 |
| **MLC LLM** | Compiled deployment engine targeting several hardware platforms and REST/API usage. | Server adapter plus build/runtime metadata; compiled model library must be recorded. | Phase 2 |
| **OpenVINO GenAI** | Intel-oriented generative inference pipelines and runtime metrics. | Library pipeline adapter first; read per-call performance metrics and system details. | Phase 2 |
| **ONNX Runtime GenAI** | Cross-platform model-generation API with language bindings and ONNX model/config workflow. | Library pipeline adapter; preserve model-builder/runtime versions and mark API/version maturity. | Phase 2 |
| **Mistral.rs** | Rust inference stack with local/server workflows and multiple backend options. | Attach or managed server adapter after API and capability verification. | Phase 2 |
| **ExLlamaV3** | Optimized inference/quantization library for modern consumer-class GPUs. | Adapter based on supported server/library interface and model format; record EXL3 and GPU requirements. | Phase 3 |
| **LocalAI** | Local API server that can expose multiple backend implementations. | Wrapper adapter records LocalAI version, selected backend, and backend-specific config when discoverable. | Phase 2 |
| **KoboldCpp** | Local GGUF-oriented runtime/server with a user-facing UI and API options. | Attach adapter; optional managed launch profile; record selected model and compatible endpoint. | Phase 2 |
| **Custom OpenAI-compatible endpoint** | Escape hatch for local or remote servers with a supported OpenAI-style chat/completion API. | User-provided base URL, model id, and optional token; endpoint feature probes; no assumptions about engine internals. | MVP |

### 5.3 Optional ecosystem expansion

The adapter contract should permit additional implementations such as Candle-based servers, Aphrodite Engine, other local API frontends, or future runtime projects without changing benchmark storage or the frontend. An engine may be listed in the catalog before managed launch support exists, provided the app accurately labels it as attach-only or planned.

### 5.4 Capability-aware support

Each adapter reports a capability document. A capability is one of:

- **Supported:** verified by the adapter for this engine version.
- **Unsupported:** the adapter knows the feature is unavailable.
- **Unknown:** the feature may exist but could not be probed.
- **User supplied:** the user confirmed a setting or statistic that could not be detected.

Capabilities include:

- Chat completion and/or raw completion.
- Streaming.
- Cancellation.
- Exact input/output token counts.
- Model inventory.
- Health endpoint.
- Prometheus or native metrics.
- Server-side time-to-first-token or token latency.
- KV-cache utilization or block counts.
- Prefix caching and cache-hit counts.
- Continuous batching and server slot count.
- Quantization metadata.
- Embeddings, reranking, image/video/audio input, and tool calling.
- Multi-GPU or distributed serving.
- Managed start/stop and process-log access.

The UI must only show engine-specific controls supported by the selected adapter and version. Unknown capabilities remain visible as unknown; they are not silently treated as false.

### 5.5 Engine research notes

- vLLM documents PagedAttention, continuous batching, prefix caching, quantization, streaming, and a Prometheus metrics endpoint with request and cache measurements.
- SGLang documents production serving, OpenAI/Hugging Face API compatibility, multiple hardware platforms, and caching/runtime capabilities.
- llama.cpp documents CPU and GPU inference, quantized models, continuous batching, OpenAI-style endpoints, and optional Prometheus metrics.
- Ollama exposes native chat and generation APIs with streaming response behavior and model-management endpoints.
- LMDeploy documents an OpenAI-compatible server and metrics such as first-token latency, token throughput, and request throughput.
- OpenVINO GenAI exposes per-call TTFT, TPOT, total generation duration, throughput, load time, and tokenization-related metrics.
- MLC LLM provides REST serving, streaming, device selection, and local/interactive/server execution modes.
- NVIDIA Dynamo is a distributed serving framework that works with vLLM, SGLang, or TensorRT-LLM; it must be represented as a composed serving stack, not as a single kernel/runtime.
- Hugging Face TGI documentation says the project is in maintenance mode. The UI should disclose that status for the connector.

---

## 6. User stories and acceptance intent

### US-01: Inspect my machine

As a learner, I want to see what compute devices and runtime versions are available so I can choose a suitable engine.

**Acceptance intent:** The app shows detectable CPU, memory, operating system, GPU/accelerator devices, and supported driver/runtime information. Undetectable values are marked unknown, with a manual-entry path for remote systems.

### US-02: Connect to an engine

As a developer, I want to attach to or launch an inference server so I can run requests through it.

**Acceptance intent:** The app tests health, model listing, streaming behavior, and feature support. It provides actionable diagnostics for failed checks.

### US-03: Save a workload

As an engineer, I want a repeatable workload with known prompts and traffic settings so I can compare configurations.

**Acceptance intent:** The workload captures request source, input sizing, output limits, concurrency or arrival rate, warmup, run duration, sampling parameters, and token-count method.

### US-04: Observe a live benchmark

As a learner, I want to understand what is happening during a run and stop it safely if the engine is overloaded.

**Acceptance intent:** The run page shows queue/running/completed/failed counts, live request and resource metrics, warnings, and a cancel control. Cancellation drains or aborts requests using the strongest supported behavior and records incomplete work.

### US-05: Find the bottleneck

As an inference learner, I want the result to distinguish first-token latency, generation latency, queueing, memory, and system limits.

**Acceptance intent:** The report separates client-side timing from engine-reported timing and labels unavailable or estimated metrics.

### US-06: Compare two configurations

As a developer, I want to compare two or more saved runs to see how a change affected performance.

**Acceptance intent:** The app shows a comparability grade and lists differences in hardware, model artifact, runtime version, workload, and measurement method before emphasizing a performance delta.

### US-07: Use my own dataset

As a researcher, I want to import a prompt set so that benchmarks resemble my own workload.

**Acceptance intent:** The app validates the file, previews a sample, supports a request limit and deterministic shuffle seed, and lets the user choose whether generated outputs are retained.

### US-08: Keep my data private

As a local-model user, I want prompts and run history to stay on my machine unless I explicitly choose a remote target or export.

**Acceptance intent:** The app binds locally by default, explains when data is sent to a configured remote endpoint, and never logs API keys or full prompt text without a visible opt-in.

### US-09: Learn an inference concept

As a learner, I want metric definitions and experiment prompts so I can connect results to inference concepts.

**Acceptance intent:** Metric labels link to concise explanations; benchmark presets include a hypothesis prompt and a short “what this experiment teaches” note.

---

## 7. Application information architecture

### 7.1 Navigation

1. Overview
2. Engines
3. Models
4. Workloads
5. Benchmarks
6. Experiments
7. Reports
8. System
9. Settings

### 7.2 Screens

#### Overview

- Active engine and target machine.
- Latest run summary.
- Throughput and latency trend.
- Resource status.
- Recent runs and warnings.
- Quick actions: add engine, create workload, run benchmark.

#### Engines

- Engine catalog and support status.
- Local installation/connection cards.
- Managed or attached state.
- Engine version, selected runner/backend, model, endpoint, and health.
- Probe, launch, stop, restart, edit, and remove actions.
- Advanced settings shown from adapter schemas.

#### Models

- Models visible to connected engines.
- Model path or remote id, format, size, quantization, architecture, context length, tokenizer metadata if known.
- Loaded/running state if the engine exposes it.
- Model artifact fingerprint and local path when available.
- No automatic model downloading without explicit user action.

#### Workloads

- Built-in workload presets.
- Dataset import and validation.
- Request-shape editor.
- Traffic pattern, warmup, duration, concurrency, target arrival rate, output token cap, and sampling settings.
- Workload preview, token-length distribution, privacy/retention settings.

#### Benchmarks

- New-run wizard.
- Live run dashboard.
- Run detail with configuration, logs, events, request samples, and metrics.
- Cancel and retry actions.

#### Experiments

- Grouped variants and one-variable-at-a-time sweeps.
- Side-by-side comparison table.
- Latency-throughput Pareto chart.
- Quality/resource trade-off chart.
- Comparability warnings and recorded user notes.

#### Reports

- Saved reports, exports, and report templates.
- Export as Markdown, JSON, and CSV.

#### System

- Current machine inventory.
- Monitoring availability.
- Storage use and run retention.
- Active benchmark resource lock.

#### Settings

- Default bind address, data directory, retention, privacy, telemetry opt-in, chart units, and advanced developer settings.

---

## 8. Detailed functional requirements

### 8.1 Onboarding and system detection

1. First launch shows the product purpose and local data policy.
2. Detect OS, CPU brand/model, logical cores, RAM, GPU/accelerator model, visible memory, and relevant drivers.
3. Detection must be best-effort and run without administrator/root privileges.
4. For remote endpoints, allow a named target profile and optional manually supplied hardware metadata.
5. Every value stores its source and detection timestamp.
6. Explain that reported hardware telemetry depends on available OS/vendor tools.

### 8.2 Engine registry and connection management

Each engine profile stores:

- Adapter id and adapter version.
- Engine display name and user label.
- Integration mode.
- Endpoint URL or launcher profile.
- Health and model-list routes where known.
- Authentication reference, never the secret in plaintext config.
- Selected model/backend when detectable.
- Engine/runtime version and probe time.
- Capability snapshot.
- Machine/target association.
- User notes.

Supported actions:

- Add, edit, test, enable, disable, start, stop, restart, remove.
- View launch command preview before managed startup.
- Copy a sanitized diagnostic bundle.
- Re-run capability probes.

Managed process rules:

- Launch using an argument array and explicit working directory; do not construct a shell command from user input.
- Store process id, start time, executable path, selected config, and a run ownership token.
- Only stop a process created and owned by LOCALFORGE.
- For attached servers, never terminate the external process.
- Capture stdout/stderr with secret redaction and configurable retention.
- Support startup timeout and health polling with a bounded retry policy.

### 8.3 Model inventory

The app can populate inventory through:

- Engine model-list endpoints.
- Runtime-specific local metadata.
- User-entered model path or identifier.
- Optional filesystem scan of user-selected model folders.

The app must not scan the entire filesystem by default. A model record stores format, model id/path, file size when known, quantization when known, model revision or hash when available, tokenizer identity when known, maximum context if known, and the source of each field.

### 8.4 Workload and dataset builder

Built-in presets:

- Single-request latency.
- Short chat interaction.
- Fixed prompt-length prefill test.
- Fixed output-length decode test.
- Concurrency sweep.
- Target request-rate test.
- Long-context test.
- Multi-turn growing-context replay.
- Structured-output/JSON validation workload.
- Optional tool-use and multimodal workload after compatible engine support exists.

Dataset inputs:

- Built-in generated prompts with a documented seed.
- JSONL import with one request per line.
- CSV import with user-mapped prompt and optional expected-answer columns.
- ShareGPT-style conversation import where licensing and schema are suitable.
- No internet fetch of datasets by default.

Validation:

- Enforce a configurable upload-size limit; default 100 MB.
- Parse incrementally rather than loading very large files into frontend memory.
- Validate field names, roles, content type, encoding, request count, and empty prompts.
- Report dropped/invalid rows and let the user download a validation summary.
- Allow deterministic random sampling by seed.
- Show estimated token-length distribution when an appropriate tokenizer exists.
- Preserve the original dataset and exact selected-row/sample hash for each run.

Traffic modes:

- **Closed-loop concurrency:** a fixed number of virtual clients issue the next request after the prior request completes.
- **Open-loop arrival rate:** requests are scheduled at a target rate independent of response completion, so queueing under offered load can be measured.
- **Trace replay:** preserve recorded arrival timestamps or multi-turn sequence ordering.
- Each mode explains what its throughput and latency mean.

Request controls:

- Prompt mode: raw completion or chat messages.
- Input prompt distribution or fixed prompt.
- Maximum output tokens.
- Sampling settings supported by the engine: temperature, top-p, top-k, seed, stop sequences, and related options.
- Warmup request count and warmup exclusion policy.
- Measurement duration, request count, concurrency, target request rate, and timeout.
- Streaming toggle when supported.
- Whether request text and generated outputs are retained.

### 8.5 Benchmark plan and preflight

Before start, display:

- Target engine, model, target machine, and expected endpoint.
- Workload and estimated request volume/runtime.
- Engine capability gaps.
- Unsupported or clamped parameters.
- Whether metrics are client-side, engine-side, approximated, or unavailable.
- Whether another run is consuming the same local device.
- Risk notices for large context, high concurrency, or insufficient available memory when detectable.

Require acknowledgement to proceed after a high-risk warning such as a predicted memory-budget exceedance. Do not present an estimate as a guarantee.

### 8.6 Run orchestration

Run lifecycle:

1. Queued.
2. Preflight.
3. Engine startup if managed.
4. Health check.
5. Warmup.
6. Measurement.
7. Cooldown and metric flush.
8. Finalization.
9. Completed, completed-with-errors, cancelled, or failed.

Each run receives a unique id and monotonic event timestamps. All phases are persisted so a crash can be shown as interrupted rather than falsely completed.

Concurrency/resource policy:

- MVP permits one active benchmark per detected local accelerator resource.
- Runs against distinct targets/devices may proceed concurrently if configured and resource detection confirms separation.
- An attached remote endpoint is locked by endpoint profile to prevent accidental overlap from this app.
- A user can override a lock only through a warning and the resulting run is marked potentially contended.
- A benchmark worker is isolated from the UI process so a hung request generator does not freeze the app.

Cancellation:

- Stop scheduling new requests immediately.
- Abort in-flight requests using adapter cancellation when supported.
- Close client connections and record all incomplete samples.
- Preserve partial measurements with status “cancelled”.
- If the engine cannot cancel a request, explain that the server may continue processing it.
- Do not kill a server unless LOCALFORGE owns that managed process and the user has selected “stop engine too”.

### 8.7 Metrics collection and definitions

Every metric stores value, unit, aggregation, sample count, source, and confidence/quality flag.

#### Core client-observed metrics

- **TTFT:** elapsed time from dispatch of a request to the first non-empty output event. For non-streaming APIs, TTFT is unavailable unless the engine reports it.
- **Inter-token latency (ITL):** elapsed time between streamed output events. If one event contains multiple tokens, label it inter-event latency rather than per-token latency.
- **TPOT:** request-level time per output token where token counts and timing permit calculation. Store exact formula and denominator.
- **End-to-end latency:** dispatch to completed response or terminal error.
- **Queue time:** local benchmark scheduler wait before dispatch, distinct from engine-internal queue time.
- **Input/output tokens:** prefer engine usage fields; otherwise use the exact model tokenizer if verified; otherwise estimate and label as estimated.
- **Prompt processing throughput:** input tokens divided by the defined prefill interval, only when that interval is measurable.
- **Generation throughput:** output tokens divided by the measured decode interval, if distinguishable.
- **Aggregate output throughput:** all completed output tokens divided by the measurement window.
- **Request throughput:** completed requests divided by the measurement window.
- **Error rate:** failed, timed-out, and cancelled request counts divided by attempted requests, with categories shown separately.
- **Percentiles:** p50, p90, p95, and p99 where sample size supports interpretation; show sample count and warn about unstable tails on small samples.

#### Optional engine/system metrics

- Engine-reported TTFT, TPOT, prefill/decode duration.
- Running, waiting, deferred, preempted, or failed request counts.
- KV-cache utilization, block count, hit rate, eviction/reuse metrics.
- CPU/GPU utilization, VRAM/RAM use, power draw, temperature, and process utilization where available.
- Model load time and cold-start time.

The UI keeps client and engine measurements in separate groups when definitions differ. Every chart shows units and source. It does not mix server-side and client-side timing in the same series without an explicit label.

### 8.8 Quality and output evaluation

Performance changes can alter output quality. The app supports lightweight evaluation:

- Exact-match checks for tasks with exact expected answers.
- JSON/schema validity.
- Stop-condition and maximum-length checks.
- User rating or side-by-side blinded review.
- Optional evaluator adapter in a later release, with its own version/cost/privacy record.

Quality evaluation is opt-in per workload. Results are not represented as a universal model-quality score. Reports show evaluation method, evaluator version, and sample count.

### 8.9 Experiment groups and sweeps

An experiment group consists of:

- A baseline plan.
- One or more variants.
- A declared independent variable, such as quantization, batch size, context length, or concurrency.
- Optional repeated trials.
- A comparison and user hypothesis.

The app supports controlled sweeps over allowed adapter settings. It must:

- Validate each setting against adapter capabilities.
- Avoid launching incompatible variants.
- Enforce a maximum run count and estimated run-time confirmation.
- Randomize variant order optionally to reduce warmup/order bias.
- Record failed variants rather than dropping them.
- Store repeat count and summarize median plus spread across repeated trials.

### 8.10 Reports and export

Markdown and JSON reports include:

- Date/time, app version, adapter version.
- Hardware and software environment fingerprint.
- Engine/runtime/orchestrator layers.
- Model artifact and quantization details.
- Workload and request sample method.
- Measurement definitions and token-count method.
- Core and engine-side metrics.
- Error breakdown and warnings.
- Comparability assessment.
- User hypothesis/notes.
- Plots or plot data.

Export must not contain secrets. Prompt/output inclusion follows the run's explicit privacy choice. The report can be shared as a local file; no automatic upload is performed.

### 8.11 Learning layer

- Every metric label has a plain-language explanation and formula.
- Preset workloads explain the concept they help investigate.
- Run reports include “What changed?” prompts rather than unsupported auto-causal claims.
- A simple diagnosis panel may suggest likely causes based on observable evidence, using wording such as “consistent with” and linking the supporting metrics.
- The app must not present heuristic diagnosis as definitive root-cause analysis.

---

## 9. Cross-engine comparison and benchmarking rules

1. **Same machine:** compare runs only when the target hardware matches, or clearly label the hardware difference.
2. **Same artifact:** record model revision, source, format, quantization, tokenizer, and conversion details. If approximate artifact equivalence is established, lower the comparability grade.
3. **Same workload:** preserve prompts, request ordering, arrival pattern, sampling settings, and output budget.
4. **Warmup policy:** keep warmup count and cold/warm state visible. Do not combine startup with steady-state generation unless the benchmark explicitly measures startup.
5. **Load generation:** record client location, CPU utilization, and request schedule where available. Warn if the client appears saturated.
6. **Tokenization:** exact token counts require the relevant tokenizer. Approximate counts are never displayed as exact.
7. **Engine metrics:** native counters are useful for diagnosis but can use different definitions. Cross-engine charts prefer standardized client measurements.
8. **Streaming:** TTFT is directly measurable only when the response streams. A non-streaming endpoint should show TTFT as unavailable or use an explicitly engine-reported value.
9. **Concurrency:** distinguish fixed-client closed-loop tests from target-RPS open-loop tests.
10. **Network:** remote endpoint latency includes network time unless a server-side metric isolates it.
11. **Quality:** compare outputs using the same check, sample set, and evaluation settings.
12. **No global winner claim:** present measured results for the selected workload and hardware, not a universal engine ranking.

### Comparability grade

- **A — Controlled:** same device, model artifact/tokenizer, workload, traffic pattern, and metric source; only the declared configuration differs.
- **B — Mostly controlled:** one or more noncritical fields differ, and all differences are visible.
- **C — Directional:** major differences exist, but the runs may still be useful for exploration.
- **Not comparable:** different workloads or model identity make a numerical comparison misleading.

Users may still view any two runs, but the grade and reasons remain visible.

---

## 10. Technical architecture

### 10.1 Recommended application stack

- **Frontend:** React and TypeScript, responsive desktop-first layout, charting library with accessible data tables.
- **Local API/control plane:** Python 3.12+ with FastAPI and Pydantic schemas.
- **Async HTTP:** HTTPX/asyncio for API adapters and streaming event parsing.
- **Worker process:** separate Python process for workload execution, system polling, and long-running tasks.
- **Persistence:** SQLite in WAL mode for configuration and run metadata; JSONL or compressed Parquet for high-volume per-request samples and time-series data.
- **Charts:** browser-rendered charts driven by stored points and aggregates.
- **Packaging:** development mode as a local web application; desktop packaging can be added after the APIs and lifecycle are stable.

The language choice keeps the benchmark client, adapters, and experiment logic approachable. A C++/CUDA or Triton-based toy-runtime module can be built separately as a deeper inference-internals exercise.

### 10.2 Runtime topology

1. The user opens the LOCALFORGE web UI served from loopback.
2. The frontend calls the local FastAPI control plane.
3. The API stores configuration and schedules jobs.
4. A run coordinator acquires resource locks and creates an isolated benchmark worker.
5. The worker talks to the selected engine adapter and samples system telemetry.
6. Events and metric batches are persisted; the frontend subscribes to live run events.
7. The report renderer reads persisted data and produces a local export.

### 10.3 Adapter contract

Each adapter implements these logical methods:

- **describe:** id, name, documentation link, supported operating systems, versions, and category.
- **validate_config:** schema and constraints.
- **probe:** health, version, model inventory, API features, and diagnostics.
- **start:** launch a managed process if supported.
- **stop:** stop only the owned process.
- **list_models.**
- **prepare_request.**
- **stream_request.**
- **abort_request** when supported.
- **read_metrics:** native counters and gauges if supported.
- **collect_fingerprint:** version, runner, backend, and runtime settings.
- **close.**

An adapter manifest declares:

- Supported API transports and routes.
- Chat/completion/embedding/multimodal capabilities.
- Streaming/cancellation/token-count behavior.
- Configuration schema and supported settings.
- Native metrics and their definitions.
- Managed-launch support by OS/accelerator.
- Known limitations and adapter version.

The backend validates every adapter result against shared schemas. Unknown engine-specific fields are stored in a namespaced extension object and never overwrite normalized fields.

### 10.4 Adapter categories

- **Native API adapter:** talks to an engine's first-party API.
- **OpenAI-compatible adapter:** uses supported chat/completion routes and probes optional routes.
- **Managed launcher adapter:** starts an installed binary using a validated command profile.
- **Library adapter:** runs a native Python library in a subprocess with a narrow IPC protocol.
- **Serving-stack adapter:** represents router/orchestrator plus worker engines and per-layer telemetry.

### 10.5 Streaming and event protocol

The worker parses streamed responses into normalized events:

- request_started
- first_output
- output_chunk
- engine_usage
- request_completed
- request_failed
- request_cancelled
- run_metric
- system_sample
- warning
- heartbeat

The local API streams run events to the frontend using Server-Sent Events or WebSocket. Events include run id, request id, monotonic offset, wall-clock timestamp, type, and sanitized payload. Large output content is stored separately based on retention settings.

### 10.6 Resource locking

Resource identifiers include:

- Local accelerator UUID/index.
- Local CPU resource group for CPU-only runs.
- Named endpoint id.
- Serving-stack id.

The lock manager prevents overlapping benchmarks on the same target unless a user explicitly overrides the warning. Locks expire after worker death and are reconciled on startup.

### 10.7 Persistence model

Core entities:

- **MachineProfile:** OS, CPU, memory, accelerator devices, drivers, detection provenance.
- **EngineAdapter:** stable adapter id, version, category, capability schema.
- **EngineProfile:** connection/launch config, endpoint, target machine, secret reference.
- **CapabilitySnapshot:** probe timestamp, supported/unknown/unsupported flags, diagnostics.
- **ModelArtifact:** model id/path, source, format, quantization, revision/hash, tokenizer metadata.
- **Dataset:** file metadata, schema mapping, content hash, privacy/retention policy.
- **Workload:** traffic pattern, prompt source, token bounds, sampling options, warmup, limits.
- **BenchmarkPlan:** engine, model, workload, options, expected run volume.
- **BenchmarkRun:** lifecycle, fingerprints, timestamps, run status, error summary.
- **RequestSample:** per-request start/first-token/end times, counts, status, optional retained content.
- **MetricSample:** metric id, value, unit, source, timestamp, labels.
- **ExperimentGroup:** baseline, variants, declared variable, repeated trials.
- **Report:** run or group, generation version, export metadata.

All persisted records include created/updated timestamps and schema version. Database migrations are forward-only and backed up before destructive migrations.

### 10.8 Backend API surface

All app routes are versioned under **/api/v1**.

- **GET /health**
- **GET /system**
- **GET /engines/catalog**
- **GET /engines**
- **POST /engines**
- **GET /engines/{id}**
- **PATCH /engines/{id}**
- **DELETE /engines/{id}**
- **POST /engines/{id}/probe**
- **POST /engines/{id}/start**
- **POST /engines/{id}/stop**
- **GET /engines/{id}/models**
- **GET /workloads**
- **POST /workloads**
- **POST /datasets/validate**
- **POST /datasets/import**
- **POST /benchmark-plans/validate**
- **POST /runs**
- **GET /runs**
- **GET /runs/{id}**
- **POST /runs/{id}/cancel**
- **GET /runs/{id}/samples**
- **GET /runs/{id}/events**
- **POST /experiments**
- **GET /experiments/{id}**
- **POST /comparisons**
- **POST /reports/export**

Every mutating route validates input, returns a stable error code, and records an audit event. Long tasks return an id and status rather than holding the HTTP request open.

---

## 11. Frontend interaction details

### 11.1 Add-engine wizard

1. Select an engine category or search for an engine name.
2. Choose managed local, attach local, attach remote, or library pipeline where available.
3. Enter endpoint or executable/model paths.
4. Select authentication method if needed.
5. Probe and display discovered models, version, health, and supported capabilities.
6. Review warnings and save the connection profile.

Fields that the adapter cannot verify are clearly marked “user supplied”.

### 11.2 New benchmark wizard

1. Select an engine profile and model.
2. Select a preset workload or import a dataset.
3. Choose closed-loop concurrency, open-loop request rate, or trace replay.
4. Set warmup, target duration/request count, input/output token limits, sampling parameters, and timeouts.
5. Choose output-retention policy and optional quality checks.
6. View preflight, unsupported options, and resource contention.
7. Confirm and start.

The user may save the plan without running it.

### 11.3 Live-run page

The layout contains:

- Run status and elapsed time.
- Current engine, model, workload, and target.
- Progress: warmup, measurement, request counts, failures, cancellation state.
- Main chart: output throughput and p95 latency with distinct axes and units.
- Metrics panel: TTFT, ITL/TPOT, request latency, token throughput, resource use, cache use if available.
- Event log with timestamps and severity.
- Stop button, which opens a compact cancellation summary if requests are still active.

The UI uses sampled/aggregated chart points to avoid overwhelming the browser during high event rates.

### 11.4 Run-detail page

Tabs:

- Summary
- Latency
- Throughput
- Resource use
- Engine metrics
- Requests and errors
- Configuration
- Events and logs

The summary keeps the setup fingerprint visible. A metric tooltip explains formula, source, sample count, and limitations.

### 11.5 Compare page

- Select two or more runs or an experiment group.
- Show comparability grade and field-by-field differences.
- Use a throughput-versus-latency scatter plot with Pareto frontier only when axes and measurement definitions match.
- Show memory/quality trade-offs in separate views.
- Permit filtering by model, engine, hardware, workload, date, and status.
- Export the comparison with the selected runs' metadata.

---

## 12. Edge cases and required behavior

### 12.1 Engine setup and connectivity

| Edge case | Required behavior |
|---|---|
| Engine executable is missing | Show installation/documentation link and keep the profile disconnected. |
| Endpoint refuses connection | Show address, error class, retry result, and safe troubleshooting steps. |
| Health endpoint exists but model list does not | Mark health as reachable and model inventory unknown; allow manual model id. |
| Port is occupied | Show the conflicting port and offer a different port; never kill the occupying process. |
| Startup exceeds timeout | Stop launching new checks, offer log inspection, and only terminate the process if it is owned by LOCALFORGE. |
| Authentication fails | Mask credentials and distinguish unauthorized from unreachable. |
| Remote endpoint has invalid TLS certificate | Fail safely by default; allow a clearly labeled advanced override only for a user-controlled endpoint. |
| Engine version changes after profile creation | Re-probe capabilities, record a new snapshot, and flag settings that are no longer recognized. |
| Adapter cannot detect underlying runner | Record wrapper engine and runner as unknown; do not infer from name alone. |

### 12.2 Model and hardware compatibility

| Edge case | Required behavior |
|---|---|
| Model format is unsupported | Explain the adapter's supported formats and block managed launch. |
| Model file is incomplete or corrupt | Fail preflight with a clear load diagnostic; preserve the profile and existing run data. |
| Tokenizer is unavailable | Allow the run if the engine accepts it; label locally computed token metrics unavailable or estimated. |
| Chat template differs across engines | Warn that chat rendering may change token count and output; offer raw prompt mode where supported. |
| Context exceeds model or engine limit | Validate known limits; if unknown, warn and record engine rejection. |
| No GPU is present | Offer CPU-compatible adapters and hide GPU-only controls. |
| GPU backend/driver is unsupported | Probe and show the exact runtime/driver mismatch when available. |
| Multiple accelerators exist | Let the user select devices and record device ids; avoid defaulting silently to all GPUs. |
| Remote hardware cannot be inspected | Allow user-supplied metadata and mark it unverified. |
| Runtime reports less memory than OS probe | Preserve both sources and show their sampling/source labels. |

### 12.3 Workloads and data

| Edge case | Required behavior |
|---|---|
| Empty dataset or no valid rows | Block run and show row-level validation summary. |
| Malformed JSONL/CSV | Identify line/row, field, and parse error; do not import partial data silently. |
| Dataset too large | Stream validation, enforce configured size limits, and show estimated disk use. |
| Prompt contains sensitive material | Default to prompt-content redaction in logs and exports; make retention an explicit choice. |
| Dataset changes after a plan was saved | Run against stored snapshot/hash or ask the user to refresh; never silently change the workload. |
| Duplicate rows | Preserve by default; offer deterministic deduplication with before/after counts. |
| Multimodal row references local paths or URLs | Validate accessible file types; remote URL fetch requires explicit configuration and is recorded. |
| Output grows beyond configured limit | Let engine stop at max tokens, classify length stop separately, and record actual output length. |

### 12.4 Benchmark execution and measurement

| Edge case | Required behavior |
|---|---|
| Engine returns a non-streaming response | Measure end-to-end latency; mark client TTFT and ITL unavailable. |
| Stream chunks contain several tokens | Record chunk/event latency; only calculate per-token values when token count is reliable. |
| Engine omits usage token counts | Use the model tokenizer if verified; otherwise estimate or show unavailable. |
| Server-side metrics are disabled | Continue with client-side metrics and show native telemetry as unavailable. |
| Engine returns inconsistent counters | Store raw values, detect invalid/decreasing counters, and exclude bad samples with explanation. |
| Warmup fails | Do not silently enter measurement; allow retry or record a warmup-failed run. |
| Request times out | Record timeout class and elapsed time, continue or abort based on plan policy. |
| Partial stream then disconnect | Save partial timing/output according to retention policy and classify as interrupted. |
| Process crashes or host sleeps | Mark run interrupted; preserve completed samples and show the last event. |
| OOM or engine preemption | Stop new dispatches, capture available logs/telemetry, and mark memory-pressure failure. |
| User cancels during warmup | Cancel safely and retain a cancelled run record. |
| Small sample count makes p95/p99 unstable | Show sample count and statistical warning; avoid drawing a misleading smooth tail chart. |
| Load generator is saturated | Warn that the client may cap offered load; label measured server throughput as potentially load-generator limited. |
| System clock changes | Use monotonic clocks for durations and wall-clock timestamps only for display/correlation. |
| Multiple benchmarks contend for a device | Enforce resource lock or mark an explicit override as contended. |

### 12.5 Comparison and reporting

| Edge case | Required behavior |
|---|---|
| Different model revision or quantization | Lower comparability grade and show exact differences. |
| Different hardware or remote network path | Keep comparison visible but label it directional. |
| Different tokenization | Do not present tokens-per-second as directly equivalent without warning. |
| Different request order or traffic pattern | Mark workload mismatch and block “controlled” badge. |
| Missing native metrics in one run | Compare shared client metrics and show missing fields as unavailable. |
| User exports with secrets or hidden prompts | Strip secrets always; include prompts/outputs only when the user explicitly enabled retention/export. |
| Export path is unwritable or disk is full | Report error without deleting run history. |
| Database is busy or corrupted | Retry bounded transient locks; preserve a backup before repair and explain recovery outcome. |
| App upgrade changes metric schema | Migrate stored records and retain original metric definition/version metadata. |

---

## 13. Privacy and security requirements

1. Bind the local web server to 127.0.0.1 by default.
2. Do not expose the API on a LAN interface without an explicit advanced setting and warning.
3. Require a per-session local token or equivalent request-origin protection for mutating API calls.
4. Apply strict CORS and CSRF protections.
5. Store API credentials in the OS credential store where available; otherwise encrypt using a local key protected by OS facilities. Do not put secrets in SQLite plaintext.
6. Never include credentials, authorization headers, or secret query parameters in logs, exports, exception messages, or frontend error displays.
7. Keep prompt and output retention disabled or redacted by default. Keep timing and aggregate measurements even when content is not retained.
8. For remote endpoints, show a pre-run disclosure that prompts and model requests will leave the machine.
9. Require explicit user confirmation before downloading models or datasets.
10. Use safe subprocess invocation with argument arrays, executable validation, bounded environment variables, and controlled working directories.
11. Do not run user-provided shell scripts in the default release.
12. Provide delete-run, delete-all-run-content, and retention controls; explain that deletion is irreversible.
13. Prevent path traversal in imported files and report exports.
14. Keep a clear record of any optional network access, update check, or telemetry. Telemetry is off by default.

---

## 14. Non-functional requirements

### Reliability

- UI remains responsive while a benchmark is running.
- Worker crashes do not corrupt the SQLite database.
- App restart recovers run status and marks incomplete runs interrupted.
- Run cancellation is bounded and leaves no LOCALFORGE-owned child process unintentionally running.

### Measurement integrity

- Use monotonic clocks for measured durations.
- Store raw request samples before calculating aggregates.
- Store aggregation formulas, units, sample counts, and data source.
- Make benchmark-plan JSON exportable and replayable.
- Record all configuration fields that can affect performance.
- Include a warning when the application itself may perturb the benchmark.

### Performance

- A benchmark worker must sustain the configured workload without serializing the HTTP client.
- Run/event persistence is batched to limit disk overhead.
- The frontend limits and downsamples chart points while retaining raw samples for export.
- App overhead is measured in a low-load calibration run and documented.

### Portability

- Core UI/API/data layer should run on Windows, Linux, and macOS.
- Individual adapters declare their supported OS, hardware, and dependency matrix.
- Unsupported adapters remain visible with a concise platform requirement explanation.

### Accessibility and usability

- All charts have a tabular equivalent.
- Keyboard navigation and visible focus are supported.
- Status is communicated by text and icon in addition to color.
- Metric definitions are reachable from the value and from a glossary.

---

## 15. Visual and interaction direction

The mockups establish a dark, technical, professional dashboard style:

- Graphite/navy surfaces with restrained cyan and amber accents.
- Clear typography, thin separators, aligned numeric columns, compact but readable tables.
- Charts emphasize axes, units, sample counts, and comparison context.
- One main chart per page with a supporting metrics panel.
- Avoid decorative AI imagery, glass panels, excessive gradients, and marketing-hero layouts.

Primary visual screens:

1. Overview: throughput versus concurrency, current machine status, recent runs, and one evidence-backed insight.
2. Live benchmark: time-series chart, run configuration, live metrics, event log, and stop control.
3. Experiment comparison: throughput-versus-latency chart, comparability notice, experiment table, and exported finding.

The generated examples use illustrative values. The implementation must show actual measurements, use units, and never treat mock values as defaults.

---

## 16. Delivery plan

### Phase 0 — Foundations

- Repo structure, app shell, local API, SQLite migrations, typed shared schemas.
- System profile detection on the developer's primary OS.
- Adapter interface, capability document, fake deterministic adapter.
- Run lifecycle, event persistence, resource lock.

### Phase 1 — Useful local benchmark app

- Overview, engine list, workload builder, new-run wizard, live run, run detail, comparisons.
- Managed/attached llama.cpp server support.
- Ollama server adapter.
- Generic OpenAI-compatible endpoint adapter.
- Dataset import, prompt privacy choices, core client measurements, reports.

### Phase 2 — Production-serving engines

- vLLM and SGLang attach adapters and managed Linux/CUDA launch profiles.
- Engine-native metrics ingestion and capability probes.
- LMDeploy, LightLLM, TensorRT-LLM/Triton connector profiles.
- TGI external endpoint support with maintenance-status disclosure.

### Phase 3 — Platform and serving-stack breadth

- NVIDIA Dynamo stack adapter and distributed topology view.
- MLX-LM, MLC LLM, OpenVINO GenAI, ONNX Runtime GenAI, Mistral.rs, ExLlamaV3.
- LocalAI and KoboldCpp wrapper adapters.
- Multi-device and distributed-target support.

### Phase 4 — Deep learning labs

- Small educational autoregressive runner with explicit KV-cache visibility.
- Paged KV-cache allocation experiment.
- Continuous-batching scheduler demonstration.
- Optional C++/CUDA or Triton kernel lab with correctness checks and before/after profiling.

Phases are additive. Each adapter can be released as a plugin without waiting for every ecosystem integration to be complete.

---

## 17. Acceptance gates

### Product acceptance

- A new user can connect an engine, run a preset workload, inspect metrics, compare a second run, and export a report without editing configuration files.
- CPU-only users can complete the full core workflow.
- Engine connection mode and capabilities are visible wherever the engine is referenced.
- The user can tell what the app measured, what the engine reported, and what the app estimated.

### Measurement acceptance

- Each run persists raw per-request timings and normalized aggregates.
- TTFT is only shown when measured from streaming or reported by the engine with a source label.
- ITL and TPOT use separately defined fields and do not get conflated.
- Errors, timeouts, cancellations, and incomplete samples remain visible in the report.
- A comparison grade changes when hardware, artifact, workload, or metric source differs.
- Benchmark plan export and re-import reproduce the same workload configuration.

### Adapter acceptance

- Adapter health probe has deterministic success/failure results in integration tests.
- Adapter returns declared capabilities and does not invent unsupported ones.
- Streaming parser handles normal completion, error events, early close, and cancellation.
- Managed process adapter never stops an unattached process.
- Each supported adapter documents platform/dependency limitations and version-tested status.

### Security acceptance

- Local API is loopback-only by default.
- Secrets are redacted from logs, errors, and exports.
- Remote endpoint configuration produces a clear data-egress prompt.
- File import and export paths cannot escape the selected app directory through traversal.

---

## 18. Key implementation decisions

1. Keep the app local-first and make all remote connections explicit.
2. Treat benchmark plans and adapters as versioned data contracts.
3. Begin with llama.cpp and Ollama native adapters, plus generic OpenAI-compatible attach mode; add vLLM and SGLang early because they expose production-serving concepts and fit the user's inference-engineering path.
4. Separate portable client-side metrics from engine-native metrics.
5. Use the same event and storage model for local runtimes, API servers, library pipelines, and serving-stack profiles.
6. Keep integrations capability-based so adding a new engine does not require designing a new application screen.
7. Preserve original raw data and configuration snapshots; derive summaries from those records.
8. Add low-level runtime experiments as a separate learning module so the main benchmark workflow remains useful before a custom engine exists.

---

## 19. Official research references

References below are first-party project documentation or repositories. Engine status and APIs evolve; the adapter catalog should re-check versions and capabilities when each integration is implemented.

### GPU and production-serving engines

- [vLLM documentation](https://docs.vllm.ai/en/stable/) — serving features, API support, quantization, batching, KV-cache, and developer guides.
- [vLLM metrics design](https://docs.vllm.ai/en/stable/design/metrics/) — Prometheus metrics including request latency, TTFT, TPOT/ITL, queueing, and KV-cache measurements.
- [SGLang documentation](https://docs.sglang.ai/) — production serving, hardware, APIs, runtime, and installation.
- [SGLang serving benchmark guide](https://docs.sglang.io/developer_guide/bench_serving.html) — workload and benchmark tooling.
- [TensorRT-LLM documentation](https://nvidia.github.io/TensorRT-LLM/) — runtime and benchmarking.
- [TensorRT-LLM user guide in Triton](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/getting_started/trtllm_user_guide.html) — serving stack integration.
- [NVIDIA Dynamo documentation](https://docs.nvidia.com/dynamo/latest/) — distributed inference orchestration and supported engine backends.
- [LMDeploy OpenAI-compatible server](https://lmdeploy.readthedocs.io/en/latest/llm/api_server.html) and [versioned LMDeploy API benchmark metrics](https://lmdeploy.readthedocs.io/en/v0.3.0/benchmark/profile_api_server.html).
- [LightLLM repository](https://github.com/ModelTC/LightLLM) — inference/serving framework and project documentation.
- [Hugging Face TGI documentation](https://huggingface.co/docs/text-generation-inference/en/index) — official page indicates maintenance-mode status.

### Local, cross-platform, and specialized runtimes

- [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/tree/master/tools/server) — local/server APIs, batching, metrics, and model support.
- [llama.cpp server benchmark guide](https://github.com/ggml-org/llama.cpp/tree/master/tools/server/bench) — server load testing and benchmark clients.
- [Ollama API introduction](https://docs.ollama.com/api/introduction), [chat API](https://docs.ollama.com/api/chat), and [generate API](https://docs.ollama.com/api/generate).
- [MLX-LM repository](https://github.com/ml-explore/mlx-lm) — Apple Silicon language model tools.
- [MLC LLM documentation](https://llm.mlc.ai/docs/) and [REST API](https://llm.mlc.ai/docs/deploy/rest.html).
- [OpenVINO GenAI performance metrics](https://openvinotoolkit.github.io/openvino.genai/docs/guides/performance-metrics/).
- [ONNX Runtime GenAI API documentation](https://onnxruntime.ai/docs/genai/api/) — API preview/maturity note is documented by the project.
- [Mistral.rs repository](https://github.com/EricLBuehler/mistral.rs).
- [ExLlamaV3 repository](https://github.com/turboderp-org/exllamav3).
- [LocalAI documentation](https://localai.io/).
- [KoboldCpp repository](https://github.com/LostRuins/koboldcpp).

---

## 20. Final product test

The project is ready for a first public portfolio demo when a user can:

1. Open the app locally and understand its data/privacy behavior.
2. Connect to llama.cpp or Ollama and attach to vLLM or SGLang when those servers are available.
3. Run the same saved workload at multiple concurrency or configuration settings.
4. Observe live TTFT, latency, throughput, resource data, and failures with honest source labels.
5. Compare the runs and see a useful comparability explanation.
6. Export a report another engineer can reproduce.
7. Explain from the resulting data what changed, what remained uncertain, and what inference concept the experiment demonstrated.

