"""P4:系统类型模板目录**开放可注册** —— 任意企业系统类型都能登记,不限三件套原型。"""
from __future__ import annotations

from dano.registry import (
    SYSTEM_TEMPLATES,
    SystemTemplate,
    all_system_templates,
    get_system_template,
    register_system_template,
)
from dano.shared.enums import Subsystem


def test_register_arbitrary_system_type():
    assert get_system_template("crm") is None          # 起初无此类型
    tpl = SystemTemplate(template_id="crm", subsystem=Subsystem("B-CRM"),
                         integration="api", actions=["create_customer"])
    try:
        register_system_template(tpl)
        got = get_system_template("crm")
        assert got is not None and got.subsystem.value == "B-CRM"   # 开放作用域键
        assert tpl in all_system_templates()
        assert get_system_template("oa") is not None   # 原型种子仍在(向后兼容)
    finally:
        SYSTEM_TEMPLATES.pop("crm", None)              # 清理全局态,不污染其它测试
