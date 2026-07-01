"""统一表单规格(form spec)—— 三层择优把**无障碍树(a11y)+ DOM 快照**合成权威字段表。

设计动机(实测得出):
  · 无障碍树(CDP `Accessibility.getFullAXTree`)给浏览器**按 W3C 算好的可访问名 + 角色 + aria 必填**,
    天然跳过单位后缀(/μL)、把单选组建成**一个** radiogroup —— 这些手搓 DOM 抓不对/抓不到。
  · 但**无 label 关联**的字段 a11y 名为空、或只拿到占位符(请选择…)→ 需 DOM 兜底。
故采**a11y 主 + DOM 兜底**的混合,每字段带 confidence/source;低置信留给人工确认,绝不静默瞎填。
**纯函数、零副作用、不挑系统/框架**:输入两份捕获(已按 backendDOMNodeId 对齐),输出统一字段表。
"""
from __future__ import annotations

import re as _re

# 占位符(非真名):a11y 在无 label 关联时常退回占位符文本 → 识别出来,改用 DOM 标签兜底。
_PLACEHOLDER_RE = _re.compile(
    r"^(请输入|请选择|请填写|请录入|请上传|如\s|例如[:：]?|placeholder|select\b|choose\b|enter\s|type\s)", _re.I)
# 纯单位/符号(/μL、ng、%):绝不当字段名。
_UNIT_RE = _re.compile(r"^[\/\\\s]*([µμ]?[a-zA-Z]{1,3}|%|‰)[\/\\\s]*$")
# HTML type → a11y role 兜底(DOM-only 字段无 a11y role 时)。
_ROLE_FROM_TYPE = {
    "text": "textbox", "textarea": "textbox", "search": "searchbox", "tel": "textbox", "email": "textbox",
    "url": "textbox", "password": "textbox", "number": "spinbutton", "date": "textbox", "datetime": "textbox",
    "time": "textbox", "select": "combobox", "file": "textbox", "checkbox": "checkbox", "radio": "radiogroup",
}


# 无障碍树里"可填控件"的角色(radiogroup/checkbox = 整组一个字段)。
_AX_CONTROL_ROLES = {"textbox", "combobox", "spinbutton", "searchbox", "checkbox", "radiogroup",
                     "switch", "listbox", "slider", "menu", "menuitemcheckbox", "menuitemradio"}


def form_ax_to_snapshot(form_ax: list[dict]) -> list[dict]:
    """权威字段表(build_form_spec)→ `form_snapshot` 形态({name,label,type,required,value}),喂现有 `bind_form_fields`。
    name=控件 name(绑定用),label=**权威字段名**,required/type/value 取权威值。低置信/无名字段照出(按值兜底绑)。"""
    out: list[dict] = []
    for f in form_ax or []:
        if not isinstance(f, dict):
            continue
        out.append({
            "name": f.get("dom_name") or "",
            "label": f.get("name") or "",
            "type": f.get("type") or "text",
            "required": bool(f.get("required")),
            "value": f.get("value") or "",
            "confidence": f.get("confidence"),      # high/medium/low → 低置信字段前端标"建议确认"
        })
    return out


