-- 登录的瞬态状态:失败计数与两步登录 challenge。
-- 两张表可随时清空,不影响任何业务数据(代价仅是限流计数归零)。
-- username 一律存 casefold 后的值,与限流 key 保持一致。
CREATE TABLE IF NOT EXISTS auth_failures (
  username       TEXT PRIMARY KEY,
  fail_count     INT NOT NULL DEFAULT 0,
  locked_until   DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_totp_step BIGINT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- challenge 明文只出现在响应与前端内存,库里只有 sha256。
CREATE TABLE IF NOT EXISTS auth_challenges (
  challenge_hash TEXT PRIMARY KEY,
  tenant         TEXT NOT NULL,
  username       TEXT NOT NULL,
  totp_failures  INT NOT NULL DEFAULT 0,
  expires_at     DOUBLE PRECISION NOT NULL,
  consumed       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_username ON auth_challenges (username);
