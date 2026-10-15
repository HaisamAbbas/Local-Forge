# Model catalog, suggestions, and pulling with Ollama

LocalForge suggests which open-weight model to try on this machine and, only when you click
**Pull with Ollama** and confirm, asks your local Ollama to download it.

## The catalog file

The curated list lives in `backend/src/localforge/data/model_catalog.json` and ships inside the
Python package. It is validated by `localforge.model_catalog.ModelCatalog` when first loaded, and
`backend/tests/test_model_catalog.py` checks the packaged file.

Top-level fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Always `1` for this layout. |
| `catalog_version` | `YYYY.M.N`; bump it on every change. |
| `catalog_date` | The date the identifiers and sizes were last checked. |
| `about` | One sentence shown nowhere else; keep it accurate. |
| `models` | The entries below. |

Each model entry:

| Field | Meaning |
| --- | --- |
| `id` | Lowercase slug, unique. |
| `name`, `family`, `publisher` | Display text. |
| `params_b` | Total parameters in billions. |
| `active_params_b` | Mixture-of-experts only: parameters read per token. Speeds use this. |
| `q4_size_gb` | Typical 4-bit (Q4_K_M) file size in GB (10^9 bytes). Used for memory fit and speed. |
| `ollama_size_gb` | Only when Ollama's tag is not 4-bit (for example Q8_0): its real download size. |
| `context_tokens` | Native context length. |
| `license` | License name as published by the model's authors. |
| `strengths` | Any of `chat`, `coding`, `reasoning`, `multilingual`, `small_fast`; the first is the model's main purpose. |
| `notes` | Optional caveat shown on the card. |
| `sources.ollama` | Explicit Ollama tag (`name:tag`). |
| `sources.ollama_aliases` | Other tags that point at the same model (for example `llama3.2:latest`), for downloaded detection. |
| `sources.gguf` | Hugging Face `repo` and Q4_K_M `file` for llama.cpp and KoboldCpp. |
| `sources.openvino`, `sources.onnx` | Hugging Face repos with OpenVINO IR or ONNX Runtime GenAI builds. |

Rules: include an identifier only after checking that it exists; use `null` (or omit it) when
unknown. Never add quality or benchmark scores: the catalog describes models, LocalForge measures
them. When editing, verify Ollama tags and sizes against the registry manifest
(`https://registry.ollama.ai/v2/library/<name>/manifests/<tag>`, metadata only) and Hugging Face
files against `https://huggingface.co/api/models/<repo>`, then bump `catalog_version` and
`catalog_date`. Catalog 2026.10.1 (39 models) was checked this way on 2026-10-14.

## How suggestions are ranked

`GET /api/v1/models/suggestions?use_case=chat` (also `coding`, `document_qa`, `small_fast`,
`reasoning`) ranks catalog models for this machine. It is loopback-only and read-only: it reads the
saved machine snapshot, saved engine inventories and completed runs, and calls `GET /api/tags` on
local Ollama profiles (or `127.0.0.1:11434` when none is saved).

- **Memory fit.** A model needs about its Q4 size + 10% + 0.5 GB (KV cache and runtime at a 4k
  context). The budget is 85% of discrete GPU memory, or RAM minus 6 GB. Within 70% of the budget
  it *fits*, up to 100% it is *tight*, otherwise *too large* (always ranked last).
- **Speed.** Generating a token reads roughly every active weight once, so tokens/s ≈ memory read
  speed ÷ model size. With completed runs on this machine, the read speed is the median of
  (model size × measured generation rate) across runs: *estimated from your runs*. Otherwise, a
  recognized discrete GPU or Apple chip gives a *rough* estimate from 60% of its published memory
  bandwidth. CPU-only machines without runs show speed as unknown.
- **Score** (0-100) = fit (30 / 10 / 0) + speed against the use case's target
  (chat and coding 10 tok/s, documents 8, small and fast 20, reasoning 15; up to 30) + model size
  (log scale, up to 25) + use-case match (up to 10) + already downloaded (5).

Already-downloaded detection matches Ollama tags (including aliases) from live `/api/tags` lists or
saved inventories, and GGUF file names from llama.cpp and KoboldCpp inventories and profile model
paths. Remote endpoints never count.

`localforge recommend` prints the top three for chat and coding under "Models to try".

## Pulling with Ollama

`POST /api/v1/models/pull` with `{"engine_profile_id", "ollama_tag", "confirm": true}` starts a
download in the background and returns `202` with a pull record. It requires the local session
token (like every mutation), `confirm: true`, an **enabled** Ollama profile whose endpoint is on
this machine (remote endpoints are refused), and a tag from the catalog; a tag outside the catalog
needs `"custom_tag": true` as well. LocalForge then calls Ollama's `POST /api/pull` with streaming
progress. Ollama, not LocalForge, downloads the files from its registry (`registry.ollama.ai`, or
the host named in a custom tag such as `hf.co/...`).

- `GET /api/v1/models/pulls/{id}`: status (`running`, `succeeded`, `failed`, `cancelled`), Ollama's
  current phase, bytes done and total, percent, error, and the follow-up probe result.
- `GET /api/v1/models/pulls`: pulls since the API started (kept in memory only).
- `POST /api/v1/models/pulls/{id}/cancel`: closes the stream, which stops Ollama's download.
  Layers already downloaded stay in Ollama's cache and a later pull resumes from them.

After a successful pull LocalForge probes that engine profile so the model appears in its
inventory. At most two pulls run at once, and the same tag cannot be pulled twice concurrently.
Stopping the API cancels running pulls.
