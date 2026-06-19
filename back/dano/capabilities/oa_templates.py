"""OA 框架模板库(阶段1):识别常见 OA/工作流系统,套用其鉴权/基础设施/成败约定。

定位:接入一份 Swagger 时,先认出"这是哪种 OA 框架"(如 RuoYi-Flowable),自动:
- 标出该框架的基础设施接口(登录/验证码/路由等),不暴露成业务 Skill;
- 注入该框架的成败判定规则(如 RuoYi 用 HTTP200 + body.code==200,而非通用单号)。

不臆造:只用 spec 里能客观判定的特征(路径前缀 / schema 名 / tags)匹配模板。
扩展:实现 OATemplate 子类 + register_oa_template() 即可接新框架(钉钉/泛微/企微…)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

from dano.shared.asset_bodies import WorkflowSkillBody

log = structlog.get_logger(__name__)


class OATemplate(ABC):
    """一种 OA 框架的识别模板。"""

    name: str = "generic"

    @abstractmethod
    def matches(self, spec: dict[str, Any]) -> bool:
        """该 spec 是否属于本框架(只用客观特征判定)。"""

    def success_rule(self) -> str | None:
        """运行期判定调用成败的断言表达式;None=只用 HTTP 2xx。"""
        return None

    def infrastructure_patterns(self) -> tuple[str, ...]:
        """本框架额外的基础设施关键词(动作名/路径命中 → 不暴露成业务 Skill)。"""
        return ()

    def workflows(self) -> list[WorkflowSkillBody]:
        """本框架的复合流程 Skill 配方(多步连接器编排成一个业务能力,阶段2)。

        只声明配方;接入时若配方引用的连接器动作未全部发布,则跳过该复合 Skill。
        """
        return []


class RuoYiFlowableTemplate(OATemplate):
    """RuoYi-Vue + Flowable 工作流(请假/审批等 BPMN 流程)。

    特征:路径含 /workflow/ 或 /flowable/,或 components.schemas 有 AjaxResult。
    成败:RuoYi 统一返回 HTTP 200 + body.code(200成功/500失败);列表类无 code 字段,
          故规则写成"有 code 就必须 200,没有 code 则靠 HTTP 2xx"。
    """

    name = "ruoyi-flowable"

    def matches(self, spec: dict[str, Any]) -> bool:
        paths = " ".join(spec.get("paths", {}) or {}).lower()
        schemas = (spec.get("components", {}) or {}).get("schemas", {}) or {}
        return "/workflow/" in paths or "/flowable/" in paths or "AjaxResult" in schemas

    def success_rule(self) -> str | None:
        return "response.code == null or response.code == 200"

    def infrastructure_patterns(self) -> tuple[str, ...]:
        return ("captcha", "getinfo", "getrouters", "logout")

    def workflows(self) -> list[WorkflowSkillBody]:
        """本框架的业务复合配方,**按业务区分开**定义在 dano.capabilities.business/(请假/出差/…)。

        共享 RuoYi 3 步契约(发起→存表单→提交;成败以事实核查为准,不信字面 200),
        各业务只在字段/模板/风险上区分。新增业务在 business 包加一个模块即可。
        """
        from dano.capabilities.business import recipes
        return recipes()


# 注册表(靠前优先)。新增框架插到最前。
_TEMPLATES: list[OATemplate] = [RuoYiFlowableTemplate()]


def register_oa_template(template: OATemplate) -> None:
    _TEMPLATES.insert(0, template)


def match_template(spec: dict[str, Any]) -> OATemplate | None:
    """匹配 spec 所属的 OA 框架模板;无匹配返回 None(走通用规则)。"""
    if not isinstance(spec, dict):
        return None
    for t in _TEMPLATES:
        try:
            if t.matches(spec):
                log.info("oa_template.matched", template=t.name)
                return t
        except Exception:  # noqa: BLE001 - 模板匹配不应让接入崩
            continue
    return None
