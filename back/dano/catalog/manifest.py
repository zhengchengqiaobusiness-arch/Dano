"""Skill 标准契约(工具定义)。function-calling / MCP 风格,前端与 LLM 都能直接消费。"""

from __future__ import annotations

import re

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
    capability: str = ""              # 对外能力键;旧资产为空时等于 name,保持 skill_id 兼容
    capability_meta: dict = Field(default_factory=dict)  # 能力别名/来源/迁移信息,不进入 JSON Schema
    capabilities: list[dict] = Field(default_factory=list)  # 一个 Skill 内可调用的业务能力列表
    subsystem: str
    action: str
    title: str
    description: str
    business: str = ""                # 所属业务(同业务多操作导出时归为一本剧本 skill)
    business_meta: dict = Field(default_factory=dict)  # 业务规则(x-flow)→ 导出剧本的前置/错误/确认段
    goal: dict = Field(default_factory=dict)           # 结构化业务目标(意图/成功判据/禁止步)→ 导出剧本"目标"段
    field_mappings: list = Field(default_factory=list)  # 可追溯字段映射 → 导出剧本"字段映射"段
    call_metadata: dict = Field(default_factory=dict)   # 调用侧元数据(字段类型/枚举快照/录制验证状态),不属于 JSON Schema
    created_at: str = ""                # 最新 published 资产产出时间(目录展示/排序)
    lifecycle_state: str = ""           # 生命周期状态(异常暂停=冻结)
    frozen: bool = False                # 冻结后保留资产库,但不导出/不调用
    integration: str                  # 调用方式:adapter / workflow / api / page
    risk_level: str
    requires_confirmation: bool       # L3+ 调用需带 confirm=true
    recording_mode: str = ""          # 录制型 Skill 的提交模式:real_submit/intercepted_submit/unknown
    verification_status: str = ""     # 调用契约证据等级
    verification_basis: str = ""      # 验证证据来源:fact_check_configured/success_rule_configured/structure_only
    parameters: dict = Field(default_factory=dict)   # 输入 JSON Schema(function-calling 风格)
    output_schema: dict = Field(default_factory=lambda: {"type": "object"})  # 输出 schema(通用对象)
    call_protocol: dict = Field(default_factory=dict)  # 导出脚本/Agent JSON 调用协议草案
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


def _enum_label_value(opt) -> tuple[str, object] | None:
    """兼容 options 为 string 或 {label,value} 的形态,给 schema/前端提供稳定枚举事实。"""
    if isinstance(opt, dict):
        label = str(opt.get("label") or opt.get("text") or opt.get("name") or opt.get("value") or "").strip()
        if not label:
            return None
        return label, opt.get("value", label)
    label = str(opt or "").strip()
    if not label:
        return None
    return label, label


def _enum_facts(sel: dict | None) -> tuple[list[str], dict[str, object], bool, bool]:
    """选择型字段的候选事实 → (opts, has_source, is_static_enum)。

    **静态页面枚举**(enum_source=dom/manual,如 请假类型=病假/事假/婚假;或无来源的纯枚举)→ 完整且稳定 → 可烤进 schema;
    **活接口目录**(用户/部门/审批人等网络源:会变、常被截断)→ **绝不烤静态清单**(否则前端被陈旧/错误选项硬约束,
    选的值与实际不符 → 入库失败),只暴露来源让调用方运行期 `--list-options` 现拉。通用,不挑系统/字段。
    """
    records = [_enum_label_value(o) for o in ((sel or {}).get("options") or [])]
    pairs = [p for p in records if p is not None]
    opts = [p[0] for p in pairs]
    option_map = dict((sel or {}).get("option_map") or {})
    for label, value in pairs:
        option_map.setdefault(label, value)
    if _options_look_value_only(opts, option_map):
        opts = []
        option_map = {}
    cnt = int((sel or {}).get("count") or len(opts))
    has_source = bool((sel or {}).get("source_url"))
    enum_source = str((sel or {}).get("enum_source") or "")
    static_source = enum_source in {"dom", "manual"}
    truncated = bool(opts) and cnt > len(opts)
    static = bool(opts) and not truncated and (static_source or not has_source)
    return opts, option_map, has_source, static


