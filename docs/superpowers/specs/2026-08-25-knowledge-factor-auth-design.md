# 知识因子登录方案设计

日期：2026-08-25
范围：后台登录的密码强化 + TOTP 二因子
不在范围：会话机制改造（`api_key` 的语义与生命周期保持不变）、`POST /tenants` 的鉴权缺失

## 背景

当前后台登录只有用户名 + 密码（scrypt 哈希，`back/dano/infra/passwords.py`）。缺三样东西：

- 登录失败没有任何限流，`/auth/login` 可被无限枚举
- 密码策略薄弱：改密只校验 ≥8 位，建租户时完全不校验
- 没有第二因子

本设计补齐这三项，构成完整的知识因子方案。

`/auth/login` 的产出仍是租户 `api_key`，前端继续用 `X-Tenant-Key` 访问。把"人的登录态"与"机器凭证"拆开是另一件事，不在本次范围。

## 决策摘要

| 决策点 | 选择 | 理由 |
|---|---|---|
| TOTP 强制性 | 租户自愿开启 | 向后兼容，现有租户不受影响 |
| 两步登录形状 | challenge 令牌两步走 | 密码只在网络上出现一次；两步各自独立限流 |
| 状态存储 | InMemory / Pg 双实现 | 与现有 `registry` 架构一致，不引入 Redis |
| 二维码 | 后端纯 Python 生成 SVG | 前后端都不加依赖 |
| 限流策略 | 按账号递增退避 | 抵御持续爆破，不会因一次误输长时间锁死 |
| 密码策略 | 长度 ≥12 + 弱密码黑名单 | 符合 NIST 现行建议，不强制字符组合 |

## 模块划分

`gateway/app.py` 已达 71 KB 并承载全部路由，认证逻辑不再往里堆。新建 `back/dano/auth/` 包，`app.py` 只做 HTTP 编排（解析请求 → 调用 auth 服务 → 转成响应码）。

| 模块 | 职责 | 依赖 |
|---|---|---|
| `auth/policy.py` | 密码强度校验：长度、弱密码黑名单、与用户名/租户名雷同 | 无 |
| `auth/totp.py` | RFC 6238 TOTP：Base32 密钥、6 位码生成与校验、`otpauth://` URI | 标准库 `hmac` / `struct` |
| `auth/qrcode.py` | 纯 Python QR 编码器（byte 模式，EC-M）→ SVG | 无 |
| `auth/backup_codes.py` | 备用码生成与一次性核销 | `hashlib` |
| `auth/throttle.py` | 递增退避决策（纯函数：失败次数 + 当前时间 → 是否锁定、锁到几时） | 无 |
| `auth/challenge.py` | 两步登录 challenge 的签发与消费 | `auth/store` |
| `auth/store.py` | 认证瞬态状态的 InMemory / Pg 双实现 | `asyncpg` |
| `auth/service.py` | 编排：登录、二步校验、改密、TOTP 绑定与解绑 | 以上全部 + `registry` |

`infra/passwords.py` 保持现状，继续只负责哈希。

每个模块都可独立测试：`totp.py` 用 RFC 6238 官方向量，`throttle.py` 是接受注入时钟的纯函数，`qrcode.py` 与参考实现对拍。

### 关于 qrcode.py 的成本

byte 模式 + Reed-Solomon 纠错 + 版本选择约 250 行，是这批里最大的单块代码。它完全自包含且可用标准向量验证，属于"不引入依赖"的代价。若判断不值，可退回"只展示 Base32 密钥 + `otpauth://` 链接"，其余设计不受影响。

## 数据模型

### 迁移 `018_tenant_totp.sql`

给 `tenants` 表加四列（长期状态，与租户同生命周期）：

```sql
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS totp_pending TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS totp_activated_at TIMESTAMPTZ;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS backup_codes TEXT[];
```

- `totp_secret`：已激活的 Base32 密钥，空表示未绑定
- `totp_pending`：绑定流程中、尚未通过验证的密钥
- `backup_codes`：备用码的 sha256 哈希数组，用一个核销一个

