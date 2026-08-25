# 知识因子登录 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Dano 后台登录补齐知识因子——密码策略、失败递增退避限流、TOTP 二因子与一次性备用码。

**Architecture:** 新建 `back/dano/auth/` 包承载全部认证逻辑，`gateway/app.py` 只做 HTTP 编排。认证瞬态状态（失败计数、challenge）用 `InMemoryAuthStore` / `PgAuthStore` 双实现，与现有 registry 一致；TOTP 密钥与备用码哈希落 `tenants` 表新列。已绑定 TOTP 的账号走 challenge 令牌两步登录。

**Tech Stack:** Python 3.12 / FastAPI / asyncpg / pytest + pytest-asyncio；前端 React 18 + Ant Design。除测试用的 `segno` 外不新增任何依赖。

## Global Constraints

- 设计来源：`docs/superpowers/specs/2026-08-25-knowledge-factor-auth-design.md`
- 生产依赖不得新增；`segno>=1.6` 只进 `pyproject.toml` 的 `dev` 可选依赖，仅用于测试对拍
- 所有涉及时间的函数接受 `now: float | None = None` 参数，默认 `time.time()`；测试注入固定时钟，禁止 `sleep`
- 密码、TOTP 密钥、备用码明文、challenge 明文一律不得写入日志
- 限流 key 一律 `username.casefold()`；租户查找仍按原样精确匹配
- 常数时间比较统一用 `hmac.compare_digest`
- 注释与用户可见文案使用中文，与现有代码风格一致
- 每个任务结束即 commit，commit message 用中文，格式 `feat(auth): ...` / `test(auth): ...`
- 后端命令一律用 `/Users/wei.quan/.conda/envs/ai-agents/bin/python`（`back/.venv` 未装依赖）

### 相对 spec 的一处偏差

spec 的迁移 018 列出了 `totp_activated_at` 列，但设计中没有任何逻辑读它。按 YAGNI 从迁移与数据模型中去掉；若日后需要审计时间戳，再加一条迁移。

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `back/dano/auth/__init__.py` | 空包标记 |
| `back/dano/auth/policy.py` | 密码强度校验 |
| `back/dano/auth/totp.py` | RFC 6238 TOTP |
| `back/dano/auth/backup_codes.py` | 备用码生成与核销 |
| `back/dano/auth/throttle.py` | 递增退避纯函数 |
| `back/dano/auth/qrcode.py` | QR 编码器 → SVG data URI |
| `back/dano/auth/challenge.py` | challenge 令牌生成与哈希 |
| `back/dano/auth/store.py` | 认证瞬态状态双实现 |
| `back/dano/auth/service.py` | 认证流程编排 |
| `back/migrations/018_tenant_totp.sql` | tenants 表 TOTP 列 |
| `back/migrations/019_auth_state.sql` | auth_failures / auth_challenges |
| `back/tests/test_auth_policy.py` 等 6 个 | 见各任务 |

**修改**

| 文件 | 改动 |
|---|---|
| `back/dano/config.py` | 4 个 `auth_*` 配置项 |
| `back/dano/registry/models.py` | `TenantRecord` 加 3 个 TOTP 字段 |
| `back/dano/registry/store.py` | 两套 registry 加 4 个 TOTP 方法 |
| `back/dano/gateway/app.py` | 登录路由改造 + 4 个 TOTP 路由 + lifespan 装配 auth store |
| `back/pyproject.toml` | dev 依赖加 `segno` |
| `skillfrontend/src/api/skills.ts` | 登录返回联合类型 + 5 个新函数 |
| `skillfrontend/src/pages/Tenant.tsx` | 两步登录 + 两步验证管理面板 |
| `skillfrontend/src/pages/RegisterTenant.tsx` | 密码规则提示 |

---

### Task 1: 配置项与密码策略

**Files:**
- Create: `back/dano/auth/__init__.py`, `back/dano/auth/policy.py`
- Modify: `back/dano/config.py`（在 `token_refresh_key` 之后插入 `# ── 后台登录 ──` 段落）
- Test: `back/tests/test_auth_policy.py`

**Interfaces:**
- Consumes: 无
- Produces: `PasswordPolicyError(ValueError)`；`validate_password(password: str, *, username: str = "", tenant: str = "", min_length: int = 12) -> None`（不合规抛 `PasswordPolicyError`，`str(e)` 即面向用户的中文提示）；配置 `Settings.auth_min_password_length` / `auth_max_failures` / `auth_lock_max_minutes` / `auth_challenge_ttl_seconds`

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_auth_policy.py
import pytest

from dano.auth.policy import PasswordPolicyError, validate_password


def test_长度不足被拒():
    with pytest.raises(PasswordPolicyError, match="至少 12 位"):
        validate_password("short1234")


def test_合规密码通过():
    validate_password("correct-horse-battery")


def test_弱密码被拒():
    with pytest.raises(PasswordPolicyError, match="过于常见"):
        validate_password("password1234")


def test_与用户名雷同被拒():
    with pytest.raises(PasswordPolicyError, match="用户名"):
        validate_password("AcmeAcmeAcme", username="acme")


def test_与租户名雷同被拒():
    with pytest.raises(PasswordPolicyError, match="租户名"):
        validate_password("contoso-contoso", tenant="contoso")


def test_min_length_可配():
    validate_password("12345678", min_length=8)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_policy.py -v`（cwd = `back/`）
Expected: FAIL，`ModuleNotFoundError: No module named 'dano.auth'`

- [ ] **Step 3: 实现**

```python
# back/dano/auth/policy.py
"""后台登录密码强度策略:长度 + 弱密码黑名单 + 与身份标识雷同检查。

按 NIST SP 800-63B 现行建议,只卡长度与已知弱口令,不强制大小写/符号组合
(组合规则实际会驱使用户选 Passw0rd! 这类可猜密码)。
"""

from __future__ import annotations

# 覆盖公开泄露榜单里最常见的一批;命中即拒,不做模糊匹配。
_WEAK: frozenset[str] = frozenset({
    "password", "passw0rd", "password1", "password12", "password123",
    "password1234", "12345678", "123456789", "1234567890", "123456789012",
    "qwertyuiop", "administrator", "letmein", "welcome", "iloveyou",
    "abc123456", "adminadmin", "changeme", "dano123456", "1qaz2wsx3edc",
})


class PasswordPolicyError(ValueError):
    """密码不符合策略;str(e) 直接作为面向用户的提示。"""


def validate_password(
    password: str,
    *,
    username: str = "",
    tenant: str = "",
    min_length: int = 12,
) -> None:
    """校验密码强度,不合规抛 PasswordPolicyError。"""
    if len(password) < min_length:
        raise PasswordPolicyError(f"密码至少 {min_length} 位")
    folded = password.casefold()
    if folded in _WEAK:
        raise PasswordPolicyError("密码过于常见,请换一个")
    for label, value in (("用户名", username), ("租户名", tenant)):
        candidate = (value or "").strip().casefold()
        if candidate and candidate in folded:
            raise PasswordPolicyError(f"密码不能包含{label}")
