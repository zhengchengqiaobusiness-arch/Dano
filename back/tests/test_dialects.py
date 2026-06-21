"""阶段1:系统方言(dialect)单测——系统特定的复合契约知识只活在 dialect。

纯离线:无 PG / LLM / 网络(discover_contract 用注入的 fake get 探针)。
"""
from __future__ import annotations

from dano.capabilities import oa_templates
from dano.capabilities.oa_templates import OATemplate

RUOYI_SPEC = {
    "paths": {"/workflow/handle/startFlow": {"post": {}}, "/biz/flow/submit": {"post": {}}},
    "components": {"schemas": {"AjaxResult": {}}},
}
GENERIC_SPEC = {"paths": {"/api/orders": {"get": {}}, "/api/orders/create": {"post": {}}}}


def test_ruoyi_detected():
    t = oa_templates.match_template(RUOYI_SPEC)
    assert t is not None and t.name == "ruoyi-flowable"


def test_generic_spec_not_matched():
    # 非工作流系统不命中任何模板 → 主流程按 dialect=None 走通用路径
    assert oa_templates.match_template(GENERIC_SPEC) is None


def test_ruoyi_owns_contract_literals():
    t = oa_templates.match_template(RUOYI_SPEC)
    toks = t.contract_tokens()
    assert "/biz/flow" in toks and "form/info" in toks
    eps = t.submit_endpoints()
    assert eps[0] == "/workflow/handle/startFlow"
    assert eps[-1] == "/biz/flow/submit"          # 最后一个 = 最终提交步


def test_base_template_defaults_are_empty():
    class _Custom(OATemplate):
        name = "custom"

        def matches(self, spec):  # noqa: ANN001
            return True

    t = _Custom()
    assert t.contract_tokens() == ()
    assert t.submit_endpoints() == ()


async def test_base_discover_contract_returns_none():
    class _Custom(OATemplate):
        def matches(self, spec):  # noqa: ANN001
            return True

    assert await _Custom().discover_contract("tpl", "http://x", "tok") is None


def test_ruoyi_form_probe_path_and_parse():
    t = oa_templates.match_template(RUOYI_SPEC)
    assert t.form_probe_path("tpl-9") == "/biz/form/info?businessId=&templateId=tpl-9"
    assert t.form_probe_path("") is None              # 无 templateId → 不探
    resp = {"code": 200, "data": {"formData": (
        '{"formData":{"fields":[{"__vModel__":"amount",'
        '"__config__":{"label":"金额","tag":"el-input-number"}}]}}')}}
    fields = t.parse_form_fields(resp)
    assert fields == [{"key": "amount", "label": "金额", "type": "el-input-number"}]


def test_base_form_probe_defaults():
    class _Custom(OATemplate):
        def matches(self, spec):  # noqa: ANN001
            return True

    t = _Custom()
    assert t.form_probe_path("x") is None
    assert t.parse_form_fields({"code": 200}) == []


async def test_ruoyi_discover_contract_with_fake_probe():
    t = oa_templates.match_template(RUOYI_SPEC)

    async def fake_get(path: str, params: dict | None = None):
        assert path == "/biz/form/info"
        assert params == {"templateId": "tpl-1"}
        return {"formData": ('{"fields":[{"__vModel__":"title",'
                             '"__config__":{"label":"标题","tag":"el-input","required":true}}]}')}

    contract = await t.discover_contract("tpl-1", "http://oa", "tok", get=fake_get)
    assert contract is not None
    assert contract["success_rule"] == "response.code == 200"
    assert [f["name"] for f in contract["fields"]] == ["title"]
    assert contract["submit_example"]["flowTask"]["templateId"] == "tpl-1"