### 迁移 `019_auth_state.sql`

两张瞬态表，可随时清空而不影响业务数据：

```sql
CREATE TABLE IF NOT EXISTS auth_failures (
  username      TEXT PRIMARY KEY,
  fail_count    INT NOT NULL DEFAULT 0,
  locked_until  TIMESTAMPTZ,
  last_totp_step BIGINT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_challenges (
  challenge_hash TEXT PRIMARY KEY,
  tenant         TEXT NOT NULL,
  username       TEXT NOT NULL,
  totp_failures  INT NOT NULL DEFAULT 0,
  expires_at     TIMESTAMPTZ NOT NULL,
  consumed       BOOLEAN NOT NULL DEFAULT FALSE
);
```

TOTP 防重放游标（`last_totp_step`）与失败计数同行，省一张表。

`InMemoryAuthStore` 与 `PgAuthStore` 实现同一组方法，`app.py` 的 lifespan 按 DB 可用性切换——DB 不可用时降级到内存，与现有 registry 的行为一致。

限流 key 使用 `username.casefold()`；租户查找仍按原样精确匹配，不改动 `idx_tenants_username` 的语义。

## 登录流程

### `POST /auth/login {username, password}`

1. 先查限流。账号处于锁定期 → **直接返回 401，不执行 scrypt**。
   锁定期仍跑 scrypt 等于给攻击者一个 CPU/内存放大器（每次 16 MB、数十毫秒）。代价是合法用户无法得知"密码其实是对的"，因此统一文案写作「用户名或密码错误；连续失败会临时锁定账号」——既不泄露账号是否存在，也为被锁用户提供解释。
2. 用户不存在或未设密码 → 对一个模块内固定的哑元哈希执行一次 `verify_password` 后返回 401，抹平时序差异。
3. 密码错误 → 失败计数 +1，按退避算出新的 `locked_until` → 401。
4. 密码正确：
   - 未绑定 TOTP → 清零计数，返回 `{tenant, api_key}`（与当前行为完全一致）
   - 已绑定 → 清零密码失败计数，签发 challenge，返回 `{need_totp: true, challenge, expires_in: 300}`

### `POST /auth/login/totp {challenge, code}`

challenge 不存在、已消费或已过期 → 401。

`code` 同时接受 6 位 TOTP 与备用码：备用码为 10 位 Base32 字符（约 50 bit 熵），展示时分组为 `XXXXX-XXXXX`，校验前去除分隔符与大小写差异，按去分隔后的长度（6 与 10）区分两类。TOTP 校验容忍 ±1 个时间步（前后 30 秒），成功后将该时间步写入 `last_totp_step`，同一码不可重放。

这一步有**独立的失败计数**：6 位码仅一百万种组合，不限流等同于没有二因子。失败次数达到 `DANO_AUTH_MAX_FAILURES` 即作废该 challenge 并按同一退避公式锁定账号，用户需从密码步重来。

### TOTP 管理接口（均需 `X-Tenant-Key`）

| 接口 | 行为 |
|---|---|
| `POST /auth/totp/setup` | 生成 pending 密钥，返回 `{secret, uri, qr_svg_data_uri}`；已激活则 409 |
| `POST /auth/totp/activate {code}` | 校验 pending 码 → 激活 → 返回 10 个备用码明文（仅此一次） |
| `POST /auth/totp/disable {password, code}` | 密码 + 有效码双重确认，清空密钥与备用码；未绑定时返回 409 |
| `POST /auth/totp/backup-codes {password, code}` | 重新生成备用码，旧的全部作废 |

### `POST /auth/change-password` 改造

- 接入密码强度校验
- **已绑定 TOTP 时额外要求 `code`**。`X-Tenant-Key` 是长期凭证，若持 key 即可改密，二因子会被绕过
- 成功后清空该账号所有未消费 challenge 并重置失败计数

### `POST /tenants`

仅增加密码强度校验（在有密码时）。该接口目前无鉴权，任何人仍可自助建租户获取 `api_key`——这会绕过本设计的全部防护，但属于独立问题，建议单独处理。

