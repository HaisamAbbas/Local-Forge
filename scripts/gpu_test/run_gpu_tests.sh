#!/usr/bin/env bash
# LocalForge GPU engine test kit.
#
# Installs LocalForge from PyPI and each engine in its own virtual environment, serves one small
# model, benchmarks it through LocalForge, stops it, and moves on. Failures are recorded and
# skipped. Everything stays under $LF_WORKDIR; nothing needs root. Results end up in
# localforge_gpu_results.zip (no tokens, passwords, prompts or outputs).
#
#   nohup bash run_gpu_tests.sh > run.log 2>&1 &      # survives a closed browser tab
#   tail -f run.log
#
# Options (environment variables):
#   ENGINES="vllm sglang ..."   engines to test (default: all below)
#   LF_WORKDIR=~/localforge-gpu-test
#   GPU_MEM_FRACTION=0.45       share of GPU memory engines may use (keep < 0.5 on shared GPUs)
#   KEEP_VENVS=0                1 keeps each engine's virtual environment for reruns
#   REQUESTS=20 CONCURRENCY=4   benchmark size per engine

set -uo pipefail

ALL_ENGINES="vllm sglang lmdeploy lightllm tensorrt_llm llama_cpp ollama onnxruntime_genai mlc_llm"
ENGINES="${ENGINES:-$ALL_ENGINES}"
W="${LF_WORKDIR:-$HOME/localforge-gpu-test}"
GPU_MEM="${GPU_MEM_FRACTION:-0.45}"
KEEP_VENVS="${KEEP_VENVS:-0}"
REQUESTS="${REQUESTS:-20}"
CONCURRENCY="${CONCURRENCY:-4}"
HF_MODEL="Qwen/Qwen2.5-1.5B-Instruct"
SERVED="qwen2.5-1.5b"
LF_PORT=8765
LF_API="http://127.0.0.1:$LF_PORT"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

R="$W/results"; L="$R/logs"
mkdir -p "$W"/{models,venvs,engines,lf-data} "$R/engines" "$L"
SUMMARY="$R/summary.tsv"
printf 'engine\tresult\tseconds\tdetail\n' > "$SUMMARY"
export PATH="$HOME/.local/bin:$PATH"
export HF_HOME="$W/hf-cache" UV_CACHE_DIR="$W/uv-cache" PIP_DISABLE_PIP_VERSION_CHECK=1
export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 VLLM_NO_USAGE_STATS=1

log() { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }
record() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$SUMMARY"; log "$1: $2 ${4:+($4)}"; }

SERVER_PID=""
start_server() {  # start_server NAME COMMAND... (own process group, logs to $L/NAME.log)
  local name="$1"; shift
  setsid "$@" > "$L/$name.log" 2>&1 < /dev/null &
  SERVER_PID=$!
}
stop_server() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM -- "-$SERVER_PID" 2>/dev/null; sleep 8
    kill -KILL -- "-$SERVER_PID" 2>/dev/null
  fi
  SERVER_PID=""; sleep 3
}
wait_http() {  # wait_http URL TIMEOUT_SECONDS
  local deadline=$((SECONDS + $2))
  while [ $SECONDS -lt "$deadline" ]; do
    curl -fs -o /dev/null "$1" && return 0
    if [ -n "$SERVER_PID" ] && ! kill -0 "$SERVER_PID" 2>/dev/null; then return 1; fi
    sleep 5
  done
  return 1
}
make_venv() {  # make_venv NAME [PYTHON] -> prints the venv path
  local dir="$W/venvs/$1"
  rm -rf "$dir"
  uv venv --quiet --python "${2:-3.12}" "$dir" >/dev/null && echo "$dir"
}
pip_into() { local venv="$1"; shift; uv pip install --quiet --python "$venv/bin/python" "$@"; }
versions() { uv pip list --python "$1/bin/python" 2>/dev/null > "$R/engines/$2.packages.txt"; }
drop_venv() { [ "$KEEP_VENVS" = 1 ] || rm -rf "$W/venvs/$1"; }
bench() {  # bench NAME ADAPTER ENDPOINT MODEL [MODE] [EXTENSIONS_JSON]
  nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader \
    > "$R/engines/$1.gpu-before.txt" 2>&1
  ( while sleep 5; do nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used \
      --format=csv,noheader; done > "$R/engines/$1.gpu-during.csv" 2>&1 ) &
  local sampler=$!
  python3 "$HERE/bench_engine.py" --api "$LF_API" --adapter "$2" --label "$1" \
    --endpoint "$3" --model "$4" --mode "${5:-attached_endpoint}" --extensions "${6:-}" \
    --requests "$REQUESTS" --concurrency "$CONCURRENCY" --out "$R/engines/$1.json"
  local status=$?
  kill "$sampler" 2>/dev/null
  return $status
}
run_engine() {  # run_engine NAME FUNCTION
  # Runs in this shell (not a subshell) so stop_server always sees the engine's process group.
  local started=$SECONDS code detail notes="$R/engines/$1.notes.txt"
  log "===== $1 ====="
  "$2" > "$notes" 2>>"$L/$1.setup.log"; code=$?
  stop_server
  detail=$(grep -v '^{' "$notes" | tail -n 1)
  case $code in
    0) record "$1" pass $((SECONDS - started)) "" ;;
    2) record "$1" skip $((SECONDS - started)) "${detail:-skipped}" ;;
    *) record "$1" fail $((SECONDS - started)) "${detail:-see results/engines/$1.json and logs/$1.*}" ;;
  esac
}

