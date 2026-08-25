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
    validate_password("kf7-mq2x", min_length=8)
    with pytest.raises(PasswordPolicyError):
        validate_password("kf7-mq2", min_length=8)
