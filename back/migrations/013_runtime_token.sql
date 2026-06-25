-- 运行期 token 凭证:页面型(抓请求)skill 运行期鉴权用的一组头(Authorization/Tenant-Id/satoken…)。
-- 录制时自动抓存一份;过期前端 PUT 换一份即可,免重录。按 (tenant, subsystem) 唯一。
-- 与登录态快照(storage_state 文件)互补:这里只存可单独查/刷新的鉴权头,落库 → 重启不丢。

CREATE TABLE IF NOT EXISTS runtime_token (
    tenant      TEXT        NOT NULL,
    subsystem   TEXT        NOT NULL,
    headers     JSONB       NOT NULL,
    source      TEXT        NOT NULL DEFAULT 'recording',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, subsystem)
);
