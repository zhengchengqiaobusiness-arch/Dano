"""剧本(Screenplay)派生层 —— P0 地基:把已有 api_request 的零散元数据(selects/identity/system_values/
steps/links)**收敛成统一的"字段角色 + 来源(provenance)+ 接口编排"**,供前端标注、导出剧本化使用。

**纯函数、零副作用、不改任何现有行为**:只读 api_request(+ 可选 reads),产出派生注释。
LLM 不参与(描述/命名在上层);本层结构全部确定性推导,不臆造。

核心产物:
- `FieldRole`  —— 每个字段"怎么来、怎么填"(前端据此区分渲染)。
- `StepRole`   —— 每个接口在剧本里的角色(选项来源 / 前置 / 提交 / 写 / 回查)。
- `build_screenplay(api_request)` —— 完整剧本:写步骤 + 选项来源接口 + 字段(角色+来源+用法)+ 步间数据流。
"""

from __future__ import annotations

import re as _re
from enum import Enum
from urllib.parse import urlparse

from dano.execution.page.request_capture import (
    _ASSIGNEE_CONTAINER_RE,
    _BPMN_NODE_RE,
    _JSONSTR,
    _SEG,
    classify_request_role,
)

# "不透明内部 ID"值:长串纯 hex/数字 或 uuid(如 ssbmId=020210601…)。这类"常量"是系统预设的环境相关 ID,
# 有来源但录制探不到 → 应标明"来源未探知,跨环境复用需人工确认",而非当成安全的模板字面量(oa_leave)。
_OPAQUE_ID_RE = _re.compile(
    r"^([0-9a-f]{10,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", _re.I)


def _is_opaque_id(value) -> bool:
    return bool(_OPAQUE_ID_RE.match(str(value if value is not None else "")))


# 分页 / 缓存破坏 / 排序等**非级联**查询参数:它们不随业务上下文变化,列出来只会误导成"级联依赖",过滤掉。
_NOISE_PARAMS = _re.compile(
    r"^(page|pageno|pagenum|pagesize|page_size|size|current|limit|offset|start|count|"
    r"sort|order|orderby|asc|desc|_t|t|timestamp|_|rnd|random|nocache|v|version)$", _re.I)


def _source_params(source_url: str) -> list[str]:
    """选项来源接口 URL 上的**业务**查询参数名(如 xxxtId)→ 提示这个源**带参数**(可能级联依赖别的字段)。
    过滤分页/缓存破坏/排序等噪声参(pageNo/pageSize/t…):它们不随上下文变,列出来会被误读成级联依赖。"""
    q = urlparse(str(source_url or "")).query
    names = [p.split("=", 1)[0] for p in q.split("&") if p and "=" in p]
    return [n for n in names if not _NOISE_PARAMS.match(n)]


class FieldRole(str, Enum):
    """字段角色(前端标注 / 导出说明用;每个字段恰好一个,按 _classify 的优先级判定)。"""
    USER_INPUT = "user_input"        # 用户/agent 直接填(文本/数值/日期)
    ENUM_STATIC = "enum_static"      # 固定下拉(DOM 抓的稳定枚举)→ 烤进 enum
    ENUM_LIVE = "enum_live"          # 活接口目录(选人/部门,会变)→ 运行期 --list-options
    LIST_SELECT = "list_select"      # 多选对象数组(参会人[])→ 传名字列表
    NAME_ID_PAIR = "name_id_pair"    # 名/ID 配对(显示名字段 + 兄弟 id 字段)
    ASSIGNEE = "assignee"            # 审批人节点(BPMN startUserSelectAssignees)
    STEP_CHAINED = "step_chained"    # 取自上一步接口响应(taskId/instanceId…)
    IDENTITY = "identity"            # 当前用户/会话值(运行期重取,不冻结)
    SYSTEM_VALUE = "system_value"    # 系统自动填(提交/创建时间戳 = now)
    CONSTANT = "constant"            # 流程模板常量(processDefKey/billType,原样提交)


class StepRole(str, Enum):
    """接口在剧本里的角色(多接口编排标注用)。"""
    OPTION_SOURCE = "option_source"      # GET 选项来源(字典/选人列表)→ 供某字段取值
    PREFETCH = "prefetch"                # 前置读(取流程句柄/上下文)
    WORKFLOW_SUBMIT = "workflow_submit"  # 发起/提交(流程起点)
    BUSINESS_WRITE = "business_write"    # 业务写(中间步)
    VERIFY_READBACK = "verify_readback"  # 提交后回查(事实核查)