```

`back/dano/auth/__init__.py` 写入一行文档字符串：

```python
"""后台登录的知识因子实现:密码策略、限流、TOTP 二因子。"""
```

`back/dano/config.py` 在 `token_refresh_key` 行之后插入：

```python
    # ── 后台登录(知识因子:密码策略 + 失败退避 + TOTP 二因子)──
    auth_min_password_length: int = 12    # 建租户/改密统一的最小密码长度
    auth_max_failures: int = 5            # 连续失败到此次数开始锁定
    auth_lock_max_minutes: int = 30       # 递增退避的锁定时长上限
    auth_challenge_ttl_seconds: int = 300  # 两步登录 challenge 有效期
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_policy.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add back/dano/auth/__init__.py back/dano/auth/policy.py back/dano/config.py back/tests/test_auth_policy.py
git commit -m "feat(auth): 密码强度策略与登录相关配置项"
```

---

### Task 2: TOTP

**Files:**
- Create: `back/dano/auth/totp.py`
- Test: `back/tests/test_auth_totp.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `generate_secret(*, nbytes: int = 20) -> str` — 无填充 Base32
  - `totp_code(secret: str, *, now: float | None = None, step: int = 30, digits: int = 6) -> str`
  - `verify_code(secret: str, code: str, *, now: float | None = None, step: int = 30, digits: int = 6, window: int = 1, last_step: int | None = None) -> int | None` — 命中返回时间步序号，失败返回 `None`；`last_step` 用于拒绝重放
  - `provisioning_uri(secret: str, *, account: str, issuer: str = "Dano") -> str`

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_auth_totp.py
from dano.auth.totp import generate_secret, provisioning_uri, totp_code, verify_code

# RFC 6238 附录 B:种子 "12345678901234567890" 的 Base32,SHA-1 组
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc6238官方向量():
    for now, expected in [
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ]:
        assert totp_code(RFC_SECRET, now=now) == expected


def test_容忍前后一个时间步():
    now = 1111111109
    assert verify_code(RFC_SECRET, "081804", now=now) == now // 30
    assert verify_code(RFC_SECRET, "081804", now=now + 30) == now // 30
    assert verify_code(RFC_SECRET, "081804", now=now - 30) == now // 30
    assert verify_code(RFC_SECRET, "081804", now=now + 90) is None


def test_重放被拒():
    now = 1111111109
    step = now // 30
    assert verify_code(RFC_SECRET, "081804", now=now, last_step=step) is None
    assert verify_code(RFC_SECRET, "081804", now=now, last_step=step - 1) == step


def test_错误的码返回None():
    assert verify_code(RFC_SECRET, "000000", now=59) is None
    assert verify_code(RFC_SECRET, "abc", now=59) is None
    assert verify_code(RFC_SECRET, "", now=59) is None


def test_生成的密钥可用():
    secret = generate_secret()
    assert len(secret) == 32 and "=" not in secret
    assert verify_code(secret, totp_code(secret, now=1000), now=1000) == 1000 // 30


def test_provisioning_uri格式():
    uri = provisioning_uri(RFC_SECRET, account="acme", issuer="Dano")
    assert uri.startswith("otpauth://totp/Dano:acme?")
    assert f"secret={RFC_SECRET}" in uri
    assert "issuer=Dano" in uri
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_totp.py -v`
Expected: FAIL，`No module named 'dano.auth.totp'`

- [ ] **Step 3: 实现**

```python
# back/dano/auth/totp.py
"""RFC 6238 TOTP(HMAC-SHA1, 30 秒步长, 6 位),标准库实现,与 Google Authenticator 兼容。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

_STEP = 30
_DIGITS = 6


def generate_secret(*, nbytes: int = 20) -> str:
    """生成无填充 Base32 密钥(默认 160 bit,与 RFC 推荐一致)。"""
    return base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    padded = secret.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded, casefold=True)


def _code_at(secret_bytes: bytes, counter: int, digits: int) -> str:
    digest = hmac.new(secret_bytes, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFF_FFFF
    return str(truncated % (10 ** digits)).zfill(digits)


def totp_code(secret: str, *, now: float | None = None, step: int = _STEP,
              digits: int = _DIGITS) -> str:
    """算出指定时刻的 TOTP 码。"""
    moment = time.time() if now is None else now
    return _code_at(_decode_secret(secret), int(moment) // step, digits)


def verify_code(secret: str, code: str, *, now: float | None = None, step: int = _STEP,
                digits: int = _DIGITS, window: int = 1,
                last_step: int | None = None) -> int | None:
    """校验 TOTP 码,命中返回时间步序号,否则 None。

    window=1 表示容忍前后各一个时间步;last_step 是上次成功使用过的时间步,
    小于等于它的一律拒绝 —— 同一个码不能用第二次。
    """
    candidate = (code or "").strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != digits:
        return None
    try:
        secret_bytes = _decode_secret(secret)
    except (ValueError, TypeError):
        return None
    moment = time.time() if now is None else now
    current = int(moment) // step
    for delta in range(-window, window + 1):
        counter = current + delta
        if last_step is not None and counter <= last_step:
            continue
        if hmac.compare_digest(_code_at(secret_bytes, counter, digits), candidate):
            return counter
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "Dano") -> str:
    """生成 Authenticator 扫码用的 otpauth:// URI。"""
    label = quote(f"{issuer}:{account}", safe="")
    params = urlencode({"secret": secret, "issuer": issuer,
                        "algorithm": "SHA1", "digits": _DIGITS, "period": _STEP})
    return f"otpauth://totp/{label}?{params}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_totp.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add back/dano/auth/totp.py back/tests/test_auth_totp.py
git commit -m "feat(auth): RFC 6238 TOTP 生成与校验(含重放拒绝)"
```

---

### Task 3: 备用码

**Files:**
- Create: `back/dano/auth/backup_codes.py`
- Test: `back/tests/test_auth_backup_codes.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `generate_codes(count: int = 10) -> list[str]` — 形如 `"A7K2M-9PQR3"`
  - `normalize(code: str) -> str` — 去分隔符、去空格、转大写
  - `hash_code(code: str) -> str` — 归一化后的 sha256 十六进制
  - `consume(code: str, hashes: list[str]) -> list[str] | None` — 命中返回剩余哈希列表，未命中返回 `None`

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_auth_backup_codes.py
from dano.auth.backup_codes import consume, generate_codes, hash_code, normalize


def test_生成十个不重复的码():
    codes = generate_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    for code in codes:
        assert len(code) == 11 and code[5] == "-"


def test_归一化忽略分隔符与大小写():
    assert normalize("a7k2m-9pqr3") == "A7K2M9PQR3"
    assert normalize(" A7K2M 9PQR3 ") == "A7K2M9PQR3"


def test_核销后该码失效():
    codes = generate_codes()
    hashes = [hash_code(c) for c in codes]
    remaining = consume(codes[3], hashes)
    assert remaining is not None
    assert len(remaining) == 9
    assert consume(codes[3], remaining) is None


def test_未命中返回None():
    hashes = [hash_code(c) for c in generate_codes()]
    assert consume("ZZZZZ-ZZZZZ", hashes) is None


