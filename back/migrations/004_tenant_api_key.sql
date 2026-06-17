-- 公司唯一标识:租户 API Key,用于 Skill 网关鉴权与多租户隔离。
-- 前端凭 api_key 调用,只能访问/调用本租户名下的 Skill。

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS api_key TEXT;

-- 已有租户补一个 key(随机),保证非空唯一
UPDATE tenants SET api_key = 'dk_' || replace(gen_random_uuid()::text, '-', '')
    WHERE api_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_api_key ON tenants (api_key);
