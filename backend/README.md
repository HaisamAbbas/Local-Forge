<p align="center"><img alt="LocalForge icon" height="96" src="https://raw.githubusercontent.com/HaisamAbbas/Local-Forge/main/frontend/public/icon-512.png" width="96" /></p>

<h1 align="center">LocalForge Inference Lab</h1>

<p align="center">
  <a href="https://github.com/HaisamAbbas/Local-Forge/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/HaisamAbbas/Local-Forge/actions/workflows/ci.yml/badge.svg" /></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue" />
</p>

Find out which open-source Large Language Models (LLMs) and inference engines your hardware runs
best, and prove it with real numbers. LocalForge inspects your CPU, system RAM, GPU(s) and
accelerators, grades models across popular quantizations, then benchmarks the engines you choose
(Ollama, llama.cpp, KoboldCpp, vLLM, OpenVINO and more) on your own machine. Build a repeatable
workload, run it, and read the results as charts: time to first token, latency percentiles, tokens
per second, and host CPU and memory during the run. Compare runs side by side to see which model
or setting is faster on your hardware.

**New: hardware fit and launch tuning.** The **Hardware fit** tab detects your machine and rates
every catalog model *Perfect*, *Good*, *Marginal* or *Too large* for it, with the best
quantization, where it would run (GPU, GPU plus RAM, or CPU) and an estimated speed. Then **Tune
launch settings** benchmarks CPU threads, GPU layers and batch size for a llama.cpp server and
keeps a change only when it clearly helps. Comparisons record the engine settings, model weights
and power state of each run, so a faster result is never credited to the wrong thing.

<p align="center"><img alt="The Hardware fit screen: detected hardware and a graded model list" src="https://raw.githubusercontent.com/HaisamAbbas/Local-Forge/main/docs/images/hardware-fit.jpg" width="900" /></p>

Everything stays local. The app listens only on `127.0.0.1`, keeps its data in a local SQLite
database, and downloads a model only when you confirm it.

## A tour of the app

<p align="center"><img alt="Engines: ranked engine recommendations for this machine, with measured speeds from your own runs." src="https://raw.githubusercontent.com/HaisamAbbas/Local-Forge/main/docs/images/engines.jpg" width="900" /><br /><sub>Engines: ranked engine recommendations for this machine, with measured speeds from your own runs.</sub></p>

<p align="center"><img alt="Models: models suggested for this machine, with one-click pull through Ollama after you confirm." src="https://raw.githubusercontent.com/HaisamAbbas/Local-Forge/main/docs/images/models.jpg" width="900" /><br /><sub>Models: models suggested for this machine, with one-click pull through Ollama after you confirm.</sub></p>

<p align="center"><img alt="Runs: a finished run with time to first token, latency, generation speed and per-request charts." src="https://raw.githubusercontent.com/HaisamAbbas/Local-Forge/main/docs/images/run-detail.jpg" width="900" /><br /><sub>Runs: a finished run with time to first token, latency, generation speed and per-request charts.</sub></p>

<p align="center"><img alt="Experiments: a comparison that says when two runs are not comparable, and why, instead of crowning a winner." src="https://raw.githubusercontent.com/HaisamAbbas/Local-Forge/main/docs/images/comparison.jpg" width="900" /><br /><sub>Experiments: a comparison that says when two runs are not comparable, and why, instead of crowning a winner.</sub></p>

<p align="center"><img alt="Learning: a glossary of every metric with its formula, plus experiment guides." src="https://raw.githubusercontent.com/HaisamAbbas/Local-Forge/main/docs/images/learning.jpg" width="900" /><br /><sub>Learning: a glossary of every metric with its formula, plus experiment guides.</sub></p>

## Install and run

You need [uv](https://docs.astral.sh/uv/) and an inference engine with a model, for example
[Ollama](https://ollama.com) with `ollama pull llama3.2:1b`.

```shell
uvx localforge-inference-lab
```

This starts LocalForge and opens it in your browser. Press Ctrl+C in the terminal to stop it.
To keep a `localforge` command installed instead:

```shell
uv tool install localforge-inference-lab
localforge
```

### Options

| Option | Purpose |
| --- | --- |
| `--port 8123` | Listen on a specific port. By default 8000, or a free port if it is taken. |
| `--data-dir PATH` | Keep the database and datasets in another folder, for example a bigger drive. |
| `--no-browser` | Start without opening a browser tab. |
| `--version` | Print the installed version. |

## First benchmark

1. **Engines**: add your engine (for Ollama keep the default `http://127.0.0.1:11434`), probe it,
   and pick a model.
2. **Workloads**: choose a preset, save the plan, and launch it.
3. **Runs**: follow the run live and read its charts and measurements.
4. **Experiments**: select several runs and compare them.

Source, documentation, and the development setup are at
<https://github.com/HaisamAbbas/Local-Forge>.