def test_不含易混字符():
    joined = "".join(normalize(c) for c in generate_codes(count=50))
    assert not set(joined) & set("OIL01")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_backup_codes.py -v`
Expected: FAIL，`No module named 'dano.auth.backup_codes'`

- [ ] **Step 3: 实现**

```python
# back/dano/auth/backup_codes.py
"""TOTP 备用码:10 位随机字符(约 50 bit 熵),一次性核销。

用 sha256 而非 scrypt:备用码是高熵随机串,不存在字典攻击面,
慢哈希只会让每次登录多付 10 倍 scrypt 成本。密码用慢哈希、高熵凭证用快哈希。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# 去掉 O/I/L/0/1 等易混字符,便于用户手抄
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LENGTH = 10
_GROUP = 5


def generate_codes(count: int = 10) -> list[str]:
    """生成 count 个互不相同的备用码,形如 A7K2M-9PQR3。"""
    codes: list[str] = []
    seen: set[str] = set()
    while len(codes) < count:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
        if raw in seen:
            continue
        seen.add(raw)
        codes.append(f"{raw[:_GROUP]}-{raw[_GROUP:]}")
    return codes


def normalize(code: str) -> str:
    """去掉分隔符与空格并转大写,便于用户随意输入。"""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def hash_code(code: str) -> str:
    """归一化后的 sha256 十六进制,入库只存这个。"""
    return hashlib.sha256(normalize(code).encode("ascii")).hexdigest()


def consume(code: str, hashes: list[str]) -> list[str] | None:
    """命中则返回去掉该码后的剩余哈希列表;未命中返回 None。"""
    target = hash_code(code)
    for index, stored in enumerate(hashes):
        if hmac.compare_digest(stored, target):
            return hashes[:index] + hashes[index + 1:]
    return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_backup_codes.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add back/dano/auth/backup_codes.py back/tests/test_auth_backup_codes.py
git commit -m "feat(auth): 一次性备用码生成与核销"
```

---

### Task 4: 递增退避限流

**Files:**
- Create: `back/dano/auth/throttle.py`
- Test: `back/tests/test_auth_throttle.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `@dataclass(frozen=True) class ThrottleState: fail_count: int = 0; locked_until: float = 0.0`
  - `CLEARED: ThrottleState` — 零值常量
  - `is_locked(state: ThrottleState, *, now: float) -> bool`
  - `register_failure(state: ThrottleState, *, now: float, max_failures: int = 5, lock_max_minutes: int = 30) -> ThrottleState`

`locked_until` 用 `0.0` 表示未锁定，避免 `None` 在 Pg 往返中的空值分支。

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_auth_throttle.py
from dano.auth.throttle import CLEARED, ThrottleState, is_locked, register_failure

NOW = 1_700_000_000.0


def test_未达阈值不锁定():
    state = CLEARED
    for _ in range(4):
        state = register_failure(state, now=NOW)
    assert state.fail_count == 4
    assert not is_locked(state, now=NOW)


def test_第五次失败锁一分钟():
    state = CLEARED
    for _ in range(5):
        state = register_failure(state, now=NOW)
    assert is_locked(state, now=NOW)
    assert state.locked_until == NOW + 60
    assert not is_locked(state, now=NOW + 61)


def test_退避逐次翻倍():
    state = CLEARED
    for _ in range(5):
        state = register_failure(state, now=NOW)
    assert state.locked_until - NOW == 60
    state = register_failure(state, now=NOW)
    assert state.locked_until - NOW == 120
    state = register_failure(state, now=NOW)
    assert state.locked_until - NOW == 240


def test_锁定时长封顶():
    state = ThrottleState(fail_count=40, locked_until=0.0)
    state = register_failure(state, now=NOW, lock_max_minutes=30)
    assert state.locked_until - NOW == 30 * 60


def test_成功后清零():
    assert CLEARED.fail_count == 0
    assert not is_locked(CLEARED, now=NOW)


def test_阈值可配():
    state = CLEARED
    state = register_failure(state, now=NOW, max_failures=2)
    assert not is_locked(state, now=NOW)
    state = register_failure(state, now=NOW, max_failures=2)
    assert is_locked(state, now=NOW)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_throttle.py -v`
Expected: FAIL，`No module named 'dano.auth.throttle'`

- [ ] **Step 3: 实现**

```python
# back/dano/auth/throttle.py
"""登录失败的递增退避:纯函数 + 显式时钟,便于测试与跨存储复用。

连续失败达到阈值后开始锁定,时长 min(2^(超出次数) 分钟, 上限);登录成功即清零。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThrottleState:
    """某账号当前的失败计数与锁定截止时间(0.0 = 未锁定)。"""

    fail_count: int = 0
    locked_until: float = 0.0


CLEARED = ThrottleState()


def is_locked(state: ThrottleState, *, now: float) -> bool:
    """当前是否处于锁定期。"""
    return state.locked_until > now


def register_failure(state: ThrottleState, *, now: float, max_failures: int = 5,
                     lock_max_minutes: int = 30) -> ThrottleState:
    """记一次失败并算出新的锁定截止时间。"""
    fail_count = state.fail_count + 1
    if fail_count < max_failures:
        return ThrottleState(fail_count=fail_count, locked_until=state.locked_until)
    exponent = min(fail_count - max_failures, 16)   # 封顶前先夹住指数,避免溢出
    minutes = min(2 ** exponent, lock_max_minutes)
    return ThrottleState(fail_count=fail_count, locked_until=now + minutes * 60)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_throttle.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add back/dano/auth/throttle.py back/tests/test_auth_throttle.py
git commit -m "feat(auth): 登录失败递增退避限流"
```

---

### Task 5: QR 编码器

**Files:**
- Create: `back/dano/auth/qrcode.py`
- Modify: `back/pyproject.toml`（`dev` 数组末尾加 `"segno>=1.6"`）
- Test: `back/tests/test_auth_qrcode.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `encode_matrix(text: str) -> list[list[bool]]` — 不含静区的模块矩阵，`True` = 黑
  - `svg_data_uri(text: str, *, module_px: int = 4, quiet_zone: int = 4) -> str` — `data:image/svg+xml;base64,...`

实现要点（byte 模式、EC 级 M、版本 1–10 自动选择）：数据编码 `0100` + 8 bit 长度 + 字节流 + 终止符 + 填充字节 `0xEC/0x11`；Reed-Solomon 用 GF(256) 本原多项式 `0x11D`；按版本的分组规则交织数据块与纠错块；矩阵铺设含定位图形、校正图形、时序图形、暗模块与格式信息；8 种掩码全试，按 QR 规范的四项罚分规则选最低分。测试用 `segno` 对拍即可暴露任何一处偏差。

- [ ] **Step 1: 安装测试用依赖并写失败的测试**

```bash
/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pip install "segno>=1.6"
```