async def capture_form_ax(page, cdp) -> list[dict]:  # noqa: ANN001 —— page/cdp 是 playwright 句柄,避免硬依赖
    """提交瞬间抓**无障碍树(CDP)+ DOM 字段**,按 `backendDOMNodeId↔data-danofid` 对齐 → `build_form_spec` 合成权威字段表。

    a11y 给浏览器算好的可访问名/角色/必填(治单位当名、单选组、aria 必填);DOM(`__danoFormDom`,标了 fid)兜底
    无 label 关联的字段。a11y 引用了但没标 fid 的节点(如 radiogroup 容器)= a11y-only 字段。纯函数式,失败回 []。
    """
    # DOM 字段:遍历**所有 frame**(企业 OA 常把表单嵌在 iframe 里;只取主 frame 会整张漏)。fid 按 frame 加前缀防撞。
    dom_fields: list[dict] = []
    try:
        frames = list(getattr(page, "frames", None) or [page])
    except Exception:  # noqa: BLE001
        frames = [page]
    for fi, fr in enumerate(frames):
        try:
            part = await fr.evaluate("(pfx) => (window.__danoFormDom ? window.__danoFormDom(pfx) : [])", f"{fi}_")
            for f in part or []:
                dom_fields.append(f)
        except Exception:  # noqa: BLE001 —— 个别 frame 跨域/已卸载 → 跳过,不影响其它
            continue
    dom_by_fid = {str(f.get("fid")): f for f in dom_fields if f.get("fid") is not None}

    try:
        await cdp.send("Accessibility.enable")
        tree = await cdp.send("Accessibility.getFullAXTree")
        # 一次 getDocument(pierce 穿透 iframe/shadow)建 backendNodeId→data-danofid 表(取代每控件一次 getAttributes,快且稳)
        doc = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    except Exception:  # noqa: BLE001
        return build_form_spec([{"ax": None, "dom": d} for d in dom_by_fid.values()]) if dom_by_fid else []

    fid_by_backend: dict = {}

    def _walk(node):
        attrs = node.get("attributes") or []
        ad = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
        if "data-danofid" in ad and node.get("backendNodeId") is not None:
            fid_by_backend[node["backendNodeId"]] = ad["data-danofid"]
        for c in node.get("children") or []:
            _walk(c)
        if node.get("contentDocument"):                          # iframe 内文档
            _walk(node["contentDocument"])
        for sr in node.get("shadowRoots") or []:                 # shadow DOM
            _walk(sr)
    try:
        _walk(doc["root"])
    except Exception:  # noqa: BLE001
        pass

    ax_nodes = []
    for n in tree.get("nodes", []):
        role = (n.get("role") or {}).get("value")
        if role not in _AX_CONTROL_ROLES or n.get("ignored"):
            continue
        name = (n.get("name") or {}).get("value")
        props = {p.get("name"): (p.get("value") or {}).get("value") for p in (n.get("properties") or [])}
        ax_nodes.append({"backend": n.get("backendDOMNodeId"), "name": name, "role": role,
                         "required": bool(props.get("required"))})

    dom_labels = {(d.get("label") or "").strip() for d in dom_by_fid.values() if (d.get("label") or "").strip()}
    records: list[dict] = []
    used: set = set()
    for ax in ax_nodes:
        fid = fid_by_backend.get(ax["backend"])
        dom = dom_by_fid.get(fid) if fid is not None else None
        # a11y-only 节点(没对齐到 DOM 控件)若已有**同名 DOM 字段**(典型:单选组 a11y radiogroup ↔ DOM 单选组)→ 跳过去重
        if dom is None and (ax.get("name") or "").strip() in dom_labels:
            continue
        if dom is not None:
            used.add(fid)
        records.append({"ax": {"name": ax["name"], "role": ax["role"], "required": ax["required"]}, "dom": dom})
    for fid, dom in dom_by_fid.items():                          # a11y 没覆盖到的 DOM 控件 → 补进来,绝不漏
        if fid not in used:
            records.append({"ax": None, "dom": dom})
    return build_form_spec(records)


def _bad_name(s: str) -> bool:
    s = (s or "").strip()
    return (not s) or bool(_PLACEHOLDER_RE.match(s)) or bool(_UNIT_RE.match(s))


def build_form_spec(records: list[dict]) -> list[dict]:
    """records:[{ax:{name,role,required}|None, dom:{label,required,value,type,name}|None}](已对齐的同一控件两份信号)
       → 统一字段 [{name, role, required, type, value, dom_name, confidence, source}]。

    名字三层择优:**a11y 名(非占位符/单位)> DOM 标签(非占位符/单位)> 占位符(低置信)**。
    必填 = a11y(aria-required)∪ DOM(视觉 `*`/is-required)—— 视觉星号 a11y 抓不到,必须并上 DOM。
    角色由 a11y 权威定(textbox/combobox/radiogroup/checkbox/spinbutton),无 a11y 时按 HTML type 兜底。
    """
    out: list[dict] = []
    for r in records:
        ax = r.get("ax") or {}
        dom = r.get("dom") or {}
        ax_name = (ax.get("name") or "").strip()
        dom_label = (dom.get("label") or "").strip()
        if not _bad_name(ax_name):
            name, source, confidence = ax_name, "a11y", "high"
        elif not _bad_name(dom_label):
            name, source, confidence = dom_label, "dom", ("high" if ax_name or ax.get("role") else "medium")
        elif ax_name and not _UNIT_RE.match(ax_name):    # 占位符还能当低置信提示;**纯单位**(/μL)是垃圾,丢
            name, source, confidence = ax_name, "placeholder", "low"
        else:
            name, source, confidence = "", "none", "low"
        role = ax.get("role") or _ROLE_FROM_TYPE.get(str(dom.get("type") or "").lower(), "textbox")
        required = bool(ax.get("required")) or bool(dom.get("required"))
        out.append({
            "name": name, "role": role, "required": required,
            "type": dom.get("type"), "value": dom.get("value"),
            "dom_name": dom.get("name"),                 # body 绑定用的控件 name(若有)
            "confidence": confidence, "source": source,
        })
    return out