_VALUE_ONLY_LABEL_RE = re.compile(
    r"^\s*(?:[-+]?\d+(?:\.\d+)?|[0-9a-f]{8,}|[A-Za-z]{0,4}[-_]?\d{3,}|[A-Za-z0-9_-]{12,})\s*$",
    re.I,
)


def _options_look_value_only(opts: list[str], option_map: dict[str, object]) -> bool:
    if not opts or not all(_VALUE_ONLY_LABEL_RE.match(str(o)) for o in opts):
        return False
    return not any(
        label and not _VALUE_ONLY_LABEL_RE.match(str(label)) and str(value) != str(label)
        for label, value in (option_map or {}).items()
    )


def _select_semantic_type(declared: str | None, sel: dict | None) -> str | None:
    """select 元数据是比 body 叶子值更强的语义证据。

    真实页面里固定下拉经常提交短码(type=2),body 值推断会得到 number；但只要录制链路已经确认
    这是 select/page_enum/api_option,对外调用契约就必须让用户选/传显示名,再由运行期映射成真实 value。
    """
    if declared in {"enum", "list-enum"}:
        return declared
    if not sel:
        return declared
    if sel.get("multi"):
        return "list-enum"
    if sel.get("source_url") or sel.get("options") or sel.get("option_map") or sel.get("enum_source"):
        return "enum"
    return declared


def _schema_prop(skill: SkillSpec, field: str, desc: str, sel: dict | None = None) -> dict:
    """字段 → JSON Schema 属性。**type 保持合法**(function-calling 可直接用),但**语义不丢**:

    - `enum`(选择型):type=string + format=name-ref + 描述提示「传名字→运行期查内部 ID」;
      **静态页面枚举**(固定下拉)烤进 `enum` 硬约束;**活接口目录**(选人/选部门/审批人:会变)**不烤** —— 只标
      `x-options-source`,让调用方 `--list-options` 实时拉当前可选项(否则陈旧/错误清单硬约束 agent → 入库失败);
    - `datetime`/`date`:type=string + 标准 format,告诉 agent 这是日期时间字段;
    - 其余按信源声明 / 数值语义判定。format 为 JSON Schema 扩展位,校验器忽略未知值,安全。
    """
    declared = _select_semantic_type((getattr(skill, "field_types", {}) or {}).get(field), sel)
    # label=字段纯语义(给 SOP/复述用,简洁);description=语义 + 调用约定(给参数表/function-calling 用)。
    # 约定不写死示例值(『张三』只适合选人,不适合选值如请假类型);示例由前端/样例值提供,不在此臆造。
    if declared == "enum":
        opts, option_map, has_source, static = _enum_facts(sel)
        prop = {"type": "string", "format": "name-ref", "label": desc}
        if static:                                           # 静态页面枚举(固定下拉)→ 烤清单
            prop["description"] = desc + ("(传名字/选项文字,Dano 提交时按名字现查内部 ID,**勿直接传 ID/编号**;"
                                          f"可先 `--list-options {field}` 实时拉可选项再选)")
            if has_source:
                prop["x-options-source"] = True
            prop["x-options"] = opts
            prop["x-enum-options"] = [{"label": o, "value": option_map.get(o, o)} for o in opts]
            prop["x-enum-value-map"] = option_map
            if len(opts) <= _OPTIONS_INLINE_MAX:
                prop["enum"] = opts                          # 静态枚举 ≤50:烤进 enum,function-calling 层约束只能选真实值
        elif has_source:                                     # 活接口目录(选人/部门/审批人:会变)→ 不烤清单,只暴露实时接口
            prop["description"] = desc + ("(传名字,Dano 提交时按名字现查内部 ID,**勿直接传 ID/编号**;"
                                          f"**该字段选项来自实时接口、会随人员/组织变化** —— 选前**必须**先 `--list-options {field}` "
                                          "拉当前可选项再传名字,**勿照搬旧快照**)")
            prop["x-options-source"] = True
        else:                                                # 既无固定清单也无来源接口:中性 name-ref(只提示传名字)
            prop["description"] = desc + "(传名字/选项文字,Dano 提交时按名字现查内部 ID,**勿直接传 ID/编号**)"
        return prop
    if declared == "list-enum":
        # 列表多选(参会人/抄送人…):agent 传**名字数组**,运行期每个名字经来源接口拼成整条记录。
        opts, option_map, has_source, static = _enum_facts(sel)
        item = {"type": "string", "format": "name-ref"}
        prop = {"type": "array", "items": item, "label": desc}
        if static:
            prop["description"] = desc + ("(**多选**:传**名字列表**,Dano 按每个名字现查内部信息拼成整条记录,"
                                          f"**勿传 ID/编号**;可先 `--list-options {field}` 实时拉可选项)")
            if has_source:
                prop["x-options-source"] = True
            prop["x-options"] = opts
            prop["x-enum-options"] = [{"label": o, "value": option_map.get(o, o)} for o in opts]
            prop["x-enum-value-map"] = option_map
            if len(opts) <= _OPTIONS_INLINE_MAX:
                item["enum"] = opts                          # 静态枚举 ≤50:内置 items.enum
        else:                                                # 活接口目录(选人多选):不烤清单,暴露实时接口
            prop["description"] = desc + ("(**多选**:传**名字列表**;**选项来自实时接口、会变** —— 选前**必须**先 "
                                          f"`--list-options {field}` 拉当前可选项再传名字,**勿传 ID/编号、勿照搬旧快照**)")
            if has_source:
                prop["x-options-source"] = True
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


