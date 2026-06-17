-- 流程1 第1–3步:租户 + 系统实例登记(接入前的元数据)。
-- 把文档的「建租户 / 选系统类型模板 / 创建系统实例」落成可审计的真实记录,
-- 接入(流程1 第4步起)针对已登记的系统实例导入材料、生成资产。

CREATE TABLE IF NOT EXISTS tenants (
    tenant        TEXT        PRIMARY KEY,           -- 租户标识(如 a-corp)
    display_name  TEXT        NOT NULL DEFAULT '',
    deploy        TEXT        NOT NULL DEFAULT '',   -- 部署方式
    worker_location TEXT      NOT NULL DEFAULT '',   -- Worker 位置
    log_policy    TEXT        NOT NULL DEFAULT '',   -- 日志策略
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_instances (
    tenant        TEXT        NOT NULL REFERENCES tenants(tenant) ON DELETE CASCADE,
    subsystem     TEXT        NOT NULL,              -- A-OA / A-工单 / A-报销
    type_template TEXT        NOT NULL,              -- 系统类型模板:oa / ticket / reimburse
    integration   TEXT        NOT NULL               -- api / page
        CHECK (integration IN ('api', 'page')),
    status        TEXT        NOT NULL DEFAULT 'created'  -- created / onboarded
        CHECK (status IN ('created', 'onboarded')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, subsystem)
);

CREATE INDEX IF NOT EXISTS idx_instances_tenant ON system_instances (tenant);