## 安全细节

- **challenge** 用 `secrets.token_urlsafe(32)` 生成，库中存 `sha256(challenge)` 作主键，明文仅存在于响应与前端内存
- **备用码用 sha256 而非 scrypt**。备用码是约 50 bit 的高熵随机串，不存在字典攻击面；慢哈希只会让每次登录多付 10×scrypt（约 300 ms）。密码用慢哈希、高熵凭证用快哈希，是两类不同场景
- **常数时间比较**：TOTP 码与备用码一律走 `hmac.compare_digest`
- **二维码以 data URI 返回**，前端用 `<img src="data:image/svg+xml;base64,...">` 渲染，而非将 SVG 注入 DOM——内容虽完全由后端生成，也不给自己留注入面
- **日志红线**：密码、TOTP 密钥、备用码、challenge 明文一律不入日志

### 新增配置项

| 变量 | 默认值 | 用途 |
|---|---|---|
| `DANO_AUTH_MAX_FAILURES` | 5 | 触发锁定的连续失败次数 |
| `DANO_AUTH_LOCK_MAX_MINUTES` | 30 | 锁定时长上限 |
| `DANO_AUTH_CHALLENGE_TTL_SECONDS` | 300 | challenge 有效期 |
| `DANO_AUTH_MIN_PASSWORD_LENGTH` | 12 | 密码最小长度 |

退避公式：连续失败达到 `MAX_FAILURES` 后开始锁定，锁定时长为 `min(2^(fails - MAX_FAILURES) 分钟, LOCK_MAX_MINUTES)`，登录成功后清零。

## 测试策略

`back/tests/` 目前为空（此前的测试已被删除），需从零建立。`pyproject.toml` 中 pytest、pytest-asyncio 与 `asyncio_mode = "auto"` 均保留，直接沿用。

| 测试文件 | 覆盖 |
|---|---|
| `test_auth_totp.py` | RFC 6238 官方测试向量（SHA-1 组）；±1 时间步容忍；同码重放被拒 |
| `test_auth_qrcode.py` | 与 `qrcode` 包对拍，验证手写编码器的模块矩阵一致 |
| `test_auth_throttle.py` | 纯函数 + 注入时钟：触发阈值、翻倍退避、上限封顶、成功清零 |
| `test_auth_policy.py` | 长度、弱密码黑名单、与用户名/租户名雷同 |
| `test_auth_login_flow.py` | 端到端：未绑定单步登录、绑定后两步、验证码错触发独立限流、锁定期不执行 scrypt、备用码一次性、改密要求验证码 |
| `test_auth_store.py` | 一组契约测试跑两遍；`InMemoryAuthStore` 必过，`PgAuthStore` 在有 `DANO_PG_DSN` 时执行、否则 skip |

`qrcode` 包加入 `pyproject.toml` 的 `dev` 可选依赖，仅供测试对拍，生产依赖不变。

所有涉及时间的函数接受 `now` 参数（默认 `time.time()`），测试注入固定时钟，不使用 sleep。

## 前端改动

不新增任何 npm 依赖。

- `api/client.ts` 不动，`X-Tenant-Key` 机制保持原样
- `api/skills.ts`：`login` 返回类型改为联合类型（`{tenant, api_key}` 或 `{need_totp, challenge, expires_in}`）；新增 `loginTotp`、`totpSetup`、`totpActivate`、`totpDisable`、`regenerateBackupCodes`；`changePassword` 增加可选 `code`
- `pages/Tenant.tsx`：登录卡片增加第二步面板（6 位验证码输入 + "改用备用码"切换）；已登录区新增「两步验证」板块——未绑定时显示二维码与激活输入框，已绑定时显示解绑与重新生成备用码；备用码以弹窗展示并提示仅显示一次
- `pages/RegisterTenant.tsx`：密码规则提示改为 ≥12 位，与后端一致

## 兼容性

现有租户的 `totp_secret` 为空，登录行为与当前完全一致。`api_key` 的语义与生命周期不变。两张瞬态表被清空不影响任何业务数据。
