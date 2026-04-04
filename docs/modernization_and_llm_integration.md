# AgentGuard: Modernization & LLM Integration (April 2026)

## Overview
This document serves as the source of truth for the project's architectural modernization, environment management, and LLM integration. It captures the transition from mock-based development to a production-ready foundation using Google AI Studio (Gemini) and modern Python tooling.

## 1. Environment & Dependency Management
We have adopted **`uv`** as the primary package manager to ensure isolated, reproducible, and high-performance environments.

### 1.1 Local Setup
- **Tooling:** [uv](https://github.com/astral-sh/uv)
- **Environment:** Project-local virtual environment located in `.venv/`.
- **Configuration:** Managed via `pyproject.toml` with `tool.uv.package = false` (Non-package mode). This allows for rapid script execution (e.g., `poc.py`) without needing to "install" the project as a library.

### 1.2 Common Commands
- **Initialize/Sync Dependencies:** `uv sync`
- **Run Scripts:** `uv run python <path_to_script>`
- **Run with PYTHONPATH:** `$env:PYTHONPATH="."; uv run python <path_to_script>`

## 2. Configuration & Security
The system uses a strict "Zero Trust" configuration model for API keys and secrets.

### 2.1 Environment Variables
- **`.env`**: Stores private API keys. **NEVER commit this file.**
- **`.env.example`**: A template file provided in the root to document all required settings.
- **`.gitignore`**: Explicitly configured to ignore `.env` while tracking `.env.example`.

### 2.2 Core Settings
- `GOOGLE_API_KEY`: Your Gemini API key from [Google AI Studio](https://aistudio.google.com/).
- `GEMINI_MODEL`: (Optional) The specific model ID to use. Defaults to `gemini-3.1-pro-preview`.

## 3. LLM Factory Architecture (`agentguard/llm.py`)
The `get_llm()` factory provides a model-agnostic interface for the entire framework.

### 3.1 Google AI Studio (Primary)
- **SDK:** `langchain-google-genai`
- **Model:** `gemini-3.1-pro-preview` (Most advanced 2026 standard).
- **Rationale:** Optimized for API Key usage without the overhead of GCP Service Accounts or Vertex AI ADC requirements.

### 3.2 Logic Flow
1. Loads environment variables via `python-dotenv` (at entry points).
2. Checks for `GOOGLE_API_KEY` (highest priority for Gemini).
3. Falls back to `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` if configured.
4. Returns `None` if no keys are found (triggering mock logic in executors).

## 4. Testing & Validation Suite
A temporary verification suite is maintained in `tests_tmp/` to prove system integrity.

- **`test_llm_auth.py`**: Unit test using `unittest.mock` to verify the `get_llm()` factory logic without network calls.
- **`list_models.py`**: Diagnostic tool to query the Google API and list all model IDs available to your specific API key.
- **`test_llm_inference.py`**: Integration test that performs a live round-trip request to verify end-to-end connectivity.

## 5. Technical Specifications (MVP)

| Pillar | Specification |
| :--- | :--- |
| **Runtime** | Python 3.13+ |
| **Orchestration** | LangGraph |
| **Model** | Gemini 3.1 Pro Preview |
| **Dependency Management** | uv + pyproject.toml |
| **Configuration** | Dotenv (.env) |

## 6. Future Roadmap Gaps
- **Persistence:** Migrate `MemoryManager` and `Registry` from in-memory mocks to Redis.
- **Concurrency:** Update `RecursiveExecutor` to use LangGraph's parallel `Send` pattern instead of serial `for` loops.
- **Identity:** Implement the JWT-based `auth_context` in `AgentGuardState` for root-user propagation.
