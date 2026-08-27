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


async def _bound() -> tuple[AuthService, str, str]:
    """返回 (服务, api_key, totp 密钥),TOTP 已激活。"""
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)
    return svc, key, secret


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
        await svc.login("acme", PASSWORD, now=NOW)              # 密码正确也被拒
    result = await svc.login("acme", PASSWORD, now=NOW + 61)    # 锁定期过后放行
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


async def test_用户名大小写不影响限流键():
    svc = await _service()
    for _ in range(3):
        with pytest.raises(AuthError):
            await svc.login("ACME", "wrong-password-here", now=NOW)
    for _ in range(2):
        with pytest.raises(AuthError):
            await svc.login("acme", "wrong-password-here", now=NOW)
    with pytest.raises(AuthError):               # 合计 5 次 → 已锁定
        await svc.login("acme", PASSWORD, now=NOW)


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


async def test_激活前不算已绑定():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    await svc.totp_setup(key)
    assert (await svc.login("acme", PASSWORD, now=NOW)).need_totp is False


async def test_同一个totp码不能用两次():
    svc, key, secret = await _bound()
    code = totp_code(secret, now=NOW + 60)
    first = await svc.login("acme", PASSWORD, now=NOW + 60)
    assert (await svc.verify_totp_login(first.challenge, code, now=NOW + 60)).api_key == key
    second = await svc.login("acme", PASSWORD, now=NOW + 60)
    with pytest.raises(AuthError):
        await svc.verify_totp_login(second.challenge, code, now=NOW + 60)


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
    svc, _, secret = await _bound()
    first = await svc.login("acme", PASSWORD, now=NOW + 60)
    for _ in range(5):
        with pytest.raises(AuthError):
            await svc.verify_totp_login(first.challenge, "000000", now=NOW + 60)
    with pytest.raises(AuthError):   # 即便这次给对码,challenge 已废
        await svc.verify_totp_login(
            first.challenge, totp_code(secret, now=NOW + 60), now=NOW + 60)


async def test_challenge过期被拒():
    svc, _, secret = await _bound()
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


async def test_改密原密码错误被拒():
    svc = await _service()
    key = (await svc.login("acme", PASSWORD, now=NOW)).api_key
    with pytest.raises(AuthError):
        await svc.change_password(key, "wrong-password-here", "another-good-password", now=NOW)


async def test_已绑定totp时改密必须带验证码():
    svc, key, secret = await _bound()
    with pytest.raises(AuthError, match="验证码"):
        await svc.change_password(key, PASSWORD, "another-good-password", now=NOW + 60)
    await svc.change_password(key, PASSWORD, "another-good-password",
                              totp_code(secret, now=NOW + 60), now=NOW + 60)


async def test_重复setup返回409():
    svc, key, _ = await _bound()
    with pytest.raises(AuthError) as exc:
        await svc.totp_setup(key)
    assert exc.value.status == 409


async def test_解绑需要密码与验证码():
    svc, key, secret = await _bound()
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


async def test_无效api_key被拒():
    svc = await _service()
    with pytest.raises(AuthError) as exc:
        await svc.totp_setup("dk_不存在")
    assert exc.value.status == 401


async def test_大小写混合用户名可完成两步登录():
    reg = InMemoryRegistry()
    await reg.create_tenant(TenantRecord(
        tenant="acme", username="Acme.Admin", password_hash=hash_password(PASSWORD)))
    svc = AuthService(registry=reg, store=InMemoryAuthStore(), settings=Settings())

    key = (await svc.login("Acme.Admin", PASSWORD, now=NOW)).api_key
    secret = (await svc.totp_setup(key))["secret"]
    await svc.totp_activate(key, totp_code(secret, now=NOW), now=NOW)

    first = await svc.login("Acme.Admin", PASSWORD, now=NOW + 60)
    assert first.need_totp is True
    second = await svc.verify_totp_login(
        first.challenge, totp_code(secret, now=NOW + 60), now=NOW + 60)
    assert second.api_key == key