```python
# back/tests/test_auth_qrcode.py
import base64

import pytest

from dano.auth.qrcode import encode_matrix, svg_data_uri

segno = pytest.importorskip("segno")


def _segno_matrix(text: str) -> list[list[bool]]:
    code = segno.make(text, error="m", mode="byte", boost_error=False)
    return [[bool(bit) for bit in row] for row in code.matrix]


@pytest.mark.parametrize("text", [
    "otpauth://totp/Dano:acme?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    "&issuer=Dano&algorithm=SHA1&digits=6&period=30",
    "HELLO WORLD",
    "a" * 100,
])
def test_与segno逐模块一致(text):
    assert encode_matrix(text) == _segno_matrix(text)


def test_svg_data_uri可解码且含尺寸():
    uri = svg_data_uri("otpauth://totp/Dano:acme?secret=ABCDEFGH")
    assert uri.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")
    assert svg.startswith("<svg") and "viewBox" in svg and "</svg>" in svg
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_qrcode.py -v`
Expected: FAIL，`No module named 'dano.auth.qrcode'`

- [ ] **Step 3: 实现 `back/dano/auth/qrcode.py`**

按上面的要点写完整编码器。模块骨架（函数边界固定，实现填充其中）：

```python
"""最小 QR 编码器(byte 模式, EC 级 M, 版本 1-10)→ SVG data URI。

只为 TOTP 绑定二维码服务,不追求完整 QR 规范覆盖。正确性由与 segno 的
逐模块对拍测试保证(见 tests/test_auth_qrcode.py)。
"""

from __future__ import annotations

import base64

_EC_CODEWORDS_M: dict[int, int] = {...}        # 版本 → EC 码字总数
_BLOCKS_M: dict[int, tuple[int, int]] = {...}   # 版本 → (组1块数, 组2块数)
_TOTAL_CODEWORDS: dict[int, int] = {...}        # 版本 → 数据+EC 总码字
_ALIGNMENT: dict[int, list[int]] = {...}        # 版本 → 校正图形中心坐标


def _gf_tables() -> tuple[list[int], list[int]]: ...
def _rs_generator(degree: int) -> list[int]: ...
def _rs_encode(data: bytes, ec_len: int) -> bytes: ...
def _pick_version(payload_len: int) -> int: ...
def _encode_data(text: str, version: int) -> bytes: ...
def _interleave(data: bytes, version: int) -> bytes: ...
def _place_modules(codewords: bytes, version: int) -> tuple[list[list[bool | None]], list[list[bool]]]: ...
def _apply_mask(matrix, reserved, mask_id: int) -> list[list[bool]]: ...
def _penalty(matrix: list[list[bool]]) -> int: ...
def _format_bits(mask_id: int) -> list[bool]: ...


def encode_matrix(text: str) -> list[list[bool]]:
    """编码为模块矩阵(不含静区),True = 黑。"""
    ...


def svg_data_uri(text: str, *, module_px: int = 4, quiet_zone: int = 4) -> str:
    """渲染为 SVG 并包成 data URI,前端用 <img src> 直接显示。"""
    matrix = encode_matrix(text)
    size = len(matrix) + quiet_zone * 2
    rects = "".join(
        f'<rect x="{x + quiet_zone}" y="{y + quiet_zone}" width="1" height="1"/>'
        for y, row in enumerate(matrix) for x, cell in enumerate(row) if cell
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size * module_px}" height="{size * module_px}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<g fill="#000">{rects}</g></svg>'
    )
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"
```

`back/pyproject.toml` 的 dev 数组改为：

```toml
dev = ["aiohttp>=3.10", "pytest>=8.3", "pytest-asyncio>=0.24", "ruff>=0.7", "segno>=1.6"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_qrcode.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add back/dano/auth/qrcode.py back/tests/test_auth_qrcode.py back/pyproject.toml
git commit -m "feat(auth): 纯标准库 QR 编码器(TOTP 绑定二维码)"
```

---

### Task 6: 迁移与租户 TOTP 字段

**Files:**
- Create: `back/migrations/018_tenant_totp.sql`, `back/migrations/019_auth_state.sql`
- Modify: `back/dano/registry/models.py`（`TenantRecord`）、`back/dano/registry/store.py`（两套 registry）
- Test: `back/tests/test_registry_totp.py`

**Interfaces:**
- Consumes: 无
- Produces：`TenantRecord` 新增 `totp_secret: str = ""`、`totp_pending: str = ""`、`backup_codes: list[str] = []`；两套 registry 新增
  - `async def set_totp_pending(self, tenant: str, secret: str) -> None`
  - `async def activate_totp(self, tenant: str, secret: str, backup_hashes: list[str]) -> None`
  - `async def disable_totp(self, tenant: str) -> None`
  - `async def set_backup_codes(self, tenant: str, backup_hashes: list[str]) -> None`

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_registry_totp.py
from dano.registry.models import TenantRecord
from dano.registry.store import InMemoryRegistry


async def test_默认未绑定():
    reg = InMemoryRegistry()
    rec = await reg.create_tenant(TenantRecord(tenant="acme", username="acme"))
    assert rec.totp_secret == "" and rec.totp_pending == "" and rec.backup_codes == []


async def test_pending到激活再解绑():
    reg = InMemoryRegistry()
    await reg.create_tenant(TenantRecord(tenant="acme", username="acme"))

    await reg.set_totp_pending("acme", "SECRET1")
    rec = await reg.get_tenant_by_username("acme")
    assert rec.totp_pending == "SECRET1" and rec.totp_secret == ""

    await reg.activate_totp("acme", "SECRET1", ["h1", "h2"])
    rec = await reg.get_tenant_by_username("acme")
    assert rec.totp_secret == "SECRET1" and rec.totp_pending == ""
    assert rec.backup_codes == ["h1", "h2"]

    await reg.set_backup_codes("acme", ["h3"])
    assert (await reg.get_tenant_by_username("acme")).backup_codes == ["h3"]

    await reg.disable_totp("acme")
    rec = await reg.get_tenant_by_username("acme")
    assert rec.totp_secret == "" and rec.backup_codes == []


