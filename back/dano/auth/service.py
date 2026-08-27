"""后台登录的流程编排:密码登录、TOTP 两步校验、改密、两步验证绑定与解绑。

网关只负责把 HTTP 请求翻成这里的调用、把 AuthError 翻回状态码;
所有判定都在这里,便于脱离 FastAPI 单独测试。

安全约定(与 docs/superpowers/specs/2026-08-25-knowledge-factor-auth-design.md 一致):
- 登录路径上任何失败都返回同一句文案,不泄露账号是否存在;
- 处于锁定期时**不执行 scrypt**,否则等于给攻击者一个 CPU/内存放大器;
- 用户不存在时对哑元哈希跑一次校验,抹平时序差异;
- 密码、密钥、备用码明文、challenge 明文一律不进日志。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from dano.auth.backup_codes import consume as consume_backup_code
from dano.auth.backup_codes import generate_codes, hash_code, normalize
from dano.auth.challenge import hash_challenge, new_challenge
from dano.auth.policy import PasswordPolicyError, validate_password
from dano.auth.qrcode import svg_data_uri
from dano.auth.throttle import CLEARED, is_locked, register_failure
from dano.auth.totp import generate_secret, provisioning_uri, verify_code
from dano.infra.passwords import hash_password, verify_password

# 登录路径上的统一文案:不区分"用户不存在""密码错""已锁定",避免账号枚举。
# 同时把"会被锁定"这件事讲清楚,免得被锁的管理员一头雾水。
_CREDENTIAL_ERROR = "用户名或密码错误;连续失败会临时锁定账号"
_TOTP_ERROR = "验证码无效或已失效,请重新登录"

# 用户不存在时拿它跑一次 scrypt,把耗时拉平到与真实校验一致
_DUMMY_HASH = hash_password(secrets.token_hex(16))

_TOTP_DIGITS = 6


class AuthError(Exception):
    """带 HTTP 状态码的认证失败;detail 直接作为面向用户的提示。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class LoginResult:
    """登录结果:要么直接给 api_key,要么要求补第二步验证码。"""

    need_totp: bool = False
    tenant: str = ""
    api_key: str = ""
    challenge: str = ""
    expires_in: int = 0


