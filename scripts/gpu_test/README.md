# LocalForge GPU engine test kit

Tests LocalForge against real inference engines on a Linux machine with an NVIDIA GPU, such as a
JupyterHub server with an A100. It needs no root access and stores everything under
`~/localforge-gpu-test`.

## What it does

1. Records the GPU, CUDA, disk and internet access in `preflight.txt`.
2. Installs [uv](https://docs.astral.sh/uv/) if missing, downloads Qwen2.5-1.5B-Instruct
   (about 3 GB), and starts LocalForge from PyPI.
3. For each engine it creates a separate virtual environment, starts the engine, runs a
   20-request LocalForge benchmark at concurrency 4, then stops the engine and deletes the
   environment.
4. Writes `~/localforge_gpu_results.zip`. The zip contains results, logs and package versions,
   with no tokens, prompts or outputs.

| Engine | How |
| --- | --- |
| vLLM, SGLang, LMDeploy, LightLLM, TensorRT-LLM, MLC LLM | pip install, attached endpoint |
| llama.cpp | built with CUDA when `nvcc` and `cmake` exist; attached and managed modes |
| Ollama | official Linux build in the work folder; also tests the generic OpenAI-compatible adapter |
| ONNX Runtime GenAI | CUDA package, model converted to int4 ONNX; library pipeline |

TGI, LocalAI and NVIDIA Dynamo are listed as skipped: they need Docker or extra services.

Expect 1.5 to 3 hours and 25 to 40 GB of downloads, most of them vLLM, SGLang and TensorRT-LLM.
Engines use at most 45% of GPU memory by default (`GPU_MEM_FRACTION`), so a shared GPU keeps
room for others.

## Run it

In a JupyterHub terminal, from the folder containing both files:

```bash
nohup bash run_gpu_tests.sh > run.log 2>&1 &
tail -f run.log        # Ctrl+C stops watching; the test keeps running
```

To test only some engines: `ENGINES="vllm sglang" nohup bash run_gpu_tests.sh > run.log 2>&1 &`

When it finishes, download `~/localforge_gpu_results.zip` from the JupyterHub file browser.
