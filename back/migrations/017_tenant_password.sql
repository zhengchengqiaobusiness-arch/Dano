-- 后台登录:每个租户一个用户名/密码账号。
-- username 默认取租户名(如 acme),唯一;password_hash 为 scrypt$N$r$p$salt$hash。
-- 密码明文永不入库;改密码走 /auth/change-password 写回 password_hash。

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS username TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS password_hash TEXT;

-- 已有租户回填:账号名 = 租户名,密码未设置(由管理员后续通过 change-password 或重置脚本补齐)。
UPDATE tenants SET username = tenant WHERE username IS NULL OR username = '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_tenants_username ON tenants (username);
