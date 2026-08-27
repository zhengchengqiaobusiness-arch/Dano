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
