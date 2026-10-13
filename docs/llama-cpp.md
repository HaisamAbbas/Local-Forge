# llama.cpp / llama-server

LocalForge supports two ways to use llama.cpp:

- **Attached endpoint:** connect to a llama-server that is already running on the machine. LocalForge does not start, stop, or replace it.
- **Managed process:** explicitly choose the llama-server executable, an existing .gguf model file, a working directory, and a loopback port. LocalForge starts that one process with an argument list, waits for /health, and records its process handle so it can stop only the process it launched.

GGUF model files are not downloaded, moved, or uploaded by LocalForge. The chosen executable must already be built for the local operating system and the acceleration backend the user wants to use. CUDA, Metal, Vulkan, CPU, and other backend support comes from that binary and its dependencies; LocalForge does not install those components or infer that a GPU backend works. Managed launch is currently declared for Windows, Linux, and macOS, subject to a valid user-selected binary and model.

## Tested server revision

The adapter contract and its fixture expectations are pinned to llama.cpp release **b11580**, commit
**4413a192410fad1f054e9216114087a3e01527a0**. The adapter compares the attached server's
/props.build_info with that revision and warns when it differs. A different build may work, but it
has not been version-verified by this adapter.

The implementation follows the pinned official
[server documentation](https://github.com/ggml-org/llama.cpp/blob/4413a192410fad1f054e9216114087a3e01527a0/tools/server/README.md)
and [llama-bench documentation](https://github.com/ggml-org/llama.cpp/blob/4413a192410fad1f054e9216114087a3e01527a0/tools/llama-bench/README.md).
LocalForge reports its own client-observed request measurements; it does not claim to reproduce
llama-bench, whose harness has different workloads and measurement details.

## Probes and metrics

The adapter checks /health, /props, the OpenAI-compatible /v1/models and chat routes, and
/metrics independently. /props may provide the build information, model path, context size,
and slot count. A missing or disabled optional endpoint does not erase the results of other checks.

The adapter only normalizes documented Prometheus series whose kind and unit are known for the
pinned revision. It retains each upstream metric name, labels, value, unit, observation time, and
engine_native source. Labeled series such as speculative decode counts by position remain separate.
Unknown series are ignored; malformed, non-finite, or definition-mismatched samples are skipped
with a diagnostic. The server must be started with --metrics for this endpoint to be available.
Managed launch enables that flag; attached servers control their own launch options.

## Managed launch settings

Managed mode requires absolute paths to an existing executable, existing .gguf file, and existing
working directory. The server binds to 127.0.0.1; users select its port in the local bind URL.
Optional settings map to the documented --ctx-size, --parallel, --batch-size,
--ubatch-size, and --gpu-layers arguments. --gpu-layers accepts a non-negative number, auto,
or all. The adapter validates numeric ranges, file existence, extension, local bind address, and
batch-size relationship before launch. It passes each setting as its own argument and uses the
selected working directory as the child process's current directory.

For attached mode, use **Engines → llama.cpp / llama-server → Add this adapter**, select **Attached
endpoint**, enter the existing server URL, then probe and select a reported model. Remote HTTPS
endpoints use LocalForge's existing egress confirmation flow. For managed mode, select **Managed
process** and fill in the local paths. Start the server from its profile card, then probe it and
select a reported model. Stop the server from the same card when you are finished.

## Optional live smoke test

Ordinary tests use only a local HTTP fixture and mock process; they never start llama.cpp or download
weights. To run one live request, first start a local llama-server yourself with a GGUF model that is
already present. Then set all four values below. MODEL_PATH is checked for existence to make the
preinstalled-model requirement explicit; the smoke test only attaches to the provided server.

~~~powershell
$env:LOCALFORGE_LLAMA_CPP_INTEGRATION = "1"
$env:LOCALFORGE_LLAMA_CPP_BASE_URL = "http://127.0.0.1:8080"
$env:LOCALFORGE_LLAMA_CPP_MODEL_ID = "your-existing-model-id"
$env:LOCALFORGE_LLAMA_CPP_MODEL_PATH = "C:\Models\your-model.gguf"
Set-Location backend
uv run pytest tests/test_llama_cpp_integration.py
~~~

The live test probes the server, sends one short completion, and attempts the optional metrics
read. It does not install a binary, launch or stop a server, or pull model weights.