# ---------------------------------------------------------------- preflight
log "Preflight"
{
  echo "date: $(date -u +%FT%TZ)"; uname -a; echo
  nvidia-smi || echo "nvidia-smi: not available"; echo
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 2>/dev/null; echo
  (command -v nvcc && nvcc --version) || echo "nvcc: not found (llama.cpp CUDA build skipped)"
  echo; python3 --version; nproc; free -g; df -h "$W"
  for tool in git cmake docker zstd; do printf '%s: %s\n' "$tool" "$(command -v $tool || echo missing)"; done
  for url in https://pypi.org/simple/ https://huggingface.co https://github.com https://ollama.com; do
    printf '%s: %s\n' "$url" "$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$url")"
  done
} > "$R/preflight.txt" 2>&1
cat "$R/preflight.txt"
command -v nvidia-smi >/dev/null || { log "No NVIDIA GPU visible; stopping."; exit 1; }

if ! command -v uv >/dev/null; then
  log "Installing uv into ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | sh || { log "uv install failed"; exit 1; }
fi

log "Downloading $HF_MODEL (about 3 GB)"
MODEL="$W/models/qwen2.5-1.5b-instruct"
uvx --quiet --from huggingface_hub hf download "$HF_MODEL" --local-dir "$MODEL" \
  > "$L/model-download.log" 2>&1 || { log "Model download failed; see logs"; exit 1; }

log "Starting LocalForge from PyPI"
uv tool install --quiet --force localforge-inference-lab > "$L/localforge-install.log" 2>&1
localforge --version > "$R/localforge-version.txt" 2>&1
setsid localforge --no-browser --port "$LF_PORT" --data-dir "$W/lf-data" \
  > "$L/localforge.log" 2>&1 < /dev/null &
LF_PID=$!
trap 'stop_server; kill -TERM -- "-$LF_PID" 2>/dev/null' EXIT
wait_http "$LF_API/health" 120 || { log "LocalForge did not start; see logs/localforge.log"; exit 1; }
python3 "$HERE/bench_engine.py" --api "$LF_API" --machine-only --out "$R/machine-profile.json"