async def test_旧行的null字段归一化为空():
    rec = TenantRecord(tenant="acme", totp_secret=None, totp_pending=None, backup_codes=None)
    assert rec.totp_secret == "" and rec.totp_pending == "" and rec.backup_codes == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_registry_totp.py -v`
Expected: FAIL，`AttributeError: 'InMemoryRegistry' object has no attribute 'set_totp_pending'`

- [ ] **Step 3: 实现**

`back/migrations/018_tenant_totp.sql`：

```sql
-- 租户后台登录的 TOTP 二因子:密钥与一次性备用码。
-- totp_secret 空 = 未绑定;totp_pending 是绑定流程中尚未验证的密钥;
-- backup_codes 存 sha256 哈希,用一个核销一个。明文永不入库。
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS totp_pending TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS backup_codes TEXT[];
```

`back/migrations/019_auth_state.sql`：

```sql
-- 登录的瞬态状态:失败计数与两步登录 challenge。
-- 两张表可随时清空,不影响任何业务数据(代价仅是限流计数归零)。
CREATE TABLE IF NOT EXISTS auth_failures (
  username       TEXT PRIMARY KEY,
  fail_count     INT NOT NULL DEFAULT 0,
  locked_until   DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_totp_step BIGINT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_challenges (
  challenge_hash TEXT PRIMARY KEY,
  tenant         TEXT NOT NULL,
  username       TEXT NOT NULL,
  totp_failures  INT NOT NULL DEFAULT 0,
  expires_at     DOUBLE PRECISION NOT NULL,
  consumed       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_username ON auth_challenges (username);
```

`back/dano/registry/models.py` 的 `TenantRecord` 增加字段与归一化校验器（沿用现有 `normalize_legacy_null_password_hash` 的写法）：

```python
    totp_secret: str = ""
    totp_pending: str = ""
    backup_codes: list[str] = Field(default_factory=list)

    @field_validator("password_hash", "totp_secret", "totp_pending", mode="before")
    @classmethod
    def normalize_legacy_null_text(cls, value: str | None) -> str:
        return value or ""

    @field_validator("backup_codes", mode="before")
    @classmethod
    def normalize_legacy_null_codes(cls, value: list[str] | None) -> list[str]:
        return list(value or [])
```

（删除原来的 `normalize_legacy_null_password_hash`，其职责已并入 `normalize_legacy_null_text`。）

`back/dano/registry/store.py` 两套 registry 各加四个方法。`InMemoryRegistry` 用 `model_copy(update=...)`，与既有 `update_tenant_password` 同款：

```python
    async def set_totp_pending(self, tenant: str, secret: str) -> None:
        rec = self._tenants.get(tenant)
        if rec is not None:
            self._tenants[tenant] = rec.model_copy(update={"totp_pending": secret})

    async def activate_totp(self, tenant: str, secret: str, backup_hashes: list[str]) -> None:
        rec = self._tenants.get(tenant)
        if rec is not None:
            self._tenants[tenant] = rec.model_copy(
                update={"totp_secret": secret, "totp_pending": "",
                        "backup_codes": list(backup_hashes)})

    async def disable_totp(self, tenant: str) -> None:
        rec = self._tenants.get(tenant)
        if rec is not None:
            self._tenants[tenant] = rec.model_copy(
                update={"totp_secret": "", "totp_pending": "", "backup_codes": []})

    async def set_backup_codes(self, tenant: str, backup_hashes: list[str]) -> None:
        rec = self._tenants.get(tenant)
        if rec is not None:
            self._tenants[tenant] = rec.model_copy(update={"backup_codes": list(backup_hashes)})
```

`PgRegistry` 对应四条 UPDATE，写法参照既有 `update_tenant_password`：

```python
    async def set_totp_pending(self, tenant: str, secret: str) -> None:
        from dano.infra.db import get_pool
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE tenants SET totp_pending=$2 WHERE tenant=$1", tenant, secret)

    async def activate_totp(self, tenant: str, secret: str, backup_hashes: list[str]) -> None:
        from dano.infra.db import get_pool
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET totp_secret=$2, totp_pending='', backup_codes=$3 "
                "WHERE tenant=$1", tenant, secret, list(backup_hashes))

    async def disable_totp(self, tenant: str) -> None:
        from dano.infra.db import get_pool
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenants SET totp_secret='', totp_pending='', backup_codes='{}' "
                "WHERE tenant=$1", tenant)

    async def set_backup_codes(self, tenant: str, backup_hashes: list[str]) -> None:
        from dano.infra.db import get_pool
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE tenants SET backup_codes=$2 WHERE tenant=$1",
                               tenant, list(backup_hashes))
```

注意 `PgRegistry.create_tenant` 的 INSERT 语句不需要改——新列都有默认空值，`RETURNING *` 会把它们带回来，`TenantRecord` 的校验器负责把 NULL 归一化。

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_registry_totp.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add back/migrations/018_tenant_totp.sql back/migrations/019_auth_state.sql \
        back/dano/registry/models.py back/dano/registry/store.py back/tests/test_registry_totp.py
git commit -m "feat(auth): 租户 TOTP 字段与认证状态表迁移"
```

---

### Task 7: 认证状态存储与 challenge

**Files:**
- Create: `back/dano/auth/store.py`, `back/dano/auth/challenge.py`
- Test: `back/tests/test_auth_store.py`

**Interfaces:**
- Consumes: `ThrottleState` / `CLEARED`（Task 4）
- Produces:
  - `challenge.new_challenge() -> tuple[str, str]` — `(明文, sha256 十六进制)`
  - `challenge.hash_challenge(token: str) -> str`
  - `@dataclass class ChallengeRecord: tenant: str; username: str; totp_failures: int; expires_at: float; consumed: bool`
  - `InMemoryAuthStore` / `PgAuthStore`，同一组方法：
    - `async get_throttle(username: str) -> ThrottleState`
    - `async set_throttle(username: str, state: ThrottleState) -> None`
    - `async get_last_totp_step(username: str) -> int | None`
    - `async set_last_totp_step(username: str, step: int) -> None`
    - `async create_challenge(challenge_hash: str, tenant: str, username: str, expires_at: float) -> None`
    - `async get_challenge(challenge_hash: str) -> ChallengeRecord | None`
    - `async bump_challenge_failure(challenge_hash: str) -> int`
    - `async consume_challenge(challenge_hash: str) -> bool`
    - `async drop_challenges_for(username: str) -> None`

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_auth_store.py
import os

import pytest

from dano.auth.challenge import hash_challenge, new_challenge
from dano.auth.store import InMemoryAuthStore, PgAuthStore
from dano.auth.throttle import CLEARED, ThrottleState

NOW = 1_700_000_000.0


async def _make_store(kind: str):
    if kind == "memory":
        return InMemoryAuthStore()
    if not os.environ.get("DANO_PG_DSN"):
        pytest.skip("未配置 DANO_PG_DSN,跳过 Pg 契约测试")
    from dano.infra.db import init_pool, run_migrations
    await init_pool()
    await run_migrations()
    return PgAuthStore()


@pytest.fixture(params=["memory", "pg"])
async def store(request):
    return await _make_store(request.param)


def test_challenge明文与哈希对应():
    token, digest = new_challenge()
    assert len(token) > 30
    assert digest == hash_challenge(token)
    assert token != digest


async def test_限流状态往返(store):
    assert await store.get_throttle("acme") == CLEARED
    await store.set_throttle("acme", ThrottleState(fail_count=3, locked_until=NOW + 60))
    state = await store.get_throttle("acme")
    assert state.fail_count == 3 and state.locked_until == NOW + 60


async def test_totp游标往返(store):
    assert await store.get_last_totp_step("acme") is None
    await store.set_last_totp_step("acme", 12345)
    assert await store.get_last_totp_step("acme") == 12345


async def test_challenge生命周期(store):
    _, digest = new_challenge()
    await store.create_challenge(digest, "acme", "acme", NOW + 300)
    rec = await store.get_challenge(digest)
    assert rec.tenant == "acme" and rec.totp_failures == 0 and not rec.consumed

    assert await store.bump_challenge_failure(digest) == 1
    assert await store.bump_challenge_failure(digest) == 2

    assert await store.consume_challenge(digest) is True
    assert await store.consume_challenge(digest) is False
    assert (await store.get_challenge(digest)).consumed is True


async def test_按用户名清空challenge(store):
    _, first = new_challenge()
    _, second = new_challenge()
    await store.create_challenge(first, "acme", "acme", NOW + 300)
    await store.create_challenge(second, "acme", "acme", NOW + 300)
    await store.drop_challenges_for("acme")
    assert await store.get_challenge(first) is None
    assert await store.get_challenge(second) is None


