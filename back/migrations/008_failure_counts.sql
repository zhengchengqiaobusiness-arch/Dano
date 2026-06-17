-- 流程10:同类失败计数持久化(原为内存 InMemoryCounter,重启即丢 → 已熔断 Skill 复活)。
-- counter_key = "fail:{skill_id}"(或 CircuitBreaker 的 "{skill_id}:{failure_class}");
-- 达阈值由保障期暂停 Skill,自愈成功后 reset_prefix 清零。跨进程重启留存。

CREATE TABLE IF NOT EXISTS failure_counts (
    counter_key TEXT        PRIMARY KEY,
    count       INT         NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
