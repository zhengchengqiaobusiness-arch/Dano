-- 租户后台登录的 TOTP 二因子:密钥与一次性备用码。
-- totp_secret 空 = 未绑定;totp_pending 是绑定流程中尚未验证的密钥;
-- backup_codes 存 sha256 哈希,用一个核销一个。明文永不入库。
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS totp_pending TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS backup_codes TEXT[];
