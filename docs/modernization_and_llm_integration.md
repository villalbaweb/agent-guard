# AgentGuard: Modernization & LLM Integration (April 2026)

## Overview
This document is the source of truth for the project's architectural decisions around environment management, LLM integration, embeddings, and infrastructure. It reflects the current production-ready PoC state after completing Phases 1–3 of the implementation plan.

---

## 1. Environment & Dependency Management

We use **`uv`** as the primary package manager for isolated, reproducible, and high-performance environments.

### 1.1 Local Setup
- **Tooling:** [uv](https://github.com/astral-sh/uv)
- **Environment:** Project-local virtual environment at `.venv/`
- **Configuration:** `pyproject.toml` with `tool.uv.package = false` (non-package mode)

### 1.2 Common Commands
```bash
uv sync                                         # install/update all dependencies
PYTHONPATH=. uv run python agentguard/poc.py    # run the full governance PoC
PYTHONPATH=. uv run pytest                      # run the test suite
```

---

## 2. Configuration & Security

### 2.1 Environment Variables
- **`.env`** — private API keys. **Never commit this file.**
- **`.env.example`** — template documenting all required settings.
- **`.gitignore`** — ignores `.env`, tracks `.env.example`.

### 2.2 Core Settings

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `GOOGLE_API_KEY` | Gemini LLM + embeddings | — |
| `GEMINI_MODEL` | Gemini chat model ID | `gemini-2.0-flash` |
| `GEMINI_EMBEDDING_MODEL` | Gemini embedding model | `models/gemini-embedding-001` |
| `OPENAI_API_KEY` | OpenAI or any compatible provider | — |
| `OPENAI_MODEL` | Chat model for OpenAI path | `gpt-4o-mini` |
| `OPENAI_BASE_URL` | Override endpoint (OpenRouter, LiteLLM, etc.) | — |
| `OPENAI_EMBEDDING_MODEL` | Embedding model for OpenAI path | `text-embedding-3-small` |
| `ANTHROPIC_API_KEY` | Anthropic Claude models | — |
| `ANTHROPIC_MODEL` | Claude model ID | `claude-haiku-4-5-20251001` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379` |
| `POLICY_FILE` | Path to YAML governance rules | `policy.yaml` |
| `AGENTGUARD_JWT_SECRET` | HS256 signing secret for JWT auth | — (required when auth enabled) |
| `AGENTGUARD_JWT_ISSUER` | Expected `iss` claim in JWTs | `agentguard` |
| `AGENTGUARD_API_KEY` | Static API key fallback for non-JWT clients | — |
| `AGENTGUARD_NO_AUTH` | Set to `1` to bypass all auth (dev/testing only) | — |

---

## 3. LLM Factory (`agentguard/llm.py`)

`get_llm()` returns an initialized `BaseChatModel` or `None` (mock mode). Provider resolution order:

1. **Google AI Studio** (`GOOGLE_API_KEY`) — Gemini, no GCP service account required
2. **Anthropic** (`ANTHROPIC_API_KEY`) — Claude models
3. **OpenAI-compatible** (`OPENAI_API_KEY`) — works with OpenAI, OpenRouter, LiteLLM, vLLM, Ollama
   - Set `OPENAI_BASE_URL` to redirect to any compatible endpoint
   - Set `OPENAI_MODEL` to override the model name
4. **Google Vertex AI** (`GOOGLE_APPLICATION_CREDENTIALS`) — enterprise GCP path

---

## 4. Embeddings Factory (`agentguard/llm.py`)

`get_embeddings()` returns a callable `(texts: list[str]) -> list[list[float]]` used for **semantic loop detection** in `MemoryManager`. Returns `None` if no provider is configured; callers degrade to exact string-match loop detection.

**No local container required.** Embeddings are resolved via cloud API:

1. **OpenAI** (`OPENAI_API_KEY`) — `text-embedding-3-small` (1536-dim), fast and cheap
2. **Google AI Studio** (`GOOGLE_API_KEY`) — `models/gemini-embedding-001` (3072-dim)

### Decision Log: Why not local sentence-transformers?
A local Docker service using Hugging Face sentence-transformers was evaluated and built. It was rejected because:
- Final image size was ~4 GB (PyTorch + CUDA libraries bundled even for CPU builds)
- Cloud embedding APIs (Google/OpenAI) have lower operational overhead and acceptable latency
- No meaningful latency advantage for the PoC's non-realtime use case

---

## 5. Infrastructure (`docker-compose.poc.yml`)

The PoC stack runs with a single compose command:

```bash
docker compose -f docker-compose.poc.yml up -d
```

| Service | Image | Port | Purpose |
| :--- | :--- | :--- | :--- |
| `redis` | `redis:7-alpine` | 6379 | Thought history, blueprint cache, loop-detection state, run state + trace storage |
| `backend` | `Dockerfile.backend` | 8000 | FastAPI REST API (`agentguard` governance layer + HTTP facade) |

The full application stack (React frontend, PostgreSQL + pgvector, Jaeger tracing) is defined in `docker-compose.local.yml` for future phases.

---

## 6. Policy Configuration (`policy.yaml`)

Governance rules are defined in YAML and hot-loaded at runtime by `PolicyEngine`. No code change is required to add, modify, or remove rules. Three sections drive all three wedge use cases:

```yaml
# Wedge 1: Cost control
budget:
  max_cost_usd: 10.00
  per_step_limit_usd: 1.00

# Wedge 2: PII & forbidden topics
content_rules:
  - id: block_pii_ssn
    operator: regex
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    action: block
  - id: hitl_financial_transactions
    operator: contains
    values: [transfer_funds, execute_payment]
    action: require_hitl

# Wedge 3: Audit trail
audit:
  enabled: true
  trace_all_decisions: true
  retention_days: 730

# Loop detection
loop_detection:
  strategy: semantic          # cosine similarity via get_embeddings()
  similarity_threshold: 0.92
  lookback_window: 5
```

State-level `budget_config` overrides `policy.yaml` values — enables per-run budget limits without touching the global policy.

---

## 7. Technical Specifications (PoC — Implemented)

| Pillar | Specification |
| :--- | :--- |
| **Runtime** | Python 3.13+ |
| **Orchestration** | LangGraph (StateGraph) |
| **Primary LLM** | Gemini 2.0 Flash (via `GOOGLE_API_KEY`) |
| **Embeddings** | Gemini `gemini-embedding-001` (fallback from OpenAI `text-embedding-3-small`) |
| **Persistence** | Redis 7 Alpine (real backend, in-memory fallback for dev) |
| **Policy format** | YAML (`policy.yaml`) — hot-loaded, no redeploy needed |
| **Dependency mgmt** | uv + pyproject.toml |
| **Configuration** | python-dotenv (`.env`) |

---

## 8. Remaining Gaps (Next Milestone)

- **Concurrency:** Replace serial `for` loop in `RecursiveExecutor` with LangGraph `Send` for true parallel subtask dispatch (requires U-01 Redlock first)
- **Registry persistence:** Migrate `Registry` from in-memory dict to Redis + vector similarity search (U-09)
- **Production JWT:** Upgrade from HS256 to RS256 with a JWKS endpoint for key rotation
- **Durable HITL checkpointer:** `langgraph-checkpoint-redis` wired but not the default install; activate `hitl-redis` optional dependency group in production
- **Load testing:** 10 concurrent runs, p95 latency measurement
- **Multi-tenant policy scoping:** Per-request `policy_id` resolution (U-11)
- **MCP integration:** PolicyGuard proxy middleware for MCP tool calls (U-03)

See `unknown_items.md` for the full open-items register.
