import type { EngineRecommendation, EngineRecommendationsResult } from "./api";

/** Test fixture for the engine recommendations endpoint (used by component tests only). */
export function recommendationFixture(
  overrides: Partial<EngineRecommendation> = {},
): EngineRecommendation {
  return {
    rank: 1,
    adapter_id: "localforge.ollama",
    display_name: "Ollama",
    fit: "best",
    score: 90,
    reasons: ["Runs GGUF models on CPU with AVX2.", "Already running on this computer."],
    cautions: [],
    status: "running",
    status_detail: null,
    endpoint_url: "http://127.0.0.1:11434",
    existing_profile_ids: [],
    install: {
      summary: "Download the Ollama installer for Windows.",
      url: "https://ollama.com/download",
      command: null,
    },
    measured: null,
    ...overrides,
  };
}

export function recommendationsFixture(
  recommendations: EngineRecommendation[] = defaultRecommendations(),
): EngineRecommendationsResult {
  return {
    generated_at: "2026-01-01T00:00:00Z",
    method:
      "Ranked by hardware fit and what is installed on this machine; this is not a speed measurement.",
    machine: {
      summary: "Windows 11 · Test CPU · 8 logical cores · 16 GB memory · no dedicated GPU",
      os: "Windows",
      arch: "AMD64",
      cpu_model: "Test CPU",
      cpu_logical_cores: 8,
      cpu_physical_cores: 4,
      memory_total_bytes: 16 * 1024 ** 3,
      accelerators: [],
      cpu_features: { avx2: true, avx512: false },
    },
    model_size_guidance: {
      basis: "system_memory",
      memory_budget_bytes: 8 * 1024 ** 3,
      comfortable_params_billion_q4: 7,
      max_params_billion_q4: 13,
      text: "Models up to about 7B parameters at Q4 should fit comfortably in memory.",
    },
    recommendations,
  };
}

export function defaultRecommendations(): EngineRecommendation[] {
  return [
    recommendationFixture(),
    recommendationFixture({
      rank: 2,
      adapter_id: "localforge.llama_cpp",
      display_name: "llama.cpp",
      fit: "good",
      score: 80,
      reasons: ["Fast CPU inference.", "Fine control over threads.", "Supports GGUF directly."],
      cautions: ["You manage model files yourself."],
      status: "installed",
      status_detail: "llama-server was found on PATH but is not running.",
      endpoint_url: null,
      install: {
        summary: "Start llama-server with a GGUF model file.",
        url: "https://github.com/ggml-org/llama.cpp",
        command: null,
      },
      measured: {
        completed_runs: 3,
        best_generation_tokens_per_second: 24.71,
        model_ref: "qwen2.5:3b",
        run_id: "run-1",
      },
    }),
    recommendationFixture({
      rank: 3,
      adapter_id: "localforge.localai",
      display_name: "LocalAI",
      fit: "possible",
      score: 55,
      reasons: ["OpenAI-compatible server."],
      cautions: ["Heavier to install than Ollama."],
      status: "not_installed",
      endpoint_url: null,
      install: {
        summary: "Run LocalAI with Docker.",
        url: "https://localai.io/basics/getting_started/",
        command: "docker run -p 8080:8080 localai/localai",
      },
    }),
    recommendationFixture({
      rank: 4,
      adapter_id: "localforge.koboldcpp",
      display_name: "KoboldCpp",
      fit: "possible",
      score: 50,
      reasons: ["Single executable."],
      status: "running",
      endpoint_url: "http://127.0.0.1:5001",
      existing_profile_ids: ["kobold-profile"],
    }),
    recommendationFixture({
      rank: 5,
      adapter_id: "localforge.vllm",
      display_name: "vLLM",
      fit: "unsuitable",
      score: 5,
      reasons: [],
      cautions: ["Needs Linux with an NVIDIA GPU; this machine has neither."],
      status: "not_installed",
      endpoint_url: null,
      install: {
        summary: "Install vLLM with pip on Linux.",
        url: "https://docs.vllm.ai",
        command: "pip install vllm",
      },
    }),
  ];
}
