-- Dano 资产库初始 schema (M0)
-- 资产库是构建时(pi coding Agent)与运行时(执行层)之间的唯一合同。
-- 五类资产共享元数据信封(作用域/源指纹/版本/验证状态/置信/生成报告),资产体存 JSONB。

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- pgvector 用于连接器知识索引(流程6.1)。**可选**:未安装时自动跳过,
-- 知识检索改走 Python 端内存/向量索引(VectorKnowledgeIndex),主路径不受影响。
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pgvector 不可用,跳过向量索引表(知识检索走 Python 内存索引)';
    END;
END $$;

-- ─────────────────────────────────────────────────────────────
-- assets:五类企业资产,append-only 版本化(旧版本保留可回滚)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    asset_id           UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    asset_type         TEXT        NOT NULL
        CHECK (asset_type IN ('field_mapping','connector','policy_rule','env_profile','page_script','workflow')),
    -- 作用域 = 租户 + 系统实例(A-OA / A-工单 / A-报销)
    tenant             TEXT        NOT NULL,
    subsystem          TEXT        NOT NULL,
    -- 作用域内的逻辑资产标识:连接器=动作名(每动作一份),其余资产=类型(每作用域一份)。
    -- 版本号按 asset_key 独立递增,以区分同一子系统下的多个连接器。
    asset_key          TEXT        NOT NULL DEFAULT 'default',
    version            INT         NOT NULL,
    source_fingerprint TEXT        NOT NULL,
    validation_status  TEXT        NOT NULL DEFAULT 'draft'
        CHECK (validation_status IN ('draft','testing','verified','published','deprecated')),
    confidence         REAL        NOT NULL DEFAULT 0,
    human_confirmed    BOOLEAN     NOT NULL DEFAULT FALSE,
    generation_report  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    body               JSONB       NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 同一逻辑资产(类型+作用域+key)的版本号唯一:升级 = 插入新版本行,不覆盖旧版本
    UNIQUE (asset_type, tenant, subsystem, asset_key, version)
);

CREATE INDEX IF NOT EXISTS idx_assets_scope
    ON assets (tenant, subsystem, asset_type, asset_key);
CREATE INDEX IF NOT EXISTS idx_assets_status
    ON assets (validation_status);
CREATE INDEX IF NOT EXISTS idx_assets_fingerprint
    ON assets (source_fingerprint);
-- 资产体上的 GIN 索引,便于按 JSONB 字段检索
CREATE INDEX IF NOT EXISTS idx_assets_body_gin
    ON assets USING GIN (body);

-- ─────────────────────────────────────────────────────────────
-- knowledge_index:连接器双重用途(流程 6.1 / 流程 3 落地点)
-- 仅在 pgvector 可用时建(否则知识检索走 Python 内存索引)。
-- ─────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        EXECUTE $ddl$
            CREATE TABLE IF NOT EXISTS knowledge_index (
                id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
                asset_id    UUID        NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
                tenant      TEXT        NOT NULL,
                subsystem   TEXT        NOT NULL,
                endpoint    TEXT,
                action      TEXT,
                summary     TEXT,
                io_spec     JSONB       NOT NULL DEFAULT '{}'::jsonb,
                embedding   vector(1536),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_scope ON knowledge_index (tenant, subsystem);
        $ddl$;
    END IF;
END $$;
