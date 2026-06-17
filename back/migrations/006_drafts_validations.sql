-- 阶段一发布硬关卡的存储底座(REWRITE_PLAN §4):
-- asset_drafts:pi 起草但未发布的资产草案;validation_runs:后端生成的验证证据(不可由 agent 伪造)。
-- 发布时只认 validation_run_id 列表,后端重读这些记录并校验 content_hash/租户/通过/未过期。

CREATE TABLE IF NOT EXISTS asset_drafts (
    asset_draft_id   UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id           TEXT        NOT NULL,                 -- 哪次 pi run 产出
    tenant           TEXT        NOT NULL,
    subsystem        TEXT        NOT NULL,
    asset_type       TEXT        NOT NULL,
    asset_key        TEXT        NOT NULL,
    body             JSONB       NOT NULL,
    content_hash     TEXT        NOT NULL,                 -- 绑定:验证证据须对应同一 hash
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_asset_drafts_run ON asset_drafts (run_id);
CREATE INDEX IF NOT EXISTS idx_asset_drafts_scope ON asset_drafts (tenant, subsystem, asset_type, asset_key);

CREATE TABLE IF NOT EXISTS validation_runs (
    validation_run_id UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_draft_id    UUID        NOT NULL REFERENCES asset_drafts(asset_draft_id) ON DELETE CASCADE,
    content_hash      TEXT        NOT NULL,                -- = 草案 content_hash,防"换草案"
    kind              TEXT        NOT NULL
        CHECK (kind IN ('connect','sandbox','readback','health','replay')),
    environment       TEXT        NOT NULL DEFAULT 'sandbox',     -- 红线:只准 sandbox
    credential_type   TEXT        NOT NULL DEFAULT 'test',        -- 红线:只准 test 凭证
    request           JSONB,
    response          JSONB,
    evidence          JSONB,
    passed            BOOLEAN     NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL DEFAULT now() + interval '1 hour'  -- 证据时效
);
CREATE INDEX IF NOT EXISTS idx_validation_runs_draft ON validation_runs (asset_draft_id);
