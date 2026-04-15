-- migrations/001_registry.sql
-- U-09 / U-12: PostgreSQL vector-backed agent registry
-- Idempotent — safe to run multiple times.

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Agent registry table
CREATE TABLE IF NOT EXISTS agent_registry (
    id              TEXT PRIMARY KEY,                        -- stable unique agent identifier
    role            TEXT NOT NULL,                           -- human-readable capability label
    semantic_description TEXT NOT NULL,                      -- prose description for intent routing
    input_schema    JSONB NOT NULL DEFAULT '{}',             -- expected input contract
    output_schema   JSONB NOT NULL DEFAULT '{}',             -- output contract
    endpoint        TEXT NOT NULL DEFAULT '',                -- optional HTTP endpoint for remote agents
    embedding       vector(1536),                            -- pgvector embedding of semantic_description
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,           -- soft-delete / deactivation flag
    health_status   TEXT NOT NULL DEFAULT 'unknown',         -- 'healthy' | 'unhealthy' | 'unknown'
    last_heartbeat  TIMESTAMPTZ,                             -- last successful health check
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),      -- creation timestamp
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()       -- last modification timestamp
);

-- HNSW index for fast cosine similarity search.
-- Only created after initial data load for best build performance.
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

DROP TRIGGER IF EXISTS trg_agent_updated_at ON agent_registry;
CREATE TRIGGER trg_agent_updated_at
    BEFORE UPDATE ON agent_registry
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();