def _clean_path(url: str) -> str:
    """URL → 干净 path(去协议+域名+query),供编排展示。"""
    u = str(url or "")
    if u.startswith("http"):
        pu = urlparse(u)
        return pu.path or "/"
    return (u.split("?")[0] or u) or "/"


def _is_assignee_path(path: str) -> bool:
    """字段路径是不是 BPMN 审批人(startUserSelectAssignees / approver / Activity_xxx 节点)。"""
    if not path:
        return False
    leaf = path.split(".")[-1].split("[")[0]
    return bool(_BPMN_NODE_RE.search(leaf) or _ASSIGNEE_CONTAINER_RE.search(path))


def _template_leaves(template) -> list[tuple]:
    """body_template 拍平成 [(点路径, is_param, 参数名 or 字面值)]。
    识别 "{{name}}" 叶子、_SEG 段拼接里的 {"$p":name}、_JSONSTR blob 内层(路径前缀延续)。"""
    out: list[tuple] = []

    def walk(node, path):
        if isinstance(node, dict):
            if set(node) == {_JSONSTR}:                       # JSON 字符串 blob:内层路径接着走
                walk(node[_JSONSTR], path)
                return
            if set(node) == {_SEG}:                           # 段拼接:常量 + {{参数}} 子串
                pname = next((it["$p"] for it in node[_SEG] if isinstance(it, dict) and "$p" in it), None)
                if pname is not None:
                    out.append((path, True, pname))
                else:
                    out.append((path, False, "".join(str(it) for it in node[_SEG])))
                return
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and node.startswith("{{") and node.endswith("}}"):
            out.append((path, True, node[2:-2]))
        else:
            out.append((path, False, node))

    walk(template, "")
    return out


def classify_field_role(*, is_param: bool, select: dict | None = None,
                        is_assignee: bool = False, is_identity: bool = False,
                        is_system: bool = False, is_link_target: bool = False) -> FieldRole:
    """单字段 → 角色(确定性,优先级从特殊到一般)。**通用,不挑系统/字段**。

    优先级:步链注入 > 身份 > 系统值 >(非参数即常量)> 审批人 > 多选 > 静态枚举 > 名/ID 配对 > 活接口枚举 > 用户填。
    """
    if is_link_target:
        return FieldRole.STEP_CHAINED
    if is_identity:
        return FieldRole.IDENTITY
    if is_system:
        return FieldRole.SYSTEM_VALUE
    if not is_param:
        return FieldRole.CONSTANT
    if is_assignee:
        return FieldRole.ASSIGNEE
    if select:
        if select.get("multi"):
            return FieldRole.LIST_SELECT
        if select.get("dom_options"):
            return FieldRole.ENUM_STATIC
        if select.get("id_path") or select.get("id_tokens"):
            return FieldRole.NAME_ID_PAIR
        if select.get("source_url"):
            return FieldRole.ENUM_LIVE
        if select.get("options"):                             # 有候选但无来源 → 当静态枚举
            return FieldRole.ENUM_STATIC
    return FieldRole.USER_INPUT