def _field_call_metadata(skill: SkillSpec, props: dict, sels: dict) -> dict:
    """字段调用元数据:给目录/前端/导出读,避免污染 OpenAI function-calling JSON Schema。"""
    declared_types = getattr(skill, "field_types", {}) or {}
    fields = {}
    for name, prop in props.items():
        info_type = _select_semantic_type(declared_types.get(name) or prop.get("type") or "string", sels.get(name))
        info = {"type": info_type or "string"}
        if prop.get("format"):
            info["format"] = prop["format"]
        sel = sels.get(name) or {}
        enum_options = prop.get("x-enum-options") or prop.get("x-options")
        if not enum_options:
            fallback = list(sel.get("options") or [])
            fallback_pairs = [p for p in (_enum_label_value(o) for o in fallback) if p]
            fallback_labels = [label for label, _value in fallback_pairs]
            fallback_map = dict(sel.get("option_map") or {})
            for label, value in fallback_pairs:
                fallback_map.setdefault(label, value)
            if not _options_look_value_only(fallback_labels, fallback_map):
                enum_options = fallback
        if enum_options:
            info["enum_options"] = enum_options
        enum_value_map = prop.get("x-enum-value-map") or sel.get("enum_value_map") or sel.get("option_map") or {}
        if enum_value_map and _options_look_value_only(list(map(str, enum_value_map.keys())), dict(enum_value_map)):
            enum_value_map = {}
        if enum_value_map:
            info["enum_value_map"] = dict(enum_value_map)
        if sel.get("source_url"):
            info["options_source"] = sel.get("source_url")
        if sel.get("enum_source"):
            info["enum_source"] = sel.get("enum_source")
        if sel.get("enum_confirmed") is not None:
            info["enum_confirmed"] = bool(sel.get("enum_confirmed"))
        fields[name] = info
    return fields


def _call_metadata(skill: SkillSpec, parameters: dict) -> dict:
    meta = dict(getattr(skill, "call_metadata", {}) or {})
    for key in ("recording_mode", "verification_status", "verification_basis"):
        val = getattr(skill, key, "")
        if val not in (None, "") and key not in meta:
            meta[key] = val
    props = (parameters or {}).get("properties", {}) or {}
    fields = _field_call_metadata(skill, props, _api_selects(skill))
    if fields:
        meta["fields"] = fields
    return meta


