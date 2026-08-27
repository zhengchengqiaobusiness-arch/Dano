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
    assert uri.startswith("otpauth://totp/Dano%3Aacme?")
    assert f"secret={RFC_SECRET}" in uri
    assert "issuer=Dano" in uri