def _provenance(role: FieldRole, *, select: dict | None = None, identity: dict | None = None,
                system: dict | None = None, link: dict | None = None, value=None,
                const_src: dict | None = None) -> dict:
    """字段来源 + 用法(你要的"这个字段来源于哪里、怎么用")。确定性推导,推不出就诚实标 unknown。"""
    if role == FieldRole.STEP_CHAINED and link:
        src = link.get("source_step")
        return {"from": {"kind": "previous_step", "step": src, "ref": link.get("source_path")},
                "usage": f"运行期取自第 {(src or 0) + 1} 步响应的 `{link.get('source_path')}`(步间数据流,勿手填)"}
    if role == FieldRole.IDENTITY and identity:
        return {"from": {"kind": "session", "source": identity.get("source")},
                "usage": "运行期取当前登录用户/会话值回填(谁调用就是谁,不冻结录制者)"}
    if role == FieldRole.SYSTEM_VALUE and system:
        return {"from": {"kind": "system", "time": system.get("kind")},
                "usage": "运行期自动填当前时间(now),不焊死录制时刻"}
    if select and select.get("source_url") and role in (
            FieldRole.ENUM_LIVE, FieldRole.NAME_ID_PAIR, FieldRole.ASSIGNEE, FieldRole.LIST_SELECT):
        frm = {"kind": "interface", "interface": "GET " + _clean_path(select["source_url"]),
               "value_key": select.get("value_key"), "label_key": select.get("label_key")}
        if select.get("category_key"):
            frm["category"] = {select["category_key"]: select.get("category_value")}
        params = _source_params(select["source_url"])     # 来源接口的查询参数(t/xxxtId…)→ 显式标出,别藏
        if params:
            frm["params"] = params
        deps = select.get("source_param_deps") or {}       # Part B:级联参数追到具体字段(xxxtId=某字段值)
        if deps:
            frm["cascade"] = deps
        usage = (f"选前先 `--list-options` 实时拉当前可选项;运行期按名字查 `{select.get('value_key')}` 回填"
                 + ("(并同步写回配对 id 字段)" if select.get("id_path") else "")
                 + (f";来源接口带参数 {', '.join(params)}" if params else "")
                 + (f" —— **级联**:{('、'.join(f'{k}=字段「{v}」' for k, v in deps.items()))},运行期按所选上下文带参,勿照搬录制值"
                    if deps else ("(若选项随其它字段变化属级联,需按当前上下文带参)" if params else "")))
        return {"from": frm, "usage": usage}
    if select and role in (FieldRole.ENUM_STATIC, FieldRole.LIST_SELECT):
        return {"from": {"kind": "dom", "options": list(select.get("options") or [])},
                "usage": "从固定下拉选项里选(选项随 skill 走;名字提交,运行期查内部 ID)"}
    if role == FieldRole.CONSTANT:
        if const_src:                                     # Part B:常量已追到来源接口 → 标明真实出处(不再"未探知")
            return {"from": {"kind": "interface", "interface": const_src.get("interface"), "ref": const_src.get("ref")},
                    "usage": f"系统预设内部 ID,**来源已追到**:由 `{const_src.get('interface')}` 的 "
                             f"`{const_src.get('ref')}` 提供(运行期原样提交,用户无需填;跨环境复用时按该接口取最新值)"}
        if _is_opaque_id(value):                          # 不透明内部 ID 但没追到来源 → 诚实标"来源未探知"
            return {"from": {"kind": "system_preset"},
                    "usage": "系统预设的内部 ID(运行期原样提交,用户无需填);**来源未探知** —— 跨环境/跨租户复用前"
                             "请人工确认该值是否需替换(可能来自登录部门/上一页上下文/某前置接口)"}
        return {"from": {"kind": "constant"}, "usage": "流程模板常量(可读字面量),运行期原样提交,**勿改**"}
    return {"from": {"kind": "input"}, "usage": "用户/agent 直接填写"}


_STEP_ROLE_MAP = {
    "workflow_submit": StepRole.WORKFLOW_SUBMIT,
    "enum_options": StepRole.OPTION_SOURCE,
    "query": StepRole.PREFETCH,
    "auth": StepRole.PREFETCH,
    "business_write": StepRole.BUSINESS_WRITE,
    "destructive": StepRole.BUSINESS_WRITE,
}


def _step_role(step: dict) -> StepRole:
    """写步骤角色:由 classify_request_role 的语义分类(按方法 + 路径段,确定性)映射而来。"""
    return _STEP_ROLE_MAP.get(classify_request_role(step).get("semanticRole"), StepRole.BUSINESS_WRITE)


def _step_fields(step: dict, step_index: int) -> list[dict]:
    """单步 → 字段注释列表(角色 + 来源 + 用法 + 选项)。枚举 body_template 的全部叶子(参数 + 常量),
    再叠加 identity/system_values/links 的路径语义。"""
    selects_by_param = {s.get("param"): s for s in (step.get("selects") or []) if s.get("param")}
    identity_by_path = {i.get("path"): i for i in (step.get("identity") or [])}
    system_by_path = {s.get("path"): s for s in (step.get("system_values") or [])}
    link_by_path = {lk.get("target_path"): lk for lk in (step.get("links") or [])}
    const_src_by_path = {c.get("path"): c for c in (step.get("constant_sources") or [])}   # Part B:常量已追到的来源
    types = step.get("field_types") or {}

    fields: list[dict] = []
    for path, is_param, payload in _template_leaves(step.get("body_template")):
        name = payload if is_param else path
        select = selects_by_param.get(name) if is_param else None
        idn = identity_by_path.get(path)
        sysv = system_by_path.get(path)
        link = link_by_path.get(path)
        role = classify_field_role(
            is_param=is_param, select=select,
            is_assignee=is_param and _is_assignee_path(path),
            is_identity=idn is not None, is_system=sysv is not None,
            is_link_target=link is not None)
        prov = _provenance(role, select=select, identity=idn, system=sysv, link=link,
                           value=(payload if not is_param else None),   # 常量传值 → 判是否不透明内部 ID
                           const_src=const_src_by_path.get(path))        # 常量已追到来源接口 → 标真实出处
        entry = {"step": step_index, "name": name, "path": path, "role": role.value,
                 "type": types.get(name, "string" if is_param else "const"),
                 "provenance": prov}
        if select and select.get("options"):
            entry["options"] = list(select["options"])
        if select and select.get("columns"):            # DOM 明细子表列结构 → 随字段进剧本(导出渲染每行列说明)
            entry["columns"] = list(select["columns"])
        fields.append(entry)
    return fields


