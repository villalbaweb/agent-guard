# U-09 & U-12: Vector & Dynamic Agent Registry — Implementation Plan

> **Goal:** Replace the static, in-memory `Registry` with a PostgreSQL-backed dynamic registry that supports vector similarity search via pgvector, runtime agent registration via REST API, and agent health monitoring.

---

## 1. Problem Statement

The current `Registry` ([registry.py](file:///d:/Git/agent-guard/agentguard/registry.py)) has four critical limitations documented in `unknown_items.md`:

| Problem | Impact |
|:--------|:-------|
| **Static registration** — agents are hardcoded in [dependencies.py::get_registry()](file:///d:/Git/agent-guard/backend/dependencies.py#L39-L56) at server startup | Any agent change requires a code change + server redeploy |
| **Substring matching** — `search_by_intent()` does case-insensitive substring scan | Intent routing accuracy degrades with 10+ agents |
| **No persistence** — registry lives in-memory only | Server restart loses all registrations |
| **No agent health** — no liveness check on registered agents | Dead agents remain routable |

Additionally, the `ExecutionPlanner` ([orchestrator.py](file:///d:/Git/agent-guard/agentguard/orchestrator.py#L55-L57)) relies entirely on `Registry.search_by_intent()` to route subtasks to agents. Improving routing accuracy directly improves the quality of every task execution.

---

## 2. Technology Choices

### 2.1 Database: PostgreSQL + pgvector

Using `pgvector/pgvector:pg16` as specified. pgvector provides:
- Native `vector` column type for storing embeddings
- `<=>` (cosine distance), `<->` (L2 distance), `<#>` (inner product) operators
- HNSW and IVFFlat indexes for approximate nearest-neighbor search
- Full SQL semantics — no separate vector store to maintain

### 2.2 Docker Compose Services

The following services will be added to `docker-compose.poc.yml`:

```yaml
# --- PostgreSQL Database ---
postgres:
  image: pgvector/pgvector:pg16
  container_name: agentguard_postgres
  restart: unless-stopped
  environment:
    POSTGRES_DB: ${POSTGRES_DB:-agent_db}
    POSTGRES_USER: ${POSTGRES_USER:-agent_user}
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}
  volumes:
    - postgres_data:/var/lib/postgresql/data
    - ./migrations:/docker-entrypoint-initdb.d
  networks:
    - npm_default
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-agent_user} -d ${POSTGRES_DB:-agent_db}"]
    interval: 10s
    timeout: 5s
    retries: 5

# --- DbGate (Database Management UI) ---
dbgate:
  image: dbgate/dbgate:latest
  container_name: agentguard_dbgate
  restart: unless-stopped
  expose:
    - "3000"
  environment:
    CONNECTIONS: postgres
    LABEL_postgres: Agent Database
    SERVER_postgres: postgres
    USER_postgres: ${POSTGRES_USER:-agent_user}
    PASSWORD_postgres: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}
    PORT_postgres: 5432
    DATABASE_postgres: ${POSTGRES_DB:-agent_db}
    ENGINE_postgres: postgres@dbgate-plugin-postgres
  depends_on:
    postgres:
      condition: service_healthy
  networks:
    - npm_default
```

### 2.3 Python Dependencies

| Package | Purpose |
|:--------|:--------|
| `psycopg[binary]>=3.2.0` | PostgreSQL driver (async-capable, pure-Python fallback) |
| `psycopg-pool>=3.2.0` | Connection pooling |
| `pgvector>=0.3.0` | pgvector Python integration (register_vector, adapters) |

### 2.4 Embedding Dimensions

The registry will store embeddings produced by the existing `get_embeddings()` factory in [llm.py](file:///d:/Git/agent-guard/agentguard/llm.py). Dimensions vary by provider:

| Provider | Model | Dimensions |
|:---------|:------|:-----------|
| OpenAI | `text-embedding-3-small` | 1536 |
| Google AI Studio | `gemini-embedding-001` | 768 |

The `vector` column will use a configurable dimension (env var `EMBEDDING_DIM`, default `1536`). A mismatch between the stored dimension and the embedding provider will be caught at registration time with a clear error.

---

## 3. Database Schema

### 3.1 SQL Migration (`migrations/001_registry.sql`)

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Agent registry table
CREATE TABLE IF NOT EXISTS agent_registry (
    id              TEXT PRIMARY KEY,                       -- stable unique agent identifier
    role            TEXT NOT NULL,                          -- human-readable capability label
    semantic_description TEXT NOT NULL,                     -- prose description for intent routing
    input_schema    JSONB NOT NULL DEFAULT '{}',            -- expected input contract
    output_schema   JSONB NOT NULL DEFAULT '{}',            -- output contract
    endpoint        TEXT NOT NULL DEFAULT '',               -- optional HTTP endpoint for remote agents
    embedding       vector(1536),                          -- pgvector embedding of semantic_description
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,          -- soft-delete / deactivation flag
    health_status   TEXT NOT NULL DEFAULT 'unknown',        -- 'healthy' | 'unhealthy' | 'unknown'
    last_heartbeat  TIMESTAMPTZ,                           -- last successful health check
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),    -- creation timestamp
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()     -- last modification timestamp
);

-- HNSW index for fast cosine similarity search
-- Only created after initial data load for best build performance
CREATE INDEX IF NOT EXISTS idx_agent_embedding_hnsw
    ON agent_registry
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Index for active-agent filtering (most queries filter on is_active=TRUE)
CREATE INDEX IF NOT EXISTS idx_agent_active
    ON agent_registry (is_active)
    WHERE is_active = TRUE;

-- Trigger to auto-update `updated_at`
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agent_updated_at
    BEFORE UPDATE ON agent_registry
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();
```

### 3.2 Design Notes

- **`embedding` is nullable** — allows registration without an embedding provider configured (falls back to substring matching).
- **`is_active`** — soft-delete pattern. Deactivated agents are excluded from search results but retained for audit trails.
- **`health_status` + `last_heartbeat`** — populated by an optional background health checker.
- **HNSW index** — chosen over IVFFlat because it doesn't require a training step and performs well with fewer than 10,000 vectors. `m=16, ef_construction=64` are sensible defaults for registries under 1,000 agents.

---

## 4. Proposed Changes

### Phase 1 — Database Layer

#### [NEW] `agentguard/db.py` — PostgreSQL Connection Manager

Singleton connection pool, analogous to how `MemoryManager` handles Redis. Provides:

```python
class DatabaseManager:
    """PostgreSQL connection pool with pgvector support."""

    def __init__(self):
        """
        Connect to PostgreSQL using DATABASE_URL env var.
        Falls back gracefully if PostgreSQL is unavailable (logs warning,
        sets self._pool = None). Callers check `self.available` before use.
        """

    @property
    def available(self) -> bool:
        """True if the connection pool is active."""

    def execute(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a query and return rows as dicts."""

    def execute_one(self, query: str, params: tuple = ()) -> dict | None:
        """Execute a query and return the first row, or None."""

    def execute_write(self, query: str, params: tuple = ()) -> int:
        """Execute an INSERT/UPDATE/DELETE and return affected row count."""

    def run_migration(self, migration_file: str):
        """Execute a SQL migration file idempotently."""

    def close(self):
        """Close the connection pool."""
```

**Environment variables:**

| Variable | Default | Description |
|:---------|:--------|:------------|
| `DATABASE_URL` | `postgresql://agent_user:changeme@localhost:5432/agent_db` | PostgreSQL connection string |
| `DB_POOL_MIN` | `2` | Minimum pool connections |
| `DB_POOL_MAX` | `10` | Maximum pool connections |
| `EMBEDDING_DIM` | `1536` | Expected embedding vector dimension |

**Graceful degradation:** If `DATABASE_URL` is not set or PostgreSQL is unreachable, `DatabaseManager.available` returns `False`. The `Registry` falls back to its current in-memory mode — no PostgreSQL required for local development.

---

#### [NEW] `migrations/001_registry.sql`

The SQL schema from Section 3.1 above. Executed by `DatabaseManager.run_migration()` at startup.

---

### Phase 2 — Registry Rewrite

#### [MODIFY] `agentguard/registry.py` — PostgreSQL-Backed Registry

The current 32-line file grows to ~250 lines. The class maintains the same public interface but gains persistence, vector search, and health tracking.

##### New constructor signature:

```python
class Registry:
    def __init__(self, db: DatabaseManager | None = None):
        """
        If db is provided and available, use PostgreSQL storage.
        Otherwise, fall back to in-memory dict (current behavior).
        """
```

**This preserves backward compatibility** — all existing code that does `Registry()` still works.

##### Updated `register()`:

```python
def register(
    self,
    item_id: str,
    role: str,
    semantic_description: str,
    input_schema: dict,
    output_schema: dict,
    endpoint: str = "",
) -> dict:
    """
    Register or update an agent.

    When PostgreSQL is available:
      1. Compute embedding of semantic_description via get_embeddings()
      2. UPSERT into agent_registry table
      3. Return the stored record

    When PostgreSQL is unavailable:
      Fall back to in-memory dict (current behavior).

    Returns:
      The agent metadata dict (including `registered_at`, `updated_at`).
    """
```

Uses PostgreSQL `INSERT ... ON CONFLICT (id) DO UPDATE` (upsert) so re-registering an agent updates its metadata and re-computes its embedding.

##### Updated `search_by_intent()`:

```python
def search_by_intent(self, intent: str, limit: int = 5) -> list[dict]:
    """
    Find agents whose semantic_description is most similar to the intent.

    When PostgreSQL + embeddings are available:
      1. Embed the intent string
      2. Query: SELECT * FROM agent_registry
                WHERE is_active = TRUE
                ORDER BY embedding <=> $intent_embedding
                LIMIT $limit
      3. Return ranked results

    When only PostgreSQL is available (no embeddings):
      Fall back to ILIKE substring matching on role + semantic_description.

    When neither is available:
      Fall back to in-memory substring matching (current behavior).
    """
```

##### New methods:

```python
def deactivate(self, item_id: str) -> bool:
    """Set is_active=FALSE. Agent is excluded from searches but retained for audit."""

def activate(self, item_id: str) -> bool:
    """Re-activate a previously deactivated agent."""

def delete(self, item_id: str) -> bool:
    """Hard-delete an agent from the registry. Use deactivate() for soft-delete."""

def get(self, item_id: str) -> dict | None:
    """Retrieve an agent by ID. Returns None if not found or inactive."""

def list_all(self, include_inactive: bool = False) -> list[dict]:
    """List all registered agents. Optionally include inactive ones."""

def update_health(self, item_id: str, status: str) -> None:
    """Update health_status and last_heartbeat for an agent."""

def recompute_embeddings(self) -> int:
    """
    Re-embed all agents' semantic_description fields.
    Useful after switching embedding providers or models.
    Returns the number of agents updated.
    """
```

---

### Phase 3 — REST API Endpoints

#### [NEW] `backend/routes/agents.py` — Agent Registry CRUD

| Method | Path | Auth | Description |
|:-------|:-----|:-----|:------------|
| `POST` | `/agents/register` | Bearer / API key | Register or update an agent |
| `GET` | `/agents` | Bearer / API key | List all active agents |
| `GET` | `/agents/{id}` | Bearer / API key | Get a single agent by ID |
| `DELETE` | `/agents/{id}` | Bearer / API key | Deactivate an agent (soft-delete) |
| `POST` | `/agents/{id}/activate` | Bearer / API key | Re-activate a deactivated agent |
| `POST` | `/agents/search` | Bearer / API key | Search agents by intent (vector similarity) |

##### Request/Response Models:

```python
# --- POST /agents/register ---
class AgentRegisterRequest(BaseModel):
    id: str = Field(..., description="Stable unique agent identifier.", min_length=1, max_length=128)
    role: str = Field(..., description="Human-readable capability label.")
    semantic_description: str = Field(..., description="Prose description for intent routing.")
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    endpoint: str = Field("", description="Optional HTTP endpoint for remote agents.")

class AgentRegisterResponse(BaseModel):
    id: str
    role: str
    semantic_description: str
    input_schema: dict
    output_schema: dict
    endpoint: str
    is_active: bool
    has_embedding: bool
    registered_at: datetime
    updated_at: datetime

# --- POST /agents/search ---
class AgentSearchRequest(BaseModel):
    intent: str = Field(..., description="Natural-language intent to match against agent descriptions.")
    limit: int = Field(5, ge=1, le=50, description="Maximum number of results.")

class AgentSearchResponse(BaseModel):
    results: list[AgentRegisterResponse]
    search_method: str  # "vector_similarity" | "substring_match" | "in_memory"

# --- GET /agents ---
class AgentListResponse(BaseModel):
    agents: list[AgentRegisterResponse]
    total: int
```

#### [MODIFY] `backend/main.py` — Wire New Router

Add `from .routes import agents` and `app.include_router(agents.router)`.

#### [MODIFY] `backend/schemas.py` — Add Agent Models

Add the Pydantic models listed above.

#### [MODIFY] `backend/dependencies.py` — Database & Registry Injection

```python
@lru_cache(maxsize=1)
def get_database() -> DatabaseManager:
    return DatabaseManager()

@lru_cache(maxsize=1)
def get_registry(db: DatabaseManager = Depends(get_database)) -> Registry:
    registry = Registry(db=db)

    # Seed default agents if the registry is empty
    if not registry.list_all():
        registry.register(
            item_id="search_agent_01",
            role="Search Specialist",
            semantic_description="Searches the web for any topic or query.",
            input_schema={"query": "string"},
            output_schema={"results": "list"},
        )
        registry.register(
            item_id="analysis_agent_01",
            role="Data Analyst",
            semantic_description="Analyzes datasets and provides structured insights.",
            input_schema={"data": "string"},
            output_schema={"analysis": "string"},
        )
    return registry
```

> [!IMPORTANT]
> The `get_registry()` singleton must remain `@lru_cache(maxsize=1)` so the executor, planner, and REST routes all share the same instance with hot data.

---

### Phase 4 — Orchestrator Adaptation

#### [MODIFY] `agentguard/orchestrator.py` — Use Vector Search

The `ExecutionPlanner.route_intent()` method currently calls `self.registry.search_by_intent(intent)`. **No change required** — the `Registry` class internally upgrades from substring to vector search when PostgreSQL is available. The planner is transparently improved.

However, one optimization should be made in the `plan()` method:

**Current pattern** (one LLM call per subtask to extract an intent verb, then substring search):
```python
for subtask in subtasks:
    intent = self._extract_intent(subtask)  # LLM call
    agents = self.route_intent(intent)       # substring match
```

**Improved pattern** (embed the full subtask directly — vector search handles semantic matching):
```python
for subtask in subtasks:
    agents = self.registry.search_by_intent(subtask)  # vector similarity on full subtask
```

This **eliminates the per-subtask LLM intent-extraction call** when vector search is active. The embedding of the subtask text is semantically compared against agent descriptions directly — no need for a separate "extract one verb" step. This saves one LLM round-trip per subtask.

When vector search is *not* available (no embeddings or no PostgreSQL), the existing LLM-based intent extraction + substring search remains as the fallback path.

---

### Phase 5 — Health Check System

#### [MODIFY] `agentguard/registry.py` — Add Health Checker

```python
async def check_agent_health(self, item_id: str) -> str:
    """
    Probe an agent's endpoint (if set) and update its health_status.
    Returns 'healthy', 'unhealthy', or 'unknown' (no endpoint).
    """
```

#### [NEW] `agentguard/health_checker.py` — Background Health Monitor

An optional background task that periodically probes all registered agents with endpoints:

```python
class AgentHealthChecker:
    """
    Periodic background task that checks the health of all registered agents
    with endpoints. Updates health_status and last_heartbeat in the registry.

    Configuration via env vars:
      AGENT_HEALTH_INTERVAL=60    (seconds between full sweeps)
      AGENT_HEALTH_TIMEOUT=5      (seconds per HTTP probe)
      AGENT_UNHEALTHY_THRESHOLD=3 (consecutive failures before marking unhealthy)
    """
```

The health checker performs HTTP GET to each agent's `endpoint` (or `endpoint + /health`). Agents without endpoints are skipped (status remains `unknown`).

#### [MODIFY] `backend/main.py` — Start Health Checker in Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AgentGuard API starting up...")
    # Start optional agent health checker
    health_checker = AgentHealthChecker(get_registry())
    health_task = asyncio.create_task(health_checker.run())
    yield
    health_task.cancel()
    logger.info("AgentGuard API shutting down.")
```

#### [MODIFY] `backend/routes/health.py` — Add PostgreSQL Probe

Update the `/health` endpoint to include PostgreSQL connectivity status alongside Redis and LLM:

```python
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unhealthy"]
    redis: Literal["ok", "unavailable"]
    postgres: Literal["ok", "unavailable"]      # NEW
    llm: Literal["ok", "unavailable"]
    timestamp: datetime
```

---

### Phase 6 — Infrastructure Updates

#### [MODIFY] `docker-compose.poc.yml`

Add the PostgreSQL and DbGate services from Section 2.2. Update the `backend` service to depend on PostgreSQL:

```yaml
backend:
  # ... existing config ...
  environment:
    REDIS_URL: redis://redis:6379
    DATABASE_URL: postgresql://${POSTGRES_USER:-agent_user}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-agent_db}
  depends_on:
    redis:
      condition: service_healthy
    postgres:
      condition: service_healthy

volumes:
  postgres_data:
```

#### [MODIFY] `pyproject.toml` — Add Dependencies

```toml
[project]
dependencies = [
    # ... existing deps ...
    # U-09/U-12 — PostgreSQL + pgvector for agent registry
    "psycopg[binary]>=3.2.0",
    "psycopg-pool>=3.2.0",
    "pgvector>=0.3.0",
]
```

#### [MODIFY] `.env.example` — Add PostgreSQL Config

```env
# --- POSTGRESQL (Agent Registry — U-09/U-12) ---
DATABASE_URL=postgresql://agent_user:changeme@localhost:5432/agent_db
POSTGRES_DB=agent_db
POSTGRES_USER=agent_user
POSTGRES_PASSWORD=changeme
# EMBEDDING_DIM=1536    # Match your embedding provider (OpenAI=1536, Gemini=768)
```

---

## 5. Detailed Task Breakdown

### Phase 1 — Database Layer
- [ ] **1.1** Create `migrations/001_registry.sql` with the schema from Section 3.1
- [ ] **1.2** Create `agentguard/db.py` — `DatabaseManager` class with connection pool, migration runner, graceful fallback
- [ ] **1.3** Add `psycopg[binary]`, `psycopg-pool`, `pgvector` to `pyproject.toml`
- [ ] **1.4** Add PostgreSQL + DbGate services to `docker-compose.poc.yml`
- [ ] **1.5** Add `DATABASE_URL`, `POSTGRES_*`, `EMBEDDING_DIM` to `.env.example`
- [ ] **1.6** Write unit tests for `DatabaseManager` (connection, migration, fallback)

### Phase 2 — Registry Rewrite
- [ ] **2.1** Refactor `agentguard/registry.py` — add `db` parameter, PostgreSQL storage, in-memory fallback
- [ ] **2.2** Implement `register()` with embedding computation + PostgreSQL upsert
- [ ] **2.3** Implement `search_by_intent()` with vector cosine similarity query
- [ ] **2.4** Implement `deactivate()`, `activate()`, `delete()`
- [ ] **2.5** Implement `update_health()`, `list_all(include_inactive)`, `get()`
- [ ] **2.6** Implement `recompute_embeddings()` utility method
- [ ] **2.7** Write unit tests for all Registry methods (with and without db)
- [ ] **2.8** Verify backward compatibility — `Registry()` with no args behaves identically to current code

### Phase 3 — REST API
- [ ] **3.1** Add Pydantic models to `backend/schemas.py` (`AgentRegisterRequest`, `AgentRegisterResponse`, `AgentSearchRequest`, `AgentSearchResponse`, `AgentListResponse`)
- [ ] **3.2** Create `backend/routes/agents.py` with all 6 endpoints
- [ ] **3.3** Update `backend/dependencies.py` — add `get_database()`, update `get_registry()` to accept `DatabaseManager`
- [ ] **3.4** Update `backend/main.py` — wire `agents.router`
- [ ] **3.5** Write API tests for all agent endpoints (`tests/test_agents_api.py`)

### Phase 4 — Orchestrator Adaptation
- [ ] **4.1** Update `ExecutionPlanner.plan()` to use full subtask text for vector search when available (bypass LLM intent extraction)
- [ ] **4.2** Update existing tests that mock `search_by_intent` to verify both vector and substring paths
- [ ] **4.3** Verify PoC end-to-end (`agentguard/poc.py`) with PostgreSQL-backed registry

### Phase 5 — Health Check System
- [ ] **5.1** Add `check_agent_health()` method to `Registry`
- [ ] **5.2** Create `agentguard/health_checker.py` — `AgentHealthChecker` background task
- [ ] **5.3** Wire health checker into `backend/main.py` lifespan
- [ ] **5.4** Update `backend/routes/health.py` — add `postgres` field to `HealthResponse`
- [ ] **5.5** Update `backend/schemas.py` — add `postgres` field to `HealthResponse`
- [ ] **5.6** Write tests for health checker (`tests/test_health_checker.py`)

### Phase 6 — Documentation Updates
- [ ] **6.1** Update [unknown_items.md](file:///d:/Git/agent-guard/docs/unknown_items.md):
  - U-09: Mark as `✅ Resolved` — "Implemented via pgvector cosine similarity. `search_by_intent()` embeds the intent text and queries `agent_registry` using `vector <=> operator` with HNSW index. Falls back to substring matching when embeddings are unavailable."
  - U-12: Mark as `✅ Resolved` — "Implemented via `POST /agents/register` REST endpoint and PostgreSQL-backed `Registry`. Agents self-register at runtime; no code change or server redeploy required. Health monitoring updates `health_status` via background task."
- [ ] **6.2** Update [implementation_plan.md](file:///d:/Git/agent-guard/docs/implementation_plan.md):
  - Update architecture diagram to show PostgreSQL alongside Redis
  - Add `POST /agents/register`, `GET /agents`, `GET /agents/{id}`, `DELETE /agents/{id}`, `POST /agents/{id}/activate`, `POST /agents/search` to the REST API table
  - Update the "Capability Registry" section to reflect pgvector + dynamic registration
  - Update the "Known Limitations" table — remove U-09 and U-12 entries
  - Update milestone status
- [ ] **6.3** Update [technical_specification_document.md](file:///d:/Git/agent-guard/docs/technical_specification_document.md):
  - Update Section 5.4 (Capability Registry) — replace "In-memory, substring-match" with pgvector description
  - Update Section 5.5 (Intent-Based Routing) — document vector similarity routing
  - Update Section 11.1–11.3 (Agent Registry Pattern) — rewrite to reflect PostgreSQL storage, HNSW index, dynamic registration, health checks
  - Add REST API endpoints for agent management to Section 7.5
  - Update Container Diagram (Section 2.2) to include PostgreSQL
  - Update Section 4 (System Context and Integrations) to mention PostgreSQL
- [ ] **6.4** Update [modernization_and_llm_integration.md](file:///d:/Git/agent-guard/docs/modernization_and_llm_integration.md):
  - Update "Remaining Gaps" section to remove registry-related items
  - Add note about the implemented pgvector + dynamic registration approach
- [ ] **6.5** Update [script_pattern.md](file:///d:/Git/agent-guard/docs/script_pattern.md) if it references static registry patterns
- [ ] **6.6** Update [testing_guide.md](file:///d:/Git/agent-guard/docs/testing_guide.md):
  - Add test commands for new test files (`test_db.py`, `test_registry.py`, `test_agents_api.py`, `test_health_checker.py`)
  - Document PostgreSQL test setup (local vs docker)

---

## 6. File Change Summary

| Action | File | Description |
|:-------|:-----|:------------|
| **NEW** | `agentguard/db.py` | PostgreSQL connection manager with pool, migrations, fallback |
| **NEW** | `agentguard/health_checker.py` | Background agent health monitor |
| **NEW** | `migrations/001_registry.sql` | PostgreSQL schema with pgvector |
| **NEW** | `backend/routes/agents.py` | Agent registry CRUD endpoints |
| **NEW** | `tests/test_db.py` | DatabaseManager tests |
| **NEW** | `tests/test_registry.py` | Registry tests (vector + fallback) |
| **NEW** | `tests/test_agents_api.py` | Agent API endpoint tests |
| **NEW** | `tests/test_health_checker.py` | Health checker tests |
| **MODIFY** | `agentguard/registry.py` | PostgreSQL-backed with vector search |
| **MODIFY** | `agentguard/orchestrator.py` | Skip LLM intent extraction when vector search available |
| **MODIFY** | `agentguard/__init__.py` | Export `DatabaseManager` |
| **MODIFY** | `backend/dependencies.py` | Add `get_database()`, update `get_registry()` |
| **MODIFY** | `backend/main.py` | Wire agents router, start health checker |
| **MODIFY** | `backend/schemas.py` | Add agent request/response models, update `HealthResponse` |
| **MODIFY** | `backend/routes/health.py` | Add PostgreSQL probe |
| **MODIFY** | `docker-compose.poc.yml` | Add PostgreSQL + DbGate services |
| **MODIFY** | `pyproject.toml` | Add psycopg, pgvector dependencies |
| **MODIFY** | `.env.example` | Add PostgreSQL env vars |
| **MODIFY** | `docs/unknown_items.md` | Close U-09 and U-12 |
| **MODIFY** | `docs/implementation_plan.md` | Update architecture, API table, registry section |
| **MODIFY** | `docs/technical_specification_document.md` | Update Sections 2.2, 4, 5.4, 5.5, 7.5, 11 |
| **MODIFY** | `docs/modernization_and_llm_integration.md` | Update remaining gaps |
| **MODIFY** | `docs/testing_guide.md` | Add new test commands |

---

## 7. Verification Plan

### Automated Tests

```bash
# All new tests
PYTHONPATH=. uv run pytest tests/test_db.py -v
PYTHONPATH=. uv run pytest tests/test_registry.py -v
PYTHONPATH=. uv run pytest tests/test_agents_api.py -v
PYTHONPATH=. uv run pytest tests/test_health_checker.py -v

# All existing tests must still pass (backward compatibility)
PYTHONPATH=. uv run pytest tests/test_trace.py -v
PYTHONPATH=. uv run pytest tests/test_hitl.py -v
PYTHONPATH=. uv run pytest tests/test_auth.py -v
PYTHONPATH=. uv run pytest tests/test_api.py -v
PYTHONPATH=. uv run pytest tests/test_parallel.py -v
```

### Key Test Scenarios

| Test | What it validates |
|:-----|:------------------|
| `test_registry_in_memory_fallback` | Registry works without PostgreSQL (backward compat) |
| `test_registry_postgres_upsert` | Agent registration persists to PostgreSQL |
| `test_registry_vector_search` | `search_by_intent()` returns cosine-ranked results |
| `test_registry_vector_vs_substring` | Vector search outperforms substring on ambiguous intents |
| `test_registry_deactivate_excludes_from_search` | Deactivated agents don't appear in search results |
| `test_registry_recompute_embeddings` | All agents get new embeddings after provider change |
| `test_api_register_agent` | `POST /agents/register` creates agent and returns metadata |
| `test_api_register_upsert` | Re-registering updates metadata without duplicating |
| `test_api_search_agents` | `POST /agents/search` returns ranked results |
| `test_api_deactivate_agent` | `DELETE /agents/{id}` soft-deletes |
| `test_api_activate_agent` | `POST /agents/{id}/activate` re-activates |
| `test_health_probe_postgres` | `/health` reports PostgreSQL status |
| `test_health_checker_marks_unhealthy` | Background checker detects dead endpoint |
| `test_planner_skips_llm_with_vector` | `ExecutionPlanner.plan()` bypasses LLM intent call |

### Manual Verification

1. Start the full stack: `docker compose -f docker-compose.poc.yml up -d`
2. Register an agent via REST:
   ```bash
   curl -X POST http://localhost:8000/agents/register \
     -H "Content-Type: application/json" \
     -H "X-Api-Key: $AGENTGUARD_API_KEY" \
     -d '{
       "id": "trade_agent_01",
       "role": "Trade Execution Specialist",
       "semantic_description": "Constructs FIX order tickets and routes to execution venues.",
       "input_schema": {"ticker": "string", "quantity": "int"},
       "output_schema": {"order_id": "string", "fill_price": "float"}
     }'
   ```
3. Search by intent:
   ```bash
   curl -X POST http://localhost:8000/agents/search \
     -H "Content-Type: application/json" \
     -H "X-Api-Key: $AGENTGUARD_API_KEY" \
     -d '{"intent": "execute a stock trade for MSFT"}'
   ```
4. Verify the agent appears in DbGate (port 3000)
5. Run a full task via `POST /runs` and verify the newly registered agent is routed to
6. Restart the backend container and verify the agent is still registered (persistence)

---

## 8. Migration & Backward Compatibility

> [!IMPORTANT]
> **Zero breaking changes for existing users.** Every `Registry()` call without a `db` parameter continues to work exactly as before — in-memory, substring matching, no PostgreSQL required.

The upgrade path:

1. **No PostgreSQL** — everything works as today. `DatabaseManager.available` is `False`, `Registry` uses in-memory dict.
2. **PostgreSQL without embeddings** — agents persist across restarts, ILIKE substring matching replaces in-memory substring matching.
3. **PostgreSQL with embeddings** — full vector similarity search. Best routing accuracy.

This gradual degradation means:
- Local development with `uv run uvicorn ...` needs no database at all
- Docker compose users get persistence and vector search automatically
- No migration script needs to run manually — `DatabaseManager` auto-executes `001_registry.sql` on first connection

---

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|:-----|:-----------|:-------|:-----------|
| Embedding dimension mismatch (switch providers mid-deployment) | Medium | Queries return 0 results | `recompute_embeddings()` method + startup dimension validation |
| PostgreSQL unavailable in production | Low | Registry falls back to in-memory (no persistence) | Health probe + alerting on `/health` endpoint |
| HNSW index rebuild on schema change | Low | Temporary slow queries during rebuild | Index creation is `IF NOT EXISTS` — only built once |
| Existing tests break due to `Registry` constructor change | Medium | CI fails | Default `db=None` preserves backward compat; all existing tests pass without change |