async def test_未知challenge返回None(store):
    assert await store.get_challenge("不存在") is None
    assert await store.consume_challenge("不存在") is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_store.py -v`
Expected: FAIL，`No module named 'dano.auth.store'`

- [ ] **Step 3: 实现**

```python
# back/dano/auth/challenge.py
"""两步登录的 challenge 令牌:明文只出现在响应与前端内存,库里只存 sha256。"""

from __future__ import annotations

import hashlib
import secrets


def new_challenge() -> tuple[str, str]:
    """生成 (明文令牌, sha256 十六进制)。"""
    token = secrets.token_urlsafe(32)
    return token, hash_challenge(token)


def hash_challenge(token: str) -> str:
    """令牌的 sha256 十六进制,作为存储主键。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()
```

`back/dano/auth/store.py` 写两个类。`InMemoryAuthStore` 用两个 dict；`PgAuthStore` 对 `auth_failures` 用 `INSERT ... ON CONFLICT (username) DO UPDATE`，对 `auth_challenges` 用普通 UPDATE / DELETE。`bump_challenge_failure` 用 `UPDATE ... SET totp_failures = totp_failures + 1 RETURNING totp_failures` 保证并发下不丢计数；`consume_challenge` 用 `UPDATE ... SET consumed = TRUE WHERE challenge_hash=$1 AND consumed = FALSE RETURNING challenge_hash`，返回是否命中，天然做到"用一次即焚"。`get_throttle` 查不到行时返回 `CLEARED`。

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_store.py -v`
Expected: memory 参数全部 passed；无 `DANO_PG_DSN` 时 pg 参数 skipped

- [ ] **Step 5: 提交**

```bash
git add back/dano/auth/store.py back/dano/auth/challenge.py back/tests/test_auth_store.py
git commit -m "feat(auth): 认证瞬态状态双实现与 challenge 令牌"
```

---

### Task 8: 认证服务编排

**Files:**
- Create: `back/dano/auth/service.py`
- Test: `back/tests/test_auth_service.py`

**Interfaces:**
- Consumes: Task 1–7 的全部模块 + `InMemoryRegistry`
- Produces:
  - `class AuthError(Exception)`，带 `status: int` 与 `detail: str`
  - `@dataclass class LoginResult: need_totp: bool = False; tenant: str = ""; api_key: str = ""; challenge: str = ""; expires_in: int = 0`
  - `class AuthService(registry, store, settings)`，方法：
    - `async login(username: str, password: str, *, now: float | None = None) -> LoginResult`
    - `async verify_totp_login(challenge: str, code: str, *, now: float | None = None) -> LoginResult`
    - `async change_password(api_key: str, old_password: str, new_password: str, code: str = "", *, now: float | None = None) -> None`
    - `async totp_setup(api_key: str) -> dict` — `{"secret", "uri", "qr_svg_data_uri"}`
    - `async totp_activate(api_key: str, code: str, *, now: float | None = None) -> list[str]`
    - `async totp_disable(api_key: str, password: str, code: str, *, now: float | None = None) -> None`
    - `async regenerate_backup_codes(api_key: str, password: str, code: str, *, now: float | None = None) -> list[str]`

统一错误文案常量 `_CREDENTIAL_ERROR = "用户名或密码错误；连续失败会临时锁定账号"`，登录路径上任何失败都用它，避免泄露账号是否存在。

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_auth_service.py
import pytest

from dano.auth.service import AuthError, AuthService
from dano.auth.store import InMemoryAuthStore
from dano.auth.totp import totp_code
from dano.config import Settings
from dano.infra.passwords import hash_password
from dano.registry.models import TenantRecord
from dano.registry.store import InMemoryRegistry

NOW = 1_700_000_000.0
PASSWORD = "correct-horse-battery"


async def _service() -> AuthService:
    reg = InMemoryRegistry()
    await reg.create_tenant(TenantRecord(
        tenant="acme", username="acme", password_hash=hash_password(PASSWORD)))
    return AuthService(registry=reg, store=InMemoryAuthStore(), settings=Settings())


async def test_未绑定totp单步登录():
    svc = await _service()
    result = await svc.login("acme", PASSWORD, now=NOW)
    assert result.need_totp is False
    assert result.tenant == "acme" and result.api_key.startswith("dk_")


async def test_密码错误统一文案():
    svc = await _service()
    with pytest.raises(AuthError) as exc:
        await svc.login("acme", "wrong-password-here", now=NOW)
    assert exc.value.status == 401
    assert "用户名或密码错误" in exc.value.detail


async def test_用户不存在与密码错误文案一致():
    svc = await _service()
    with pytest.raises(AuthError) as missing:
        await svc.login("nobody", PASSWORD, now=NOW)
    with pytest.raises(AuthError) as wrong:
        await svc.login("acme", "wrong-password-here", now=NOW)
    assert missing.value.detail == wrong.value.detail


async def test_连续失败后锁定且不再校验密码():
    svc = await _service()
    for _ in range(5):
        with pytest.raises(AuthError):
            await svc.login("acme", "wrong-password-here", now=NOW)
    with pytest.raises(AuthError):
        await svc.login("acme", PASSWORD, now=NOW)          # 密码正确也被拒
    result = await svc.login("acme", PASSWORD, now=NOW + 61)  # 锁定期过后放行
    assert result.api_key.startswith("dk_")


async def test_成功登录清零失败计数():
    svc = await _service()
    for _ in range(3):
        with pytest.raises(AuthError):
            await svc.login("acme", "wrong-password-here", now=NOW)
    await svc.login("acme", PASSWORD, now=NOW)
    for _ in range(4):
        with pytest.raises(AuthError):
            await svc.login("acme", "wrong-password-here", now=NOW)
    await svc.login("acme", PASSWORD, now=NOW)   # 仍未触发锁定


async def test_绑定后走两步登录():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key

    setup = await svc.totp_setup(key)
    secret = setup["secret"]
    assert setup["qr_svg_data_uri"].startswith("data:image/svg+xml;base64,")
    assert setup["uri"].startswith("otpauth://totp/")

    codes = await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)
    assert len(codes) == 10

    first = await svc.login("acme", PASSWORD, now=NOW + 60)
    assert first.need_totp is True and first.challenge and not first.api_key

    second = await svc.verify_totp_login(
        first.challenge, totp_code(secret, now=NOW + 60), now=NOW + 60)
    assert second.api_key == key


async def test_备用码可登录且一次性():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    codes = await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)

    first = await svc.login("acme", PASSWORD, now=NOW + 60)
    assert (await svc.verify_totp_login(first.challenge, codes[0], now=NOW + 60)).api_key == key

    second = await svc.login("acme", PASSWORD, now=NOW + 120)
    with pytest.raises(AuthError):
        await svc.verify_totp_login(second.challenge, codes[0], now=NOW + 120)


async def test_验证码连续错误作废challenge():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)

    first = await svc.login("acme", PASSWORD, now=NOW + 60)
    for _ in range(5):
        with pytest.raises(AuthError):
            await svc.verify_totp_login(first.challenge, "000000", now=NOW + 60)
    with pytest.raises(AuthError):   # 即便这次给对码,challenge 已废
        await svc.verify_totp_login(
            first.challenge, totp_code(secret, now=NOW + 60), now=NOW + 60)


async def test_challenge过期被拒():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)
    first = await svc.login("acme", PASSWORD, now=NOW + 60)
    with pytest.raises(AuthError):
        await svc.verify_totp_login(
            first.challenge, totp_code(secret, now=NOW + 400), now=NOW + 400)


async def test_改密走密码策略():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    with pytest.raises(AuthError, match="至少 12 位"):
        await svc.change_password(key, PASSWORD, "short12", now=NOW)
    await svc.change_password(key, PASSWORD, "another-good-password", now=NOW)
    assert (await svc.login("acme", "another-good-password", now=NOW)).api_key == key


async def test_已绑定totp时改密必须带验证码():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)
    with pytest.raises(AuthError, match="验证码"):
        await svc.change_password(key, PASSWORD, "another-good-password", now=NOW + 60)
    await svc.change_password(key, PASSWORD, "another-good-password",
                              totp_code(secret, now=NOW + 60), now=NOW + 60)


async def test_解绑需要密码与验证码():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)
    with pytest.raises(AuthError):
        await svc.totp_disable(key, "wrong-password-here",
                               totp_code(secret, now=NOW + 60), now=NOW + 60)
    await svc.totp_disable(key, PASSWORD, totp_code(secret, now=NOW + 90), now=NOW + 90)
    assert (await svc.login("acme", PASSWORD, now=NOW + 120)).need_totp is False


async def test_重新生成备用码作废旧码():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    old = await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)
    new = await svc.regenerate_backup_codes(
        key, PASSWORD, totp_code(secret, now=NOW + 60), now=NOW + 60)
    assert len(new) == 10 and set(new) != set(old)

    first = await svc.login("acme", PASSWORD, now=NOW + 120)
    with pytest.raises(AuthError):
        await svc.verify_totp_login(first.challenge, old[0], now=NOW + 120)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_service.py -v`
Expected: FAIL，`No module named 'dano.auth.service'`

- [ ] **Step 3: 实现 `back/dano/auth/service.py`**

按 spec「登录流程」一节实现。关键实现约束：

- `login` 顺序：`store.get_throttle` → `is_locked` 则**直接抛 401，不碰 scrypt** → 查租户 → 租户不存在或 `password_hash` 为空则对模块级常量 `_DUMMY_HASH`（模块导入时用 `hash_password(secrets.token_hex(16))` 生成一次）跑一次 `verify_password` 再抛 401 → 密码错则 `register_failure` 落库后抛 401 → 密码对且未绑定 TOTP 则 `set_throttle(CLEARED)` 并返回 `api_key` → 已绑定则 `set_throttle(CLEARED)`、`new_challenge()`、`create_challenge` 后返回 `need_totp`
- `verify_totp_login`：查 challenge → 不存在/已消费/`expires_at <= now` 抛 401 → 先按长度归一化判断是 TOTP 还是备用码 → TOTP 走 `verify_code(..., last_step=await store.get_last_totp_step(username))`，命中后 `set_last_totp_step`；备用码走 `consume` 并 `registry.set_backup_codes` 写回剩余 → 失败则 `bump_challenge_failure`，返回值 `>= settings.auth_max_failures` 时 `consume_challenge` 作废并 `register_failure` 锁账号 → 成功则 `consume_challenge` 并返回 api_key
- 需要密码+验证码的三个管理方法共用私有 `async def _require_password_and_code(rec, password, code, now)`，密码错或验证码错一律抛 401
- `PasswordPolicyError` 捕获后转成 `AuthError(400, str(e))`
- `totp_setup` 在 `rec.totp_secret` 非空时抛 `AuthError(409, "已绑定两步验证，请先解绑")`
- `totp_disable` 在未绑定时抛 `AuthError(409, "尚未绑定两步验证")`
- 改密成功后调用 `store.set_throttle(username, CLEARED)` 与 `store.drop_challenges_for(username)`

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_service.py -v`
Expected: 14 passed

- [ ] **Step 5: 提交**

```bash
git add back/dano/auth/service.py back/tests/test_auth_service.py
git commit -m "feat(auth): 认证服务编排(两步登录/改密/TOTP 管理)"
```

---

### Task 9: 网关路由

**Files:**
- Modify: `back/dano/gateway/app.py`
- Test: `back/tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `AuthService` / `AuthError`（Task 8）、`InMemoryAuthStore` / `PgAuthStore`（Task 7）、`validate_password`（Task 1）
- Produces: HTTP 契约——`POST /auth/login`、`POST /auth/login/totp`、`POST /auth/change-password`、`POST /auth/totp/setup`、`POST /auth/totp/activate`、`POST /auth/totp/disable`、`POST /auth/totp/backup-codes`

- [ ] **Step 1: 写失败的测试**

```python
# back/tests/test_auth_routes.py
import pytest
from fastapi.testclient import TestClient

from dano.auth.totp import totp_code
from dano.gateway.app import app

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_tenant(client, tenant="acme"):
    resp = client.post("/tenants", json={
        "tenant": tenant, "username": tenant, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()["api_key"]


def test_建租户拒绝弱密码(client):
    resp = client.post("/tenants", json={
        "tenant": "weak", "username": "weak", "password": "short"})
    assert resp.status_code == 400
    assert "至少 12 位" in resp.json()["detail"]


def test_未绑定时单步登录(client):
    key = _make_tenant(client, "solo")
    resp = client.post("/auth/login", json={"username": "solo", "password": PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["api_key"] == key


def test_密码错误返回401(client):
    _make_tenant(client, "badpass")
    resp = client.post("/auth/login", json={
        "username": "badpass", "password": "definitely-wrong-here"})
    assert resp.status_code == 401
    assert "用户名或密码错误" in resp.json()["detail"]


def test_两步登录完整链路(client):
    key = _make_tenant(client, "twofa")

    setup = client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).json()
    secret = setup["secret"]
    assert setup["qr_svg_data_uri"].startswith("data:image/svg+xml;base64,")

    activate = client.post("/auth/totp/activate", headers={"X-Tenant-Key": key},
                           json={"code": totp_code(secret)})
    assert activate.status_code == 200
    assert len(activate.json()["backup_codes"]) == 10

    first = client.post("/auth/login", json={"username": "twofa", "password": PASSWORD}).json()
    assert first["need_totp"] is True and "api_key" not in first

    second = client.post("/auth/login/totp", json={
        "challenge": first["challenge"], "code": totp_code(secret)})
    assert second.status_code == 200
    assert second.json()["api_key"] == key


def test_绑定后改密必须带验证码(client):
    key = _make_tenant(client, "chpw")
    secret = client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).json()["secret"]
    client.post("/auth/totp/activate", headers={"X-Tenant-Key": key},
                json={"code": totp_code(secret)})

    resp = client.post("/auth/change-password", headers={"X-Tenant-Key": key},
                       json={"old_password": PASSWORD, "new_password": "another-good-password"})
    assert resp.status_code == 401

    resp = client.post("/auth/change-password", headers={"X-Tenant-Key": key},
                       json={"old_password": PASSWORD, "new_password": "another-good-password",
                             "code": totp_code(secret)})
    assert resp.status_code == 200


def test_重复setup返回409(client):
    key = _make_tenant(client, "dup")
    secret = client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).json()["secret"]
    client.post("/auth/totp/activate", headers={"X-Tenant-Key": key},
                json={"code": totp_code(secret)})
    assert client.post("/auth/totp/setup", headers={"X-Tenant-Key": key}).status_code == 409


def test_totp接口需要鉴权(client):
    assert client.post("/auth/totp/setup").status_code == 401
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/test_auth_routes.py -v`
Expected: FAIL（`/auth/totp/setup` 404、建租户弱密码返回 200）

- [ ] **Step 3: 实现**

在 `app.py` 顶部加 `_auth_store` 全局并在 lifespan 里装配——DB 就绪时 `PgAuthStore()`，否则 `InMemoryAuthStore()`：

```python
_auth_store = InMemoryAuthStore()      # 模块级默认,DB 就绪时在 lifespan 换成 PgAuthStore
```

lifespan 的 `_registry = PgRegistry()` 之后加一行 `_auth_store = PgAuthStore()`，并把 `_auth_store` 加进该函数的 `global` 声明。

加一个服务工厂（每次请求现取，保证拿到 lifespan 替换后的实例）：

```python
def _auth_service() -> AuthService:
    return AuthService(registry=_registry, store=_auth_store, settings=get_settings())
```

统一的异常映射：

```python
def _raise_http(err: AuthError) -> None:
    raise HTTPException(status_code=err.status, detail=err.detail) from err
```

替换现有 `/auth/login`、`/auth/change-password` 的实现体为对 `AuthService` 的调用；新增 `/auth/login/totp` 与四个 `/auth/totp/*` 路由。请求模型：

```python
class LoginRequest(BaseModel):
    username: str
    password: str


class TotpLoginRequest(BaseModel):
    challenge: str
    code: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
    code: str = ""


class TotpCodeRequest(BaseModel):
    code: str


class TotpConfirmRequest(BaseModel):
    password: str
    code: str
```

`/auth/login` 的响应按 `LoginResult.need_totp` 分支：未绑定返回 `{"tenant", "api_key"}`，已绑定返回 `{"need_totp": True, "challenge", "expires_in"}`。

`POST /tenants` 在 `hash_password(password)` 之前插入策略校验：

```python
    if username and password:
        try:
            validate_password(password, username=username, tenant=req.tenant,
                              min_length=get_settings().auth_min_password_length)
        except PasswordPolicyError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        payload["username"] = username
        payload["password_hash"] = hash_password(password)
```

- [ ] **Step 4: 运行全部后端测试**

Run: `/Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/ -v`
Expected: 全部 passed（Pg 相关 skipped）

- [ ] **Step 5: 提交**

```bash
git add back/dano/gateway/app.py back/tests/test_auth_routes.py
git commit -m "feat(auth): 网关登录路由改造与 TOTP 管理接口"
```

---

### Task 10: 前端两步登录与绑定面板

**Files:**
- Modify: `skillfrontend/src/api/skills.ts`, `skillfrontend/src/pages/Tenant.tsx`, `skillfrontend/src/pages/RegisterTenant.tsx`

**Interfaces:**
- Consumes: Task 9 的 HTTP 契约
- Produces: 无（终端任务）

- [ ] **Step 1: 改 `api/skills.ts`**

```ts
export type LoginResult =
  | { need_totp?: false; tenant: string; api_key: string }
  | { need_totp: true; challenge: string; expires_in: number };

export async function login(username: string, password: string): Promise<LoginResult> {
  const { data } = await api.post("/auth/login", { username, password });
  return data;
}

export async function loginTotp(
  challenge: string,
  code: string,
): Promise<{ tenant: string; api_key: string }> {
  const { data } = await api.post("/auth/login/totp", { challenge, code });
  return data;
}

export async function totpSetup(): Promise<{
  secret: string;
  uri: string;
  qr_svg_data_uri: string;
}> {
  const { data } = await api.post("/auth/totp/setup");
  return data;
}

export async function totpActivate(code: string): Promise<string[]> {
  const { data } = await api.post("/auth/totp/activate", { code });
  return data.backup_codes;
}

export async function totpDisable(password: string, code: string): Promise<void> {
  await api.post("/auth/totp/disable", { password, code });
}

export async function regenerateBackupCodes(password: string, code: string): Promise<string[]> {
  const { data } = await api.post("/auth/totp/backup-codes", { password, code });
  return data.backup_codes;
}
```

`changePassword` 增加第三个可选参数 `code = ""`，随 body 一起发送。

- [ ] **Step 2: 改 `pages/Tenant.tsx`**

登录卡片加 `challenge` 状态：`onLogin` 拿到 `need_totp` 时存下 `challenge` 并切到验证码面板；验证码面板提交走 `loginTotp`，成功后 `setTenant` 并跳转。面板下方给一个「改用备用码」链接，只是把输入框的 `maxLength` 从 6 放宽到 11、占位符换成 `XXXXX-XXXXX`。

已登录区在「修改密码」卡片之后新增「两步验证」卡片：

- 未绑定：按钮「开启两步验证」→ 调 `totpSetup` → 显示 `<img src={qr_svg_data_uri} width={180} />` 与 Base32 密钥文本 → 输入 6 位码 → `totpActivate` → 用 `Modal` 展示 10 个备用码并提示只显示一次
- 已绑定：显示「已开启」状态，两个按钮——「重新生成备用码」与「解绑」，各自弹出需要密码 + 验证码的表单

绑定状态用一个本地 `boolean` 维护：页面加载时不额外请求，`totpActivate` 成功后置 `true`，`totpDisable` 成功后置 `false`；首次进入按未绑定展示，若用户点「开启」时后端返回 409，则据此把状态改为已绑定并提示。

「修改密码」表单增加可选的验证码字段，提示文案「已开启两步验证时必填」。

- [ ] **Step 3: 改 `pages/RegisterTenant.tsx`**

密码校验规则 `min` 由 8 改为 12，提示文案改为「初始密码（至少 12 位）」。

- [ ] **Step 4: 构建并手工验证**

```bash
cd skillfrontend && npm run build
```

Expected: 构建通过、无 TypeScript 错误。随后启动前后端，在浏览器走一遍：新建租户 → 开启两步验证 → 扫码 → 激活拿到备用码 → 退出 → 用密码 + 验证码登录 → 再用备用码登录一次。

- [ ] **Step 5: 提交**

```bash
git add skillfrontend/src/api/skills.ts skillfrontend/src/pages/Tenant.tsx skillfrontend/src/pages/RegisterTenant.tsx
git commit -m "feat(auth): 前端两步登录与两步验证管理面板"
```

---

## 验收清单

全部任务完成后逐条确认：

- [ ] `cd back && /Users/wei.quan/.conda/envs/ai-agents/bin/python -m pytest tests/ -v` 全绿
- [ ] `cd back && ruff check .` 无错
- [ ] `cd skillfrontend && npm run build` 通过
- [ ] 现有租户（`totp_secret` 为空）登录行为与改造前一致
- [ ] 浏览器手工走通：绑定 → 两步登录 → 备用码登录 → 解绑