def build_screenplay(api_request: dict, reads: list[dict] | None = None) -> dict:
    """api_request(单请求 或 {steps:[...]} 工作流)→ 完整剧本(确定性派生,零副作用)。

    返回 {multi_step, write_steps:[{index,role,method,path}], option_sources:[{interface,for_fields}],
          verify:bool, fields:[{step,name,path,role,type,provenance,options?}], data_flow:[{...}]}。
    供:前端按 role 渲染徽章 + provenance 提示;导出按"业务说明→接口编排→字段表→SOP→数据流"剧本化。
    """
    apir = api_request or {}
    steps = apir.get("steps") or [apir]
    multi = bool(apir.get("steps"))

    write_steps: list[dict] = []
    fields: list[dict] = []
    data_flow: list[dict] = []
    option_sources: dict[str, list[str]] = {}
    prefetch_sources: dict[str, list[str]] = {}        # Part B:提供常量(ssbmId/bmId)的前置接口

    for idx, st in enumerate(steps):
        write_steps.append({"index": idx, "role": _step_role(st).value,
                            "method": (st.get("method") or "POST").upper(),
                            "path": _clean_path(st.get("path") or st.get("url") or "")})
        for fld in _step_fields(st, idx):
            fields.append(fld)
            frm = fld["provenance"]["from"]
            if frm.get("kind") == "interface":
                bucket = prefetch_sources if fld["role"] == FieldRole.CONSTANT.value else option_sources
                bucket.setdefault(frm["interface"], []).append(fld["name"])
        for lk in (st.get("links") or []):
            data_flow.append({"to_step": idx, "to": lk.get("target_path"),
                              "from_step": lk.get("source_step"), "from": lk.get("source_path")})

    verify = bool(apir.get("fact_check") or (steps and steps[-1].get("fact_check")))
    return {
        "multi_step": multi,
        "write_steps": write_steps,
        "option_sources": [{"interface": k, "for_fields": v} for k, v in option_sources.items()],
        "prefetch_sources": [{"interface": k, "provides": v} for k, v in prefetch_sources.items()],
        "verify": verify,
        "fields": fields,
        "data_flow": data_flow,
    }


def screenplay_skeleton(sp: dict) -> dict:
    """从剧本里抽**纯结构骨架**(只留 字段名/角色/来源接口路径/编排/数据流),**剔除一切值与凭证**——
    安全喂 LLM 润色业务描述用(红线:LLM 只见结构,绝不见具体值/人名/ID/选项快照/凭证)。"""
    def _f(f: dict) -> dict:
        frm = (f.get("provenance") or {}).get("from") or {}
        out = {"name": f.get("name"), "role": f.get("role")}
        if frm.get("kind"):
            out["source"] = frm["kind"]
        if frm.get("interface"):
            out["interface"] = frm["interface"]      # 接口路径(非值)
        if frm.get("ref"):
            out["from_ref"] = frm["ref"]              # 取值字段路径(非值)
        if f.get("columns"):                          # 明细子表列名(纯结构标签,无值)→ 助 LLM 准确描述子表
            out["columns"] = [{"name": c.get("name"), "required": bool(c.get("required"))}
                              for c in f["columns"] if c.get("name")]
        return out
    return {
        "multi_step": sp.get("multi_step"),
        "write_steps": [{"role": s.get("role"), "method": s.get("method"), "path": s.get("path")}
                        for s in sp.get("write_steps") or []],
        "option_sources": sp.get("option_sources") or [],     # {interface, for_fields} 全是名字/路径,无值
        "data_flow": sp.get("data_flow") or [],               # 路径,无值
        "fields": [_f(f) for f in sp.get("fields") or []],     # 不含 options/样例值
    }


