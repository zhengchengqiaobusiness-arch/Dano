"""租户 / 系统实例 / 系统类型模板的数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dano.shared.enums import Subsystem


class SystemTemplate(BaseModel):
    """系统类型模板(流程1 第2步「选系统类型模板」)。

    模板决定:对应哪个子系统(开放键,任意 `{公司}-{系统}`)、用 API 还是页面接入、预期动作清单。
    """

    template_id: str                 # 任意类型 id(oa / crm / erp …),不限三件套
    subsystem: Subsystem             # 开放作用域键(P0):任意系统名均可
    integration: str                 # api / page
    actions: list[str] = Field(default_factory=list)


# 系统类型模板目录:**开放可注册**(不是固定三类)。下列三项只是 A 公司原型**种子**;
# 部署方 / dialect 可经 `register_system_template` 追加任意系统类型。接入并不强制从此目录选
# (subsystem 已是开放键),真实"选业务模板"由 /onboarding/list-templates 经 dialect 动态提供。
SYSTEM_TEMPLATES: dict[str, SystemTemplate] = {
    "oa": SystemTemplate(template_id="oa", subsystem=Subsystem.OA, integration="api",
                         actions=["query_balance", "create_leave", "query_approval"]),
    "ticket": SystemTemplate(template_id="ticket", subsystem=Subsystem.TICKET, integration="api",
                             actions=["create_ticket", "query_ticket"]),
    "reimburse": SystemTemplate(template_id="reimburse", subsystem=Subsystem.REIMBURSE,
                                integration="page", actions=["create_reimburse_draft"]),
}


def register_system_template(template: SystemTemplate) -> None:
    """注册 / 覆盖一个系统类型模板(扩展点,与 register_oa_template 同构)。

    让任意企业的任意系统类型(CRM/ERP/HR…)在部署期登记进目录,而不必改动本文件的字面量。
    """
    SYSTEM_TEMPLATES[template.template_id] = template


def all_system_templates() -> list[SystemTemplate]:
    """目录里全部系统类型模板(内置种子 + 已注册)。"""
    return list(SYSTEM_TEMPLATES.values())


def get_system_template(template_id: str) -> SystemTemplate | None:
    """按 id 取系统类型模板;未登记返回 None(由调用方决定回退/报错)。"""
    return SYSTEM_TEMPLATES.get(template_id)


def new_api_key() -> str:
    """生成公司唯一标识 api_key。"""
    import secrets

    return "dk_" + secrets.token_hex(16)


class TenantRecord(BaseModel):
    """租户(流程1 第1步「建 A 公司租户」)。api_key 为公司唯一标识,前端调用凭此鉴权。"""

    tenant: str
    display_name: str = ""
    deploy: str = ""
    worker_location: str = ""
    log_policy: str = ""
    api_key: str = Field(default_factory=new_api_key)


class SystemInstance(BaseModel):
    """系统实例(流程1 第3步「创建系统实例 A-OA / A-工单 / A-报销」)。"""

    tenant: str
    subsystem: Subsystem
    type_template: str               # 选用的模板 id
    integration: str                 # api / page
    status: str = "created"          # created → onboarded