def _capability_of(skill: SkillSpec) -> str:
    """能力键优先使用显式 capability;旧 Skill 没有时退回 skill_id。"""
    meta = getattr(skill, "call_metadata", {}) or {}
    goal = getattr(skill, "goal", {}) or {}
    caps = list(getattr(skill, "capabilities", []) or meta.get("capabilities") or [])
    default_cap = ""
    for preferred in ("submit_batch", "submit", "query_status", "list_options"):
        hit = next((c for c in caps if isinstance(c, dict) and (c.get("name") == preferred or c.get("kind") == preferred)), None)
        if hit:
            default_cap = str(hit.get("name") or hit.get("kind") or "").strip()
            break
    candidates = [
        getattr(skill, "capability", ""),
        meta.get("capability"),
        goal.get("capability") if isinstance(goal, dict) else "",
        default_cap,
        skill.skill_id,
    ]
    for val in candidates:
        cap = str(val or "").strip()
        if cap:
            return cap
    return skill.skill_id


def _capability_meta(skill: SkillSpec, capability: str) -> dict:
    """收集能力元数据,并显式记录 legacy skill/tool 名,方便调用协议迁移。"""
    meta: dict = {}
    raw = getattr(skill, "capability_meta", {}) or {}
    if isinstance(raw, dict):
        meta.update(raw)
    call_meta = getattr(skill, "call_metadata", {}) or {}
    raw = call_meta.get("capability_meta") if isinstance(call_meta, dict) else None
    if isinstance(raw, dict):
        meta.update(raw)
    meta.setdefault("legacy_skill_id", skill.skill_id)
    meta.setdefault("legacy_tool_name", skill.skill_id.replace(".", "__"))
    if capability != skill.skill_id:
        aliases = list(meta.get("aliases") or [])
        for val in (skill.skill_id, skill.skill_id.replace(".", "__")):
            if val not in aliases:
                aliases.append(val)
        meta["aliases"] = aliases
    return meta


def _call_protocol(capability: str, skill_id: str) -> dict:
    """导出给 Agent 的调用协议草案;同时声明旧 name 兼容通道。"""
    return {
        "protocol": "dano.capability_call.draft",
        "transport": "POST /v1/tools/call",
        "capability": capability,
        "capability_key": "capability",
        "legacy_name": skill_id.replace(".", "__"),
        "arguments_keys": ["input", "arguments"],
        "confirm_key": "confirm",
        "compatibility": "payload always includes legacy name for existing Dano gateways",
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
    parameters = _parameters_schema(skill)
    call_metadata = _call_metadata(skill, parameters)
    capability = _capability_of(skill)
    capabilities = list(getattr(skill, "capabilities", []) or call_metadata.get("capabilities") or [])
    return SkillManifest(
        name=skill.skill_id,
        capability=capability,
        capability_meta=_capability_meta(skill, capability),
        capabilities=capabilities,
        subsystem=skill.subsystem.value,
        action=skill.action,
        title=title,
        description=f"{title}({skill.subsystem.value} · {kind}类动作)",
        business=getattr(skill, "business", ""),
        business_meta=getattr(skill, "business_meta", {}) or {},
        goal=getattr(skill, "goal", {}) or {},
        field_mappings=getattr(skill, "field_mappings", []) or [],
        call_metadata=call_metadata,
        created_at=(skill.created_at.isoformat() if getattr(skill, "created_at", None) else ""),
        lifecycle_state=getattr(skill, "lifecycle_state", "") or "",
        frozen=bool(getattr(skill, "frozen", False)),
        integration=integration,
        risk_level=risk.value,
        requires_confirmation=risk in _CONFIRM_FROM,
        recording_mode=call_metadata.get("recording_mode", ""),
        verification_status=call_metadata.get("verification_status", ""),
        verification_basis=call_metadata.get("verification_basis", ""),
        parameters=parameters,
        call_protocol=_call_protocol(capability, skill.skill_id),
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
