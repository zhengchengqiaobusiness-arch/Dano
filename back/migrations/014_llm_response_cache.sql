-- Canonical LLM result cache. Only the request hash and structured output are
-- persisted; prompts may contain tenant business metadata and are never stored.
CREATE TABLE IF NOT EXISTS llm_response_cache (
    cache_key      TEXT PRIMARY KEY,
    model          TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    response       JSONB NOT NULL,
    prompt_tokens  BIGINT NOT NULL DEFAULT 0,
    output_tokens  BIGINT NOT NULL DEFAULT 0,
    expires_at     TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_hit_at    TIMESTAMPTZ,
    hit_count      BIGINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_response_cache_expiry
    ON llm_response_cache (expires_at);
