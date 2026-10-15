# Changelog

## 0.2.0

### Find the right engine and model for this machine

- **Engine recommendations.** LocalForge ranks the inference engines that suit this machine's
  hardware and says what is already installed or running. It is shown on first start, with
  `localforge recommend` (add `--json`), and in a "Best engine fit for this machine" panel on
  Overview and Engines. The ranking is by fit, not speed: measured speeds appear only where you
  have run a benchmark.
- **Scan this machine.** One click finds engines already running on `127.0.0.1` (Ollama,
  llama.cpp, KoboldCpp, vLLM and others), then adds, probes and enables them. Each engine card
  shows a next-steps checklist (Added, Probed, Enabled, Playground, Benchmark), and Benchmark and
  Playground open with the engine and its model already chosen.
- **Hardware fit tab.** Detects this machine and grades every catalog model for it: the best
  quantization that fits, where it would run (GPU, GPU plus RAM, CPU), an estimated speed and a
  Perfect, Good, Marginal or Too large rating. Filterable, with a "Detect again" button.
- **Model suggestions.** A curated catalog of 39 open models, ranked for this machine's memory
  and the model-read speed measured in your own runs. Ollama models can be pulled with an
  explicit, in-page confirmation. Downloads are limited to Ollama's registry and Hugging Face.

### Results you can trust

- **Run conditions are recorded:** engine runtime settings (CPU threads, GPU layers, context
  size, parallel slots), a SHA-256 of the model weights, and power state (AC or battery, power
  plan).
- **Comparisons check them.** Runs that differ in threads or slots are flagged ("the speed
  difference may come from that, not the engine"); the same weights are verified identical across
  llama.cpp, KoboldCpp and Ollama; different weights are not comparable.
- **Prompt caching.** Synthetic prompts are unique per request, and runs warn when cached prompts
  likely made the first token faster.
- **Truncated prompts.** Ollama silently cut long prompts to its context window, so an "8k"
  test could measure 2k. The adapter now sizes the context to the prompt, and every engine gets
  a `prompt_truncated` warning when it counts far fewer tokens than were sent.
- **Repeat runs** report the median, range and spread, and flag unstable results.

### Does this setup do my job?

- **Use-case presets** (interactive chat, coding assistant, document Q&A, batch summarization,
  long context) carry default targets. Each run gets a verdict, "Meets your targets" or "Misses
  N targets", with an explanation in the run's own numbers.
- **Quality checks** (exact match, valid JSON, finished properly, contains) are opt-in and
  evaluated in memory; outputs are not stored unless you retain them. A built-in quality smoke
  test is included.
- A **prefill-aware estimate** says how long the first token will take for a long prompt, from
  your earlier runs.

### New screens and tools

- **Learning:** a glossary of every metric with its formula, experiment guides, and a "Diagnose
  a run" panel that describes what the data is consistent with, never a verdict.
- **Models** and **Reports** screens; run delete, clean-up and "repeat with one change"; a
  latency-versus-throughput chart; a throughput trend on Overview.
- **`localforge bench plan.json`** runs a benchmark without the browser, for scripts and CI, and
  fails on a regression against a saved baseline.
- **`localforge tune plan.json`** finds the best launch settings (CPU threads, GPU layers, batch
  size) for a managed llama.cpp by benchmarking each candidate, keeping a change only when it
  clearly beats the current best. Managed llama.cpp now also accepts a `threads` setting. The same tuner is on each managed
  llama.cpp engine card in **Engines**, with progress, stop and an explicit "Apply".
- A **GPU test kit** (`scripts/gpu_test`) and a CI job that benchmarks a real llama-server.

### Fixed

- llama.cpp managed mode failed to start unless an undocumented working directory was set, and
  start errors gave only a generic checklist.
- Model ids that are file paths (llama-server) were shown as full local paths.
- Generation speed was missing for KoboldCpp because token counts were not requested.
- OpenVINO GenAI and ONNX Runtime GenAI pipelines could not start: the worker imported
  LocalForge's own adapter module instead of the library. Probes also failed to list the model,
  and the failure message hid the real reason.
- The "Add an engine" form started with an empty endpoint (a placeholder looked like a value).

### Security

- Every request, not only mutations, must be addressed to a loopback host. This stops a web page
  from reading the session token, run history or settings through DNS rebinding.
- Model weights are hashed only for local `.gguf` files, so an engine cannot make LocalForge read
  other files.
- The local engine scan does not follow redirects, only contacts `127.0.0.1`, and caps responses.

## 0.1.0

First release: local UI and API, engine adapters, workload builder, benchmark runner, charts,
settings, hardware detection, and one-command install with `uvx localforge-inference-lab`.