# ── 业务说明:固定模板的**确定性**渲染(同剧本每次字节级一致;空段省略,绝不臘造)──
_FIELD_ROLE_CN = {
    "user_input": "用户填", "enum_static": "固定枚举", "enum_live": "活接口·实时拉",
    "list_select": "多选/明细·名字列表", "name_id_pair": "名/ID配对", "assignee": "审批人·活接口",
    "step_chained": "上一步带出", "identity": "当前用户", "system_value": "系统自动填", "constant": "固定常量",
}
_STEP_ROLE_CN = {
    "option_source": "选项来源(读)", "prefetch": "前置读", "workflow_submit": "发起/提交",
    "business_write": "业务写", "verify_readback": "提交后回查",
}
_USER_ROLES = {"user_input", "enum_static", "enum_live", "list_select", "name_id_pair", "assignee"}


def _desc_field_line(f: dict, required: set) -> str:
    """字段一行:名(必填/选填)· [角色] · 来源用法(复用 provenance.usage,已 grounded)· 枚举选项/子表列。"""
    name = f.get("name") or f.get("path") or ""
    tag = (("(必填)" if name in required else "(选填)") if required else "")
    role_cn = _FIELD_ROLE_CN.get(f.get("role"), f.get("role") or "")
    usage = ((f.get("provenance") or {}).get("usage") or "").strip()
    extra = ""
    cols, opts = f.get("columns"), f.get("options")
    if cols:
        cn = "、".join(f"{c.get('name')}{'(必填)' if c.get('required') else ''}" for c in cols if c.get("name"))
        if cn:
            extra = f";每行含:{cn}"
    elif opts:
        shown = "/".join(str(o) for o in list(opts)[:8])
        extra = f";可选:{shown}" + (f"…(共{len(opts)}项)" if len(opts) > 8 else "")
    return f"  - `{name}`{tag}:[{role_cn}] {usage}{extra}".rstrip()


def render_business_description(screenplay: dict, purpose: str = "", required=None) -> str:
    """剧本 → **固定模板**业务说明(确定性,同剧本每次一致)。结构全来自 grounded 的 screenplay,
    **零业务/字段/系统字面量**;有什么段渲染什么,没有的省略,绝不臘造。LLM/手写**只动【业务目的】一行**。

    通用:不同业务(单写/多步/带子表/读多写少)各自按抓到的事实自适应渲染,不是某类业务的写死模板。
    """
    sp = screenplay or {}
    req = set(required or [])
    L: list[str] = ["【业务目的】" + ((purpose or "").strip() or "(待补充:一句话说明本操作办什么业务)")]

    ws = sp.get("write_steps") or []
    if ws:
        L += ["", f"【办理流程】(服务端按序执行,共 {len(ws)} 步写操作)"]
        for s in ws:
            L.append(f"  {(s.get('index') or 0) + 1}. `{(s.get('method') or 'POST')} {s.get('path') or ''}`"
                     f" —— {_STEP_ROLE_CN.get(s.get('role'), s.get('role'))}")

    pf, osrc = sp.get("prefetch_sources") or [], sp.get("option_sources") or []
    if pf or osrc:
        L += ["", "【前置/数据来源接口】(下列字段的值/选项实时取自这些接口)"]
        for o in pf:
            L.append(f"  - `{o.get('interface')}` → 提供:{'、'.join(o.get('provides') or [])}(系统预设,运行期取)")
        for o in osrc:
            L.append(f"  - `{o.get('interface')}` → 供字段:{'、'.join(o.get('for_fields') or [])}(选前先 --list-options)")

    fields = sp.get("fields") or []
    user_like = [f for f in fields if f.get("role") in _USER_ROLES]
    auto_like = [f for f in fields if f.get("role") not in _USER_ROLES]
    if user_like:
        L += ["", "【需填写/选择的字段】(调用方提供;选择型传名字,运行期查内部 ID)"]
        L += [_desc_field_line(f, req) for f in user_like]
    if auto_like:
        L += ["", "【自动填充 / 无需提供的字段】(运行期注入,调用方勿填)"]
        L += [_desc_field_line(f, req) for f in auto_like]

    df = sp.get("data_flow") or []
    if df:
        L += ["", "【步间数据流】(上一步响应自动注入下一步,勿手填)"]
        for d in df:
            L.append(f"  - 第{(d.get('from_step') or 0) + 1}步响应 `{d.get('from')}` → "
                     f"第{(d.get('to_step') or 0) + 1}步 `{d.get('to')}`")

    tail = []
    if sp.get("verify"):
        tail.append("提交后回查记录确认真生效(按业务返回码判成败,非 HTTP 字面)")
    if sp.get("multi_step"):
        tail.append("多步写:按上面办理流程顺序逐步执行,中途失败不重复提交")
    if tail:
        L += ["", "【后续 / 校验】"] + ["  - " + t for t in tail]

    return "\n".join(L).rstrip() + "\n"