class AuthService:
    def __init__(self, *, registry, store, settings) -> None:
        self._registry = registry
        self._store = store
        self._settings = settings

    # ── 登录 ──────────────────────────────────────────────────────────
    async def login(self, username: str, password: str, *,
                    now: float | None = None) -> LoginResult:
        moment = time.time() if now is None else now
        name = (username or "").strip()
        key = name.casefold()

        state = await self._store.get_throttle(key)
        if is_locked(state, now=moment):
            # 锁定期直接短路:不跑 scrypt,不给攻击者放大 CPU 的机会
            raise AuthError(401, _CREDENTIAL_ERROR)

        rec = await self._registry.get_tenant_by_username(name)
        if rec is None or not rec.password_hash:
            verify_password(password, _DUMMY_HASH)      # 抹平时序
            await self._register_failure(key, state, moment)
            raise AuthError(401, _CREDENTIAL_ERROR)

        if not verify_password(password, rec.password_hash):
            await self._register_failure(key, state, moment)
            raise AuthError(401, _CREDENTIAL_ERROR)

        await self._store.set_throttle(key, CLEARED)
        if not rec.totp_secret:
            return LoginResult(tenant=rec.tenant, api_key=rec.api_key)

        token, digest = new_challenge()
        ttl = self._settings.auth_challenge_ttl_seconds
        # 存原始用户名:第二步要靠它查租户(租户查找大小写敏感);限流键另用 casefold
        await self._store.create_challenge(digest, rec.tenant, rec.username, moment + ttl)
        return LoginResult(need_totp=True, tenant=rec.tenant, challenge=token, expires_in=ttl)

    async def verify_totp_login(self, challenge: str, code: str, *,
                                now: float | None = None) -> LoginResult:
        moment = time.time() if now is None else now
        digest = hash_challenge(challenge)
        record = await self._store.get_challenge(digest)
        if record is None or record.consumed or record.expires_at <= moment:
            raise AuthError(401, _TOTP_ERROR)

        rec = await self._registry.get_tenant_by_username(record.username)
        if rec is None or not rec.totp_secret:
            raise AuthError(401, _TOTP_ERROR)

        key = (record.username or "").casefold()
        if not await self._consume_second_factor(rec, code, key, moment):
            failures = await self._store.bump_challenge_failure(digest)
            if failures >= self._settings.auth_max_failures:
                await self._store.consume_challenge(digest)     # 作废,必须从密码步重来
                state = await self._store.get_throttle(key)
                await self._register_failure(key, state, moment)
            raise AuthError(401, _TOTP_ERROR)

        if not await self._store.consume_challenge(digest):
            raise AuthError(401, _TOTP_ERROR)
        await self._store.set_throttle(key, CLEARED)
        return LoginResult(tenant=rec.tenant, api_key=rec.api_key)

    # ── 改密 ──────────────────────────────────────────────────────────
    async def change_password(self, api_key: str, old_password: str, new_password: str,
                              code: str = "", *, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        rec = await self._require_tenant(api_key)
        if not rec.password_hash:
            raise AuthError(403, "该租户未启用密码登录")
        if not verify_password(old_password, rec.password_hash):
            raise AuthError(401, "原密码错误")
        # api_key 是长期凭证:只凭它就能改密的话,二因子等于形同虚设
        await self._require_second_factor(rec, code, moment)

        candidate = (new_password or "").strip()
        try:
            validate_password(candidate, username=rec.username, tenant=rec.tenant,
                              min_length=self._settings.auth_min_password_length)
        except PasswordPolicyError as e:
            raise AuthError(400, str(e)) from e

        await self._registry.update_tenant_password(rec.tenant, hash_password(candidate))
        await self._store.set_throttle((rec.username or "").casefold(), CLEARED)
        await self._store.drop_challenges_for(rec.username)

    # ── 两步验证管理 ──────────────────────────────────────────────────
    async def totp_setup(self, api_key: str) -> dict:
        rec = await self._require_tenant(api_key)
        if rec.totp_secret:
            raise AuthError(409, "已绑定两步验证,请先解绑")
        secret = generate_secret()
        await self._registry.set_totp_pending(rec.tenant, secret)
        uri = provisioning_uri(secret, account=rec.username or rec.tenant)
        return {"secret": secret, "uri": uri, "qr_svg_data_uri": svg_data_uri(uri)}

    async def totp_activate(self, api_key: str, code: str, *,
                            now: float | None = None) -> list[str]:
        moment = time.time() if now is None else now
        rec = await self._require_tenant(api_key)
        if rec.totp_secret:
            raise AuthError(409, "已绑定两步验证,请先解绑")
        if not rec.totp_pending:
            raise AuthError(409, "请先获取绑定二维码")
        step = verify_code(rec.totp_pending, code, now=moment)
        if step is None:
            raise AuthError(401, "验证码不正确,请对照 Authenticator 重试")

        codes = generate_codes()
        await self._registry.activate_totp(rec.tenant, rec.totp_pending,
                                           [hash_code(c) for c in codes])
        await self._store.set_last_totp_step((rec.username or "").casefold(), step)
        return codes

    async def totp_disable(self, api_key: str, password: str, code: str, *,
                           now: float | None = None) -> None:
        moment = time.time() if now is None else now
        rec = await self._require_tenant(api_key)
        if not rec.totp_secret:
            raise AuthError(409, "尚未绑定两步验证")
        await self._require_password_and_code(rec, password, code, moment)
        await self._registry.disable_totp(rec.tenant)

    async def regenerate_backup_codes(self, api_key: str, password: str, code: str, *,
                                      now: float | None = None) -> list[str]:
        moment = time.time() if now is None else now
        rec = await self._require_tenant(api_key)
        if not rec.totp_secret:
            raise AuthError(409, "尚未绑定两步验证")
        await self._require_password_and_code(rec, password, code, moment)
        codes = generate_codes()
        await self._registry.set_backup_codes(rec.tenant, [hash_code(c) for c in codes])
        return codes

    # ── 内部工具 ──────────────────────────────────────────────────────
    async def _require_tenant(self, api_key: str):
        rec = await self._registry.get_tenant_by_key(api_key or "")
        if rec is None:
            raise AuthError(401, "X-Tenant-Key 无效")
        return rec

    async def _register_failure(self, key: str, state, moment: float) -> None:
        await self._store.set_throttle(key, register_failure(
            state, now=moment,
            max_failures=self._settings.auth_max_failures,
            lock_max_minutes=self._settings.auth_lock_max_minutes))

    async def _require_second_factor(self, rec, code: str, moment: float) -> None:
        """已绑定两步验证时校验验证码;未绑定则不要求。"""
        if not rec.totp_secret:
            return
        if not (code or "").strip():
            raise AuthError(401, "已开启两步验证,请填写验证码")
        if not await self._consume_second_factor(rec, code, (rec.username or "").casefold(),
                                                 moment):
            raise AuthError(401, "验证码不正确")

    async def _require_password_and_code(self, rec, password: str, code: str,
                                         moment: float) -> None:
        if not rec.password_hash or not verify_password(password, rec.password_hash):
            raise AuthError(401, "密码或验证码不正确")
        if not await self._consume_second_factor(rec, code, (rec.username or "").casefold(),
                                                 moment):
            raise AuthError(401, "密码或验证码不正确")

    async def _consume_second_factor(self, rec, code: str, key: str, moment: float) -> bool:
        """核销一次第二因子:6 位走 TOTP,10 位走备用码。命中返回 True。"""
        candidate = normalize(code)
        if len(candidate) == _TOTP_DIGITS and candidate.isdigit():
            last_step = await self._store.get_last_totp_step(key)
            step = verify_code(rec.totp_secret, candidate, now=moment, last_step=last_step)
            if step is None:
                return False
            await self._store.set_last_totp_step(key, step)     # 同一个码不能用第二次
            return True
        remaining = consume_backup_code(candidate, list(rec.backup_codes))
        if remaining is None:
            return False
        await self._registry.set_backup_codes(rec.tenant, remaining)
        return True
