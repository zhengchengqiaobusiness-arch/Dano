"""租户 / 系统实例 / 系统类型模板的数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dano.business_packs import system_templates_for
from dano.shared.enums import Subsystem


class SystemTemplate(BaseModel):
    """系统类型模板(流程1 第2步「选系统类型模板」)。

    模板决定:对应哪个子系统(开放键,任意 `{公司}-{系统}`)、用 API 还是页面接入、预期动作清单。
    """

    template_id: str                 # 任意类型 id(oa / crm / erp …),不限三件套
    subsystem: Subsystem             # 开放作用域键(P0):任意系统名均可
    integration: str                 # api / page
    actions: list[str] = Field(default_factory=list)


# Process-local extensions; tenant-owned seeds live in business packs.
SYSTEM_TEMPLATES: dict[str, SystemTemplate] = {}


def register_system_template(template: SystemTemplate) -> None:
    """注册 / 覆盖一个系统类型模板(扩展点,与 register_oa_template 同构)。

    让任意企业的任意系统类型(CRM/ERP/HR…)在部署期登记进目录,而不必改动本文件的字面量。
    """
    SYSTEM_TEMPLATES[template.template_id] = template


def all_system_templates(tenant: str = "") -> list[SystemTemplate]:
    """Return registered extensions plus the selected tenant's templates."""
    configured = [SystemTemplate.model_validate(item) for item in system_templates_for(tenant)]
    merged = {item.template_id: item for item in configured}
    merged.update(SYSTEM_TEMPLATES)
    return list(merged.values())


def get_system_template(template_id: str, tenant: str = "") -> SystemTemplate | None:
    """按 id 取系统类型模板;未登记返回 None(由调用方决定回退/报错)。"""
    return next((item for item in all_system_templates(tenant) if item.template_id == template_id), None)


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
    username: str = ""
    password_hash: str = ""
    api_key: str = Field(default_factory=new_api_key)
