-- 流程12:Skill 生命周期状态机持久化。
-- 接入产出登记于此,驱动到运行态;保障期(流程10/11)暂停/恢复/回滚也更新此表。
-- skill_id = "{subsystem}.{action}",与运行期 SkillRegistry 一致,接入↔运行↔保障同源。

CREATE TABLE IF NOT EXISTS skill_lifecycle (
    skill_id      TEXT        PRIMARY KEY,
    subsystem     TEXT        NOT NULL,
    action        TEXT        NOT NULL,
    state         TEXT        NOT NULL,
    asset_version INT         NOT NULL DEFAULT 0,
    history       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_state ON skill_lifecycle (state);
CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_subsystem ON skill_lifecycle (subsystem);