# ---------------------------------------------------------------- engines
test_vllm() {
  local v; v=$(make_venv vllm) && pip_into "$v" vllm || { echo "pip install vllm failed"; return 1; }
  versions "$v" vllm
  start_server vllm "$v/bin/vllm" serve "$MODEL" --served-model-name "$SERVED" --host 127.0.0.1 \
    --port 8101 --gpu-memory-utilization "$GPU_MEM" --max-model-len 4096
  wait_http http://127.0.0.1:8101/v1/models 900 || { echo "server did not become ready"; return 1; }
  bench vllm localforge.vllm http://127.0.0.1:8101 "$SERVED"; local s=$?
  stop_server; drop_venv vllm; return $s
}
test_sglang() {
  local v; v=$(make_venv sglang) && pip_into "$v" "sglang[all]" || { echo "pip install sglang failed"; return 1; }
  versions "$v" sglang
  start_server sglang "$v/bin/python" -m sglang.launch_server --model-path "$MODEL" \
    --served-model-name "$SERVED" --host 127.0.0.1 --port 8102 --mem-fraction-static "$GPU_MEM" \
    --context-length 4096
  wait_http http://127.0.0.1:8102/v1/models 900 || { echo "server did not become ready"; return 1; }
  bench sglang localforge.sglang http://127.0.0.1:8102 "$SERVED"; local s=$?
  stop_server; drop_venv sglang; return $s
}
test_lmdeploy() {
  local v; v=$(make_venv lmdeploy) && pip_into "$v" lmdeploy || { echo "pip install lmdeploy failed"; return 1; }
  versions "$v" lmdeploy
  start_server lmdeploy "$v/bin/lmdeploy" serve api_server "$MODEL" --server-name 127.0.0.1 \
    --server-port 8103 --model-name "$SERVED" --cache-max-entry-count "$GPU_MEM" --session-len 4096
  wait_http http://127.0.0.1:8103/v1/models 900 || { echo "server did not become ready"; return 1; }
  bench lmdeploy localforge.lmdeploy http://127.0.0.1:8103 "$SERVED"; local s=$?
  stop_server; drop_venv lmdeploy; return $s
}
test_lightllm() {
  command -v git >/dev/null || { echo "git missing"; return 2; }
  local v; v=$(make_venv lightllm) || return 1
  pip_into "$v" torch && pip_into "$v" "git+https://github.com/ModelTC/LightLLM.git" \
    || { echo "LightLLM install failed"; return 1; }
  versions "$v" lightllm
  start_server lightllm "$v/bin/python" -m lightllm.server.api_server --model_dir "$MODEL" \
    --host 127.0.0.1 --port 8104 --max_total_token_num 60000
  wait_http http://127.0.0.1:8104/health 900 || { echo "server did not become ready"; return 1; }
  bench lightllm localforge.lightllm http://127.0.0.1:8104 "$SERVED" attached_endpoint \
    "{\"lightllm\": {\"model_id\": \"$SERVED\"}}"; local s=$?
  stop_server; drop_venv lightllm; return $s
}
test_tensorrt_llm() {
  local v; v=$(make_venv tensorrt_llm) || return 1
  pip_into "$v" --extra-index-url https://pypi.nvidia.com tensorrt_llm \
    || { echo "pip install tensorrt_llm failed (often needs system OpenMPI: libopenmpi-dev)"; return 1; }
  versions "$v" tensorrt_llm
  start_server tensorrt_llm "$v/bin/trtllm-serve" "$MODEL" --host 127.0.0.1 --port 8105 \
    --kv_cache_free_gpu_memory_fraction "$GPU_MEM"
  wait_http http://127.0.0.1:8105/v1/models 1200 || { echo "server did not become ready"; return 1; }
  bench tensorrt_llm localforge.tensorrt_llm http://127.0.0.1:8105 "*"; local s=$?
  stop_server; drop_venv tensorrt_llm; return $s
}
test_llama_cpp() {
  command -v nvcc >/dev/null && command -v cmake >/dev/null && command -v git >/dev/null \
    || { echo "needs nvcc, cmake and git to build llama.cpp with CUDA"; return 2; }
  local src="$W/engines/llama.cpp" gguf="$W/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
  rm -rf "$src"; git clone --quiet --depth 1 https://github.com/ggml-org/llama.cpp "$src" || return 1
  cmake -S "$src" -B "$src/build" -DGGML_CUDA=ON -DLLAMA_CURL=OFF > "$L/llama_cpp.build.log" 2>&1 \
    && cmake --build "$src/build" --target llama-server -j "$(nproc)" >> "$L/llama_cpp.build.log" 2>&1 \
    || { echo "llama.cpp CUDA build failed"; return 1; }
  git -C "$src" rev-parse --short HEAD > "$R/engines/llama_cpp.commit.txt"
  uvx --quiet --from huggingface_hub hf download Qwen/Qwen2.5-1.5B-Instruct-GGUF \
    qwen2.5-1.5b-instruct-q4_k_m.gguf --local-dir "$W/models" > /dev/null 2>&1 || return 1
  local server="$src/build/bin/llama-server"
  start_server llama_cpp "$server" -m "$gguf" --alias "$SERVED" -ngl 99 -c 4096 \
    --host 127.0.0.1 --port 8106 --metrics
  wait_http http://127.0.0.1:8106/health 300 || { echo "server did not become ready"; return 1; }
  bench llama_cpp localforge.llama_cpp http://127.0.0.1:8106 "$SERVED"; local s=$?
  stop_server
  # Managed mode: LocalForge launches and stops llama-server itself.
  bench llama_cpp_managed localforge.llama_cpp http://127.0.0.1:8107 "*" managed_process \
    "{\"llama_cpp\": {\"executable_path\": \"$server\", \"model_path\": \"$gguf\", \"context_size\": 4096, \"gpu_layers\": 99, \"startup_timeout_seconds\": 300}}" \
    || s=1
  return $s
}
test_ollama() {
  local dir="$W/engines/ollama"; rm -rf "$dir"; mkdir -p "$dir"
  if curl -fsSL -o "$dir/ollama.tar.zst" https://ollama.com/download/ollama-linux-amd64.tar.zst 2>/dev/null \
      && command -v zstd >/dev/null; then
    zstd -dc "$dir/ollama.tar.zst" | tar -x -C "$dir" || return 1
  else
    curl -fsSL https://ollama.com/download/ollama-linux-amd64.tgz | tar -xz -C "$dir" \
      || { echo "Ollama download failed"; return 1; }
  fi
  "$dir/bin/ollama" --version > "$R/engines/ollama.version.txt" 2>&1
  OLLAMA_HOST=127.0.0.1:11500 OLLAMA_MODELS="$W/ollama-models" \
    start_server ollama "$dir/bin/ollama" serve
  wait_http http://127.0.0.1:11500/api/version 120 || { echo "ollama serve did not start"; return 1; }
  OLLAMA_HOST=127.0.0.1:11500 "$dir/bin/ollama" pull qwen2.5:1.5b > "$L/ollama.pull.log" 2>&1 || return 1
  bench ollama localforge.ollama http://127.0.0.1:11500 qwen2.5:1.5b; local s=$?
  # The generic OpenAI-compatible adapter against the same server.
  bench openai_compatible localforge.openai_compatible http://127.0.0.1:11500/v1 qwen2.5:1.5b \
    attached_endpoint '{"openai_compatible": {"include_usage": true}}' || s=1
  stop_server; return $s
}
test_onnxruntime_genai() {
  local v out="$W/models/qwen2.5-1.5b-onnx-cuda-int4"
  v=$(make_venv onnxruntime_genai) || return 1
  pip_into "$v" onnxruntime-genai-cuda onnx torch transformers \
    || { echo "pip install onnxruntime-genai-cuda failed"; return 1; }
  versions "$v" onnxruntime_genai
  [ -f "$out/genai_config.json" ] || "$v/bin/python" -m onnxruntime_genai.models.builder \
    -i "$MODEL" -o "$out" -p int4 -e cuda > "$L/onnxruntime_genai.build.log" 2>&1 \
    || { echo "ONNX model build failed"; return 1; }
  bench onnxruntime_genai localforge.onnxruntime_genai "" "*" library_pipeline \
    "{\"onnxruntime_genai\": {\"model_path\": \"$out\", \"python_executable\": \"$v/bin/python\"}}"
  local s=$?; drop_venv onnxruntime_genai; return $s
}
test_mlc_llm() {
  local v; v=$(make_venv mlc_llm) || return 1
  pip_into "$v" --pre -f https://mlc.ai/wheels mlc-llm-nightly-cu123 mlc-ai-nightly-cu123 \
    || { echo "MLC wheels for this CUDA/Python were not available"; return 1; }
  versions "$v" mlc_llm
  start_server mlc_llm "$v/bin/mlc_llm" serve HF://mlc-ai/Qwen2.5-1.5B-Instruct-q4f16_1-MLC \
    --host 127.0.0.1 --port 8108
  wait_http http://127.0.0.1:8108/v1/models 1200 || { echo "server did not become ready"; return 1; }
  bench mlc_llm localforge.mlc_llm http://127.0.0.1:8108 "*"; local s=$?
  stop_server; drop_venv mlc_llm; return $s
}

for engine in $ENGINES; do
  if declare -F "test_$engine" > /dev/null; then run_engine "$engine" "test_$engine"
  else record "$engine" skip 0 "unknown engine name"; fi
done
record tgi skip 0 "needs Docker (not part of this kit)"
record localai skip 0 "needs Docker (not part of this kit)"
record nvidia_dynamo skip 0 "needs etcd and NATS services (not part of this kit)"
record mlx_lm skip 0 "Apple Silicon only"

# ---------------------------------------------------------------- package results
cp "$L/localforge.log" "$R/logs/" 2>/dev/null
nvidia-smi > "$R/nvidia-smi-end.txt" 2>&1
( cd "$W" && python3 -m zipfile -c "$HOME/localforge_gpu_results.zip" results )
log "Done. Summary:"; column -t -s $'\t' "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
log "Download $HOME/localforge_gpu_results.zip and put it in D:\\LocalForgeData\\gpu-results\\"
