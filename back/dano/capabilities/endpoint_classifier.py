"""端点分类(阶段0):把接口分为 基础设施 / 查询 / 业务动作。

目的:工作流系统的 Swagger 里混着大量"管道接口"(登录、验证码、获取用户信息、路由),
它们不是用户要的业务能力。现在的"1接口=1Skill"会把它们也当 Skill 暴露(垃圾)。
本分类器把基础设施接口标出来,让规划层不为它们生成业务 Skill。

判定只用客观信号(动作名 / 路径 / tags / HTTP 方法),模板可追加框架特定的基础设施关键词。
"""

from __future__ import annotations

from dano.capabilities.doc_parser import ActionSpec

INFRASTRUCTURE = "infrastructure"   # 鉴权/会话/路由等管道,不暴露成业务 Skill
QUERY = "query"                     # 查询类(只读)
BUSINESS = "business_action"        # 业务写动作

# 通用基础设施关键词(动作名/路径/标签子串命中即视为基础设施)
_INFRA_HINTS = (
    "captcha", "login", "logout", "gettoken", "refreshtoken", "getinfo", "userinfo",
    "getrouters", "register", "smscode", "verifycode", "/auth/", "authorize",
)
# 查询类动作名前缀
_QUERY_PREFIXES = ("list", "get", "query", "page", "detail", "search", "export", "find")


def classify(action: ActionSpec, *, extra_infra: tuple[str, ...] = ()) -> str:
    """对一个端点分类。extra_infra:模板追加的框架特定基础设施关键词。"""
    hay = f"{action.name} {action.endpoint} {' '.join(action.tags)}".lower()
    if any(h in hay for h in _INFRA_HINTS) or any(h in hay for h in extra_infra):
        return INFRASTRUCTURE
    if action.method.upper() == "GET" or action.name.lower().startswith(_QUERY_PREFIXES):
        return QUERY
    return BUSINESS


def is_business_skill(action: ActionSpec, *, extra_infra: tuple[str, ...] = ()) -> bool:
    """是否应暴露成业务 Skill(查询 + 业务动作 = 是;基础设施 = 否)。"""
    return classify(action, extra_infra=extra_infra) != INFRASTRUCTURE
