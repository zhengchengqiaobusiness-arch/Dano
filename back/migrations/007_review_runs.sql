-- 三模型评审委员会证据(接入期发布前硬闸门,REWRITE_PLAN §4 之上叠加):
-- 成果验收 / 漏洞检测 / 合规审核 各由一个独立模型评,结论写此表(不可由 agent 伪造)。
-- 发布时只认 review_run_id 列表,后端重读校验:全 passed、未过期、content_hash 绑定、
-- role 覆盖三审、distinct(model_id)=3(强制"换 3 个不同模型")。

CREATE TABLE IF NOT EXISTS review_runs (
    review_run_id   UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_draft_id  UUID        NOT NULL REFERENCES asset_drafts(asset_draft_id) ON DELETE CASCADE,
    content_hash    TEXT        NOT NULL,                 -- = 草案 content_hash,防"换草案"
    role            TEXT        NOT NULL
        CHECK (role IN ('acceptance','security','compliance')),
    model_id        TEXT        NOT NULL,                 -- 评审所用模型(闸门校验三者互不相同)
    passed          BOOLEAN     NOT NULL,
    findings        JSONB,                                -- 评审理由/发现(reasons[] 等)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + interval '1 hour'  -- 评审时效
);
CREATE INDEX IF NOT EXISTS idx_review_runs_draft ON review_runs (asset_draft_id);
