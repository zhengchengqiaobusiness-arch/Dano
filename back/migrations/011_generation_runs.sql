-- goal 模式代码生成的可追溯记录:每次 GenerationLoop.run 一行,迭代明细存 JSONB。
-- 用途:审计"系统自动写了什么代码、第几轮被哪个关卡以什么理由驳回、最终发布了哪个资产"。

CREATE TABLE IF NOT EXISTS generation_runs (
    generation_run_id  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id             TEXT        NOT NULL,
    tenant             TEXT        NOT NULL,
    subsystem          TEXT        NOT NULL,
    flow               TEXT        NOT NULL,
    strategy           TEXT,
    ok                 BOOLEAN     NOT NULL,
    asset_id           UUID,
    rejections         INTEGER     NOT NULL DEFAULT 0,
    iterations         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    reason             TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_generation_runs_scope
    ON generation_runs (tenant, subsystem, flow, created_at DESC);
