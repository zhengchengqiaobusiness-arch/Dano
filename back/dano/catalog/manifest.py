"""Skill 标准契约(工具定义)。function-calling / MCP 风格,前端与 LLM 都能直接消费。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dano.orchestrator.types import SkillSpec
from dano.shared.enums import RiskLevel
from dano.shared.std_fields import ALL_STD_FIELDS, is_flow_internal, is_form_envelope, is_numeric_field

# 动作友好标题(可扩展;缺省用 action 名)
_ACTION_TITLES: dict[str, str] = {
    "query_balance": "查询假期余额",
    "create_leave": "创建请假",
    "query_approval": "查询审批状态",
    "create_ticket": "创建 IT 工单",
    "query_ticket": "查询工单进度",
    "create_reimburse_draft": "创建报销草稿",
    "submit_leave": "提交请假申请",   # 复合流程(阶段2)
}

# 标准字段 → 人类可读描述(供前端表单/LLM 理解参数)
_FIELD_DESC = {f.key: (f.aliases[0] if f.aliases else f.key) for f in ALL_STD_FIELDS}

# 需用户确认的风险线(L3 及以上)
_CONFIRM_FROM = {RiskLevel.L3, RiskLevel.L4, RiskLevel.L5}


class SkillManifest(BaseModel):
    """一个 Skill 的标准工具契约。"""

    name: str                         # skill_id,如 "A-OA.create_leave"(调用入口)
    subsystem: str
    action: str
    title: str
    description: str
    business: str = ""                # 所属业务(同业务多操作导出时归为一本剧本 skill)
    business_meta: dict = Field(default_factory=dict)  # 业务规则(x-flow)→ 导出剧本的前置/错误/确认段
    goal: dict = Field(default_factory=dict)           # 结构化业务目标(意图/成功判据/禁止步)→ 导出剧本"目标"段
    field_mappings: list = Field(default_factory=list)  # 可追溯字段映射 → 导出剧本"字段映射"段
    integration: str                  # 调用方式:adapter / workflow / api / page
    risk_level: str
    requires_confirmation: bool       # L3+ 调用需带 confirm=true
    parameters: dict = Field(default_factory=dict)   # 输入 JSON Schema(function-calling 风格)
    output_schema: dict = Field(default_factory=lambda: {"type": "object"})  # 输出 schema(通用对象)
    page: dict | None = None          # 页面型 Skill 专属:{start_url, success_marker, steps[]}(供详情可视化)
    flow: dict = Field(default_factory=dict)   # 执行画像(供导出 SOP):步数/前置/计算/回查/成败约定;全部 grounded、零框架字面量


def _is_reserved(field: str) -> bool:
    """运行期注入的内部字段,不进对外契约/function-calling 参数:
    ① `__base_url__` 这类保留名;② 流程内部句柄(templateId/procInsId/taskId…,由 Dano 注入);
    ③ 整表序列化信封(formData 等,应拆成业务叶子,绝不暴露黑盒)。
    """
    return ((field.startswith("__") and field.endswith("__"))
            or is_flow_internal(field) or is_form_envelope(field))


_OPTIONS_INLINE_MAX = 50    # 候选 ≤ 此数 → 内置 enum 进 schema(agent 直接选);更多 → 只留来源,运行期 --list-options 现拉


def _api_selects(skill: SkillSpec) -> dict:
    """从 api_request(单请求 + 多步各步)汇总 select 元数据 → {参数名: select}。供字段 schema 补枚举/来源。"""
    apir = getattr(skill, "api_request", None) or {}
    sels = list(apir.get("selects") or [])
    for st in (apir.get("steps") or []):
        sels += list(st.get("selects") or [])
    return {s.get("param"): s for s in sels if s.get("param")}


def _schema_prop(skill: SkillSpec, field: str, desc: str, sel: dict | None = None) -> dict:
    """字段 → JSON Schema 属性。**type 保持合法**(function-calling 可直接用),但**语义不丢**:

    - `enum`(选领导/字典下拉):type=string + format=name-ref + 描述提示「传名字→运行期查内部 ID」
      (这是关键:agent 必须传名字/选项文字,而非内部 ID/编号,否则提交进的是错值);
      **候选选项内置进 schema**(≤50 条直接 `enum`;更多则带 `x-options-source`,运行期 --list-options 现拉)→
      agent 从真实可选值里选,不再凭空猜名(问题1:稳固、不易错);
    - `datetime`/`date`:type=string + 标准 format,告诉 agent 这是日期时间字段;
    - 其余按信源声明 / 数值语义判定。format 为 JSON Schema 扩展位,校验器忽略未知值,安全。
    """
    declared = (getattr(skill, "field_types", {}) or {}).get(field)
    # label=字段纯语义(给 SOP/复述用,简洁);description=语义 + 调用约定(给参数表/function-calling 用)。
    # 约定不写死示例值(『张三』只适合选人,不适合选值如请假类型);示例由前端/样例值提供,不在此臆造。
    if declared == "enum":
        prop = {"type": "string", "format": "name-ref", "label": desc,
                "description": desc + ("(传名字/选项文字,Dano 提交时按名字现查内部 ID,**勿直接传 ID/编号**;"
                                       f"选前先 `--list-options {field}` 实时拉可选项再选)")}
        opts = [o for o in ((sel or {}).get("options") or []) if str(o).strip()]
        cnt = int((sel or {}).get("count") or len(opts))
        if (sel or {}).get("source_url"):
            prop["x-options-source"] = True                  # 该字段有来源接口 → 可 --list-options 实时拉
        if opts:
            prop["x-options"] = opts                         # 候选快照(≤500),写进 references/OPTIONS.md 供离线参考
            if len(opts) <= _OPTIONS_INLINE_MAX:
                prop["enum"] = opts                          # ≤50 直接内置 enum:function-calling 层就约束 agent 只能选真实值
            if cnt > len(opts):                              # 快照被截断(候选 >500)→ 以实时拉取为准
                prop["x-options-truncated"] = True
        return prop
    if declared == "datetime":
        return {"type": "string", "format": "date-time", "label": desc,
                "description": desc + "(日期时间;传 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:mm:ss`,Dano 运行期自动转成目标系统格式,**勿自己拼时间戳**)"}
    if declared == "date":
        return {"type": "string", "format": "date", "label": desc,
                "description": desc + "(日期;传 `YYYY-MM-DD`,Dano 运行期自动转成目标系统格式)"}
    if declared in ("number", "integer", "boolean", "array", "object"):
        return {"type": declared, "label": desc, "description": desc}
    return {"type": "number" if is_numeric_field(field, desc, declared_type=declared) else "string",
            "label": desc, "description": desc}


def _parameters_schema(skill: SkillSpec) -> dict:
    """构造 JSON Schema(标准函数参数定义):必填 + 可选字段都暴露,required 仅列必填。

    - 字段描述优先用接口 schema 抽出的语义描述(阶段4),退而用标准字段别名,再退字段名。
    - 字段类型/语义按信源判定(数值=number、选择型=name-ref、日期=date(-time)),不再一律塌成 string。
    - 运行期注入字段(__base_url__、templateId 等流程句柄)一律剔除,不暴露给前端/LLM。
    """
    all_fields = [f for f in dict.fromkeys([*skill.required_fields, *skill.optional_fields])
                  if not _is_reserved(f)]
    sels = _api_selects(skill)                               # 选择型字段的候选选项/来源(内置进 schema)
    props = {}
    for f in all_fields:
        desc = skill.field_docs.get(f) or _FIELD_DESC.get(f, f)
        props[f] = _schema_prop(skill, f, desc, sels.get(f))
    return {
        "type": "object",
        "properties": props,
        "required": [f for f in skill.required_fields if not _is_reserved(f)],
        "additionalProperties": False,
    }


def _req_path(req: dict) -> str:
    """从一步请求里取干净的 path(去协议+域名,留 path,丢 query/敏感参数),供 SOP 展示编排。"""
    u = str(req.get("path") or req.get("url") or "")
    i = u.find("//")
    if i >= 0:
        j = u.find("/", i + 2)
        u = u[j:] if j >= 0 else "/"
    return (u.split("?")[0] or "/")


def _step_paths(steps: list[dict]) -> list[dict]:
    """各步接口签名(method + path),供导出 SOP 把多接口编排显式列出来(grounded,不臆造)。"""
    return [{"method": (s.get("method") or "POST").upper(), "path": _req_path(s)} for s in steps]


def _flow_meta(skill: SkillSpec) -> dict:
    """执行画像:供导出 SOP 渲染的**通用 grounded 数据**——步数、前置、计算、是否回查、是否按业务码判成败。

    全部从资产体抽,**不含任何业务/框架字面量**(渲染器据此产 SOP,而非写死"两步/采购/taskId")。
    各类 Skill 都给得出:工作流取 steps/preconditions;连接器/适配器/页面各按自身字段。
    """
    if skill.is_workflow:
        steps = list(getattr(skill, "workflow_steps", []) or [])
        n = sum(1 for s in steps if (s.get("kind") or "call") == "call")
        pre = [{"check": p.get("check", ""), "message": p.get("message", "")}
               for p in (getattr(skill, "workflow_preconditions", []) or []) if p.get("check")]
        comp = [{"out": o, "expr": e}
                for s in steps if s.get("kind") == "compute"
                for o, e in (s.get("outputs") or {}).items()]
        verify = any((i.get("evidence") or {}).get("query_action")
                     for i in (getattr(skill, "workflow_invariants", []) or []))
        return {"step_count": max(n, 1), "preconditions": pre, "computes": comp,
                "verify": verify, "judged_by_code": bool(getattr(skill, "workflow_success_rule", None))}
    if not skill.has_api:        # 页面型
        apir = getattr(skill, "api_request", None) or {}
        if apir:                 # 抓请求型:编排/成功约定/事实核查随 api_request 走(不再恒报"一步")
            steps = list(apir.get("steps") or [])
            wf = [s for s in steps if (s.get("method") or s.get("path") or s.get("url"))]
            last = (wf[-1] if wf else apir)
            verify = bool(apir.get("fact_check") or last.get("fact_check"))
            judged = bool(apir.get("success_rule") or last.get("success_rule"))
            return {"step_count": max(len(wf), 1), "preconditions": [], "computes": [],
                    "verify": verify, "judged_by_code": judged,
                    "step_paths": _step_paths(wf or [apir])}   # 各步 接口(method+path),供 SOP 展示编排
        return {"step_count": len(getattr(skill, "page_steps", []) or []) or 1,
                "preconditions": [], "computes": [],
                "verify": bool(getattr(skill, "page_success_marker", None)), "judged_by_code": False}
    if getattr(skill, "is_adapter", False):
        return {"step_count": 1, "preconditions": [], "computes": [],
                "verify": bool(getattr(skill, "adapter_fact_check", None)),
                "judged_by_code": bool(getattr(skill, "adapter_success_rule", None))}
    # 普通连接器
    return {"step_count": 1, "preconditions": [], "computes": [],
            "verify": bool(getattr(skill, "fact_check_query", None) or getattr(skill, "fact_check_expr", None)),
            "judged_by_code": False}


def to_manifest(skill: SkillSpec) -> SkillManifest:
    risk = RiskLevel(skill.risk_level)
    # 阶段4:标题优先用接口 summary(skill.title),退而用内置词典,再退动作名
    title = skill.title or _ACTION_TITLES.get(skill.action, skill.action)
    if getattr(skill, "is_adapter", False):
        integration, kind = "adapter", "流程"      # goal 模式生成的代码 Skill
    elif skill.is_workflow:
        integration, kind = "workflow", "流程"
    elif skill.has_api:
        integration = "api"
        kind = "查询" if skill.fact_check_query is None and skill.action.startswith("query") else "操作"
    else:
        integration, kind = "page", "操作"
    # 页面型 Skill:带上步骤/起始页/成功标志,供前端详情可视化(非 function-calling 参数)
    page = None
    if not skill.has_api and (getattr(skill, "page_steps", None) or getattr(skill, "page_start_url", "")):
        page = {"start_url": getattr(skill, "page_start_url", ""),
                "success_marker": getattr(skill, "page_success_marker", None),
                "steps": getattr(skill, "page_steps", []) or []}
    return SkillManifest(
        name=skill.skill_id,
        subsystem=skill.subsystem.value,
        action=skill.action,
        title=title,
        description=f"{title}({skill.subsystem.value} · {kind}类动作)",
        business=getattr(skill, "business", ""),
        business_meta=getattr(skill, "business_meta", {}) or {},
        goal=getattr(skill, "goal", {}) or {},
        field_mappings=getattr(skill, "field_mappings", []) or [],
        integration=integration,
        risk_level=risk.value,
        requires_confirmation=risk in _CONFIRM_FROM,
        parameters=_parameters_schema(skill),
        page=page,
        flow=_flow_meta(skill),
    )


def build_manifests(skills: list[SkillSpec]) -> list[SkillManifest]:
    """把一个租户的 Skill 列表转成标准契约目录。"""
    return [to_manifest(s) for s in skills]


# ── function-calling 工具导出(给聊天端 LLM 直接当 tools 用)──
# 工具名规则:skill_id 的点 '.' 在 OpenAI 函数名里不合法,转成 '__';回调时反向还原。
def tool_name_of(skill_id: str) -> str:
    return skill_id.replace(".", "__")


def skill_id_of(tool_name: str) -> str:
    return tool_name.replace("__", ".")


def to_function_tool(m: SkillManifest) -> dict:
    """转成 OpenAI function-calling tool 规格(name/description/parameters)。"""
    desc = m.description + ("(高风险:调用需 confirm=true)" if m.requires_confirmation else "")
    return {"type": "function",
            "function": {"name": tool_name_of(m.name), "description": desc,
                         "parameters": m.parameters}}


def build_function_tools(skills: list[SkillSpec]) -> list[dict]:
    """把租户 Skill 列表导出为聊天 LLM 可直接使用的 function-calling tools 数组。"""
    return [to_function_tool(to_manifest(s)) for s in skills]
