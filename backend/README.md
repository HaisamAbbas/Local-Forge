# LocalForge Inference Lab

LocalForge measures and compares LLM inference engines on your own machine. Connect an engine
you already run (Ollama, llama.cpp, vLLM, SGLang, an OpenAI-compatible server, and more), build a
repeatable workload, run it, and read the results as charts: time to first token, latency
percentiles, tokens per second, and host CPU and memory during the run. Compare runs side by
side to see which model or setting is faster on your hardware.

Everything stays local. The app listens only on `127.0.0.1`, keeps its data in a local SQLite
database, and never downloads models.

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
