"""租户 / 系统实例 / 系统类型模板的数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dano.shared.enums import Subsystem


class SystemTemplate(BaseModel):
    """系统类型模板(流程1 第2步「选系统类型模板」)。

    模板决定:对应哪个子系统、用 API 还是页面接入、预期动作清单。
    """

    template_id: str                 # oa / ticket / reimburse
    subsystem: Subsystem
    integration: str                 # api / page
    actions: list[str] = Field(default_factory=list)


# A 公司原型三类系统模板(平台一次建设,接入时选用)
SYSTEM_TEMPLATES: dict[str, SystemTemplate] = {
    "oa": SystemTemplate(template_id="oa", subsystem=Subsystem.OA, integration="api",
                         actions=["query_balance", "create_leave", "query_approval"]),
    "ticket": SystemTemplate(template_id="ticket", subsystem=Subsystem.TICKET, integration="api",
                             actions=["create_ticket", "query_ticket"]),
    "reimburse": SystemTemplate(template_id="reimburse", subsystem=Subsystem.REIMBURSE,
                                integration="page", actions=["create_reimburse_draft"]),
}


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
